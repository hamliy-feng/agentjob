from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DB_FILE = DATA_DIR / "job_agent.sqlite3"
TZ = ZoneInfo("Asia/Taipei")
DATA_DIR.mkdir(parents=True, exist_ok=True)

STAGES = ["L0", "L1", "L2", "L3", "L4", "L5", "L6"]


def now_iso() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    c = sqlite3.connect(DB_FILE)
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS jobs(
          job_id TEXT PRIMARY KEY,
          source TEXT NOT NULL,
          source_url TEXT,
          detail_url TEXT,
          title TEXT,
          company TEXT,
          company_key TEXT,
          location TEXT,
          salary TEXT,
          work_days_per_week REAL,
          internship_months REAL,
          experience TEXT,
          education TEXT,
          tags_json TEXT NOT NULL DEFAULT '[]',
          description TEXT,
          recruiter_name TEXT,
          recruiter_title TEXT,
          address TEXT,
          l0_detail_status TEXT NOT NULL,
          first_seen TEXT NOT NULL,
          last_seen TEXT NOT NULL,
          source_evidence_json TEXT NOT NULL,
          raw_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS stage_runs(
          job_id TEXT NOT NULL,
          stage TEXT NOT NULL,
          status TEXT NOT NULL,
          agent_id TEXT,
          version TEXT NOT NULL,
          score REAL,
          summary TEXT,
          input_hash TEXT,
          output_json TEXT NOT NULL DEFAULT '{}',
          error TEXT,
          started_at TEXT,
          finished_at TEXT,
          updated_at TEXT NOT NULL,
          PRIMARY KEY(job_id, stage)
        );
        CREATE TABLE IF NOT EXISTS source_health(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          source TEXT NOT NULL,
          status TEXT NOT NULL,
          url TEXT,
          detail TEXT,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS company_reports(
          company_key TEXT PRIMARY KEY,
          company_name TEXT NOT NULL,
          report_status TEXT NOT NULL,
          company_score REAL,
          risk_level TEXT,
          report_json TEXT NOT NULL,
          researched_at TEXT NOT NULL,
          stale_after TEXT
        );
        CREATE TABLE IF NOT EXISTS company_sources(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          company_key TEXT NOT NULL,
          source_type TEXT NOT NULL,
          source_name TEXT,
          url TEXT,
          published_at TEXT,
          retrieved_at TEXT NOT NULL,
          confidence TEXT NOT NULL,
          claims_json TEXT NOT NULL,
          note TEXT
        );
        CREATE TABLE IF NOT EXISTS materials(
          job_id TEXT PRIMARY KEY,
          company_report_key TEXT,
          communication_advice TEXT,
          greeting TEXT,
          resume_focus_json TEXT NOT NULL DEFAULT '[]',
          material_json TEXT NOT NULL,
          generated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS application_gates(
          job_id TEXT PRIMARY KEY,
          gate_status TEXT NOT NULL,
          confirmed INTEGER NOT NULL DEFAULT 0,
          executed INTEGER NOT NULL DEFAULT 0,
          confirmation_note TEXT,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS job_ui_state(
          job_id TEXT PRIMARY KEY,
          state TEXT NOT NULL DEFAULT 'active',
          favorite INTEGER NOT NULL DEFAULT 0,
          state_content_hash TEXT,
          note TEXT,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS application_history(
          job_id TEXT NOT NULL,
          content_hash TEXT NOT NULL,
          status TEXT NOT NULL,
          verified_at TEXT,
          note TEXT,
          PRIMARY KEY(job_id, content_hash)
        );
        """
    )
    # 轻量 schema migration：老库原地升级，不要求用户删库。
    def _ensure_col(table: str, column: str, ddl: str) -> None:
        cols = {r[1] for r in c.execute(f"pragma table_info({table})")}
        if column not in cols:
            c.execute(f"alter table {table} add column {ddl}")
    _ensure_col("jobs", "content_hash", "content_hash TEXT")
    _ensure_col("jobs", "previous_content_hash", "previous_content_hash TEXT")
    _ensure_col("job_ui_state", "favorite", "favorite INTEGER NOT NULL DEFAULT 0")
    _ensure_col("job_ui_state", "state_content_hash", "state_content_hash TEXT")
    _ensure_col("application_gates", "content_hash", "content_hash TEXT")
    _ensure_col("application_gates", "verified_at", "verified_at TEXT")
    c.commit()
    return c


def company_key(name: str) -> str:
    norm = "".join((name or "").strip().lower().split())
    return hashlib.sha256(norm.encode("utf-8", "ignore")).hexdigest()[:20] if norm else "unknown"


def job_id_for(job: dict) -> str:
    key = (job.get("detail_url") or "").strip()
    if not key:
        key = "|".join(str(job.get(k, "")).strip().lower() for k in ["title", "company", "location", "salary"])
    return hashlib.sha256(key.encode("utf-8", "ignore")).hexdigest()[:20]


def content_hash_for(job: dict) -> str:
    # 只对真正影响岗位内容的字段建版本hash；招聘人活跃状态/页面更新时间变化不算“岗位上新”。
    stable = {
        "title": job.get("title") or "",
        "company": job.get("company") or "",
        "salary": job.get("salary") or "",
        "location": job.get("location") or "",
        "description": job.get("description") or "",
        "requirements": job.get("requirements") or "",
        "responsibilities": job.get("responsibilities") or "",
        "work_days_per_week": job.get("work_days_per_week"),
        "internship_months": job.get("internship_months"),
    }
    raw = json.dumps(stable, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()[:40]


def upsert_job(job: dict) -> str:
    jid = job.get("job_id") or job_id_for(job)
    ts = now_iso()
    c = connect()
    existed = c.execute("select * from jobs where job_id=?", (jid,)).fetchone()
    first_seen = existed["first_seen"] if existed else ts
    incoming_status = job.get("l0_detail_status") or "captured_card"
    incoming_hash = content_hash_for(job) if incoming_status == "detail_complete" else (existed["content_hash"] if existed else None)
    previous_hash = existed["content_hash"] if existed else None
    version_changed = bool(existed and incoming_status == "detail_complete" and previous_hash and incoming_hash and previous_hash != incoming_hash)
    # 已有完整BOSS详情时，后续一次半加载/空白/安全页不得降级覆盖历史可信事实。
    if existed and existed["l0_detail_status"] == "detail_complete" and incoming_status != "detail_complete":
        c.execute("update jobs set last_seen=? where job_id=?", (ts, jid))
        mark_stage(c, jid, "L0", "complete", version="2.1", summary=f"保留既有detail_complete；本次抓取={incoming_status}", output={"detail_status":"detail_complete","refresh_attempt":incoming_status,"preserved":True})
        c.commit(); c.close(); return jid
    tags = job.get("tags") or []
    if isinstance(tags, str):
        tags = [x.strip() for x in tags.replace("、", ",").split(",") if x.strip()]
    ck = job.get("company_key") or company_key(job.get("company", ""))
    vals = (
        jid, job.get("source") or "unknown", job.get("source_url") or "", job.get("detail_url") or "",
        job.get("title") or "", job.get("company") or "", ck, job.get("location") or "", job.get("salary") or "",
        job.get("work_days_per_week"), job.get("internship_months"), job.get("experience") or "", job.get("education") or "",
        json.dumps(tags, ensure_ascii=False), job.get("description") or "", job.get("recruiter_name") or "",
        job.get("recruiter_title") or "", job.get("address") or "", job.get("l0_detail_status") or "captured_card",
        first_seen, ts, json.dumps(job.get("source_evidence") or {}, ensure_ascii=False), json.dumps(job, ensure_ascii=False),
        incoming_hash, previous_hash if version_changed else (existed["previous_content_hash"] if existed else None)
    )
    c.execute(
        """INSERT INTO jobs(job_id,source,source_url,detail_url,title,company,company_key,location,salary,work_days_per_week,internship_months,experience,education,tags_json,description,recruiter_name,recruiter_title,address,l0_detail_status,first_seen,last_seen,source_evidence_json,raw_json,content_hash,previous_content_hash)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(job_id) DO UPDATE SET source=excluded.source,source_url=excluded.source_url,detail_url=excluded.detail_url,title=excluded.title,company=excluded.company,company_key=excluded.company_key,location=excluded.location,salary=excluded.salary,work_days_per_week=excluded.work_days_per_week,internship_months=excluded.internship_months,experience=excluded.experience,education=excluded.education,tags_json=excluded.tags_json,description=excluded.description,recruiter_name=excluded.recruiter_name,recruiter_title=excluded.recruiter_title,address=excluded.address,l0_detail_status=excluded.l0_detail_status,last_seen=excluded.last_seen,source_evidence_json=excluded.source_evidence_json,raw_json=excluded.raw_json,content_hash=excluded.content_hash,previous_content_hash=excluded.previous_content_hash""",
        vals,
    )
    if version_changed:
        # 同一BOSS job_id 的正文真正发生变化，视作“岗位上新”。旧跳过/已投递只针对旧版本。
        c.execute("delete from stage_runs where job_id=? and stage in ('L1','L2','L3','L4','L5','L6')", (jid,))
        c.execute("delete from materials where job_id=?", (jid,))
        c.execute(
            """insert into job_ui_state(job_id,state,favorite,state_content_hash,note,updated_at)
               values(?, 'active', coalesce((select favorite from job_ui_state where job_id=?),0), ?, 'JD内容已更新，重新进入候选池', ?)
               on conflict(job_id) do update set state='active',state_content_hash=excluded.state_content_hash,note=excluded.note,updated_at=excluded.updated_at""",
            (jid, jid, incoming_hash, ts),
        )
        c.execute(
            """update application_gates set confirmed=0,executed=0,gate_status='not_ready',confirmation_note='JD内容已更新，需重新评估',content_hash=?,verified_at=null,updated_at=? where job_id=?""",
            (incoming_hash, ts, jid),
        )
    status = incoming_status
    stage_status = "complete" if status == "detail_complete" else "needs_detail" if status in {"captured_card", "blocked_security", "needs_login", "external_enriched", "external_enriched_needs_boss", "needs_detail"} else status
    mark_stage(c, jid, "L0", stage_status, version="2.0", summary=f"L0 detail status={status}", output={"detail_status": status})
    c.commit(); c.close()
    return jid


def mark_stage(c: sqlite3.Connection, job_id: str, stage: str, status: str, *, version: str = "2.0", agent_id: str | None = None, score: float | None = None, summary: str | None = None, output: dict | None = None, error: str | None = None) -> None:
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage}")
    ts = now_iso()
    out = output or {}
    raw = json.dumps(out, ensure_ascii=False, sort_keys=True)
    input_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    finished = ts if status in {"complete", "pass", "reject", "blocked", "needs_ai", "needs_detail", "ready", "skipped"} else None
    c.execute(
        """INSERT INTO stage_runs(job_id,stage,status,agent_id,version,score,summary,input_hash,output_json,error,started_at,finished_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(job_id,stage) DO UPDATE SET status=excluded.status,agent_id=excluded.agent_id,version=excluded.version,score=excluded.score,summary=excluded.summary,input_hash=excluded.input_hash,output_json=excluded.output_json,error=excluded.error,finished_at=excluded.finished_at,updated_at=excluded.updated_at""",
        (job_id, stage, status, agent_id, version, score, summary, input_hash, raw, error, ts, finished, ts)
    )


def record_source_health(source: str, status: str, url: str = "", detail: str = "") -> None:
    c = connect(); c.execute("insert into source_health(source,status,url,detail,created_at) values(?,?,?,?,?)", (source,status,url,detail,now_iso())); c.commit(); c.close()


def status_snapshot() -> dict:
    c = connect()
    jobs = c.execute("select count(*) from jobs").fetchone()[0]
    stages = {r[0]: r[1] for r in c.execute("select stage||':'||status,count(*) from stage_runs group by stage,status order by stage,status")}
    health = [dict(r) for r in c.execute("select * from source_health order by id desc limit 20")]
    c.close()
    return {"jobs": jobs, "stage_status": stages, "source_health_recent": health}
