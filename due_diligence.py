from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from config_loader import load_system
from workflow_db import connect, mark_stage, now_iso

TZ = ZoneInfo("Asia/Taipei")
ROOT = Path(__file__).resolve().parent
RESEARCH_DIR = ROOT / "data" / "research"
RESEARCH_DIR.mkdir(parents=True, exist_ok=True)


def build_request(job_id: str) -> dict:
    c = connect()
    job = c.execute("select * from jobs where job_id=?", (job_id,)).fetchone()
    if not job:
        c.close(); raise KeyError(job_id)
    l1 = c.execute("select * from stage_runs where job_id=? and stage='L1'", (job_id,)).fetchone()
    l2 = c.execute("select * from stage_runs where job_id=? and stage='L2'", (job_id,)).fetchone()
    if job["l0_detail_status"] != "detail_complete":
        c.close(); raise RuntimeError(f"L3前置门：L0不是BOSS完整JD（{job['l0_detail_status']}）")
    if not l1 or l1["status"] != "pass":
        c.close(); raise RuntimeError("L3前置门：L1未通过")
    if not l2 or l2["status"] != "pass":
        c.close(); raise RuntimeError("L3前置门：L2未通过")
    ck = job["company_key"]
    cached = c.execute("select * from company_reports where company_key=?", (ck,)).fetchone()
    raw_job = json.loads(job["raw_json"] or "{}")
    l1_out = json.loads(l1["output_json"] or "{}")
    l2_out = json.loads(l2["output_json"] or "{}")
    c.close()
    system = load_system()
    boss_facts = {
        "title": raw_job.get("title") or job["title"],
        "company": raw_job.get("company") or job["company"],
        "salary": raw_job.get("salary") or job["salary"],
        "location": raw_job.get("location") or job["location"],
        "experience": raw_job.get("experience") or job["experience"],
        "education": raw_job.get("education") or job["education"],
        "work_days_per_week": raw_job.get("work_days_per_week"),
        "internship_months": raw_job.get("internship_months"),
        "employment_type": raw_job.get("employment_type"),
        "tags": raw_job.get("tags") or [],
        "responsibilities": raw_job.get("responsibilities") or "",
        "requirements": raw_job.get("requirements") or "",
        "description": raw_job.get("description") or job["description"],
        "recruiter_name": raw_job.get("recruiter_name") or job["recruiter_name"],
        "recruiter_title": raw_job.get("recruiter_title") or job["recruiter_title"],
        "recruiter_activity": raw_job.get("recruiter_activity") or "",
        "address": raw_job.get("address") or job["address"],
        "benefits": raw_job.get("benefits") or [],
        "company_financing": raw_job.get("company_financing") or "",
        "company_size": raw_job.get("company_size") or "",
        "company_industry": raw_job.get("company_industry") or "",
        "company_intro": raw_job.get("company_intro") or "",
        "business_info": raw_job.get("business_info") or {},
        "page_updated_at": raw_job.get("page_updated_at") or "",
        "detail_url": raw_job.get("detail_url") or job["detail_url"],
        "source": job["source"],
        "source_evidence": json.loads(job["source_evidence_json"] or "{}")
    }
    return {
        "schema_version": "2.1",
        "job_id": job_id,
        "company_key": ck,
        "company_name": job["company"],
        "boss_primary_facts": boss_facts,
        "l1": l1_out,
        "l2": l2_out,
        "cached_company_report": json.loads(cached["report_json"]) if cached else None,
        "required_dimensions": system["due_diligence"]["required_dimensions"],
        "research_rules": {
            "boss_is_primary_job_source": True,
            "external_sources_are_due_diligence_or_enrichment_only": True,
            "prefer_primary_sources": True,
            "every_nontrivial_claim_needs_source": True,
            "anonymous_discussion_is_signal_only": True,
            "preserve_conflicts": True,
            "include_published_date": True,
            "include_retrieved_at": True
        },
        "expected_output": {
            "company_summary": "string",
            "business_products": "object",
            "funding_investors": "object",
            "operating_and_legal_risk": "object",
            "company_size_team": "object",
            "benefits": "object, include 下午茶/零食/五险一金/年终奖/假期/弹性/加班等，并区分BOSS声称与外部验证",
            "work_culture": "object",
            "public_discussion": "object",
            "layoff_business_risk": "object",
            "job_posting_pattern": "object",
            "salary_competitiveness": "object",
            "job_specific_notes": "object",
            "communication_advice": "array",
            "company_score": "0-100",
            "risk_level": "low|medium|high|unknown",
            "sources": "array of source objects"
        }
    }


def save_request(job_id: str) -> Path:
    req = build_request(job_id)
    path = RESEARCH_DIR / f"request_{job_id}.json"
    path.write_text(json.dumps(req, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def ingest_report(payload: dict, *, agent_id: str = "research_agent") -> dict:
    job_id = payload["job_id"]
    company_key = payload["company_key"]
    company_name = payload["company_name"]
    report = payload.get("report") or payload
    sources = report.get("sources") or []
    score = report.get("company_score")
    risk = report.get("risk_level") or "unknown"
    c = connect()
    stale_days = int(load_system()["due_diligence"].get("stale_after_days", 180))
    stale_after = (datetime.now(TZ) + timedelta(days=stale_days)).isoformat(timespec="seconds")
    c.execute(
        """insert into company_reports(company_key,company_name,report_status,company_score,risk_level,report_json,researched_at,stale_after)
           values(?,?,?,?,?,?,?,?) on conflict(company_key) do update set company_name=excluded.company_name,report_status=excluded.report_status,company_score=excluded.company_score,risk_level=excluded.risk_level,report_json=excluded.report_json,researched_at=excluded.researched_at,stale_after=excluded.stale_after""",
        (company_key, company_name, "complete", score, risk, json.dumps(report, ensure_ascii=False), now_iso(), stale_after)
    )
    c.execute("delete from company_sources where company_key=?", (company_key,))
    for s in sources:
        c.execute(
            """insert into company_sources(company_key,source_type,source_name,url,published_at,retrieved_at,confidence,claims_json,note)
               values(?,?,?,?,?,?,?,?,?)""",
            (company_key, s.get("source_type") or "unknown", s.get("source_name") or "", s.get("url") or "", s.get("published_at"), s.get("retrieved_at") or now_iso(), s.get("confidence") or "medium", json.dumps(s.get("claims") or [], ensure_ascii=False), s.get("note") or "")
        )
    summary = report.get("company_summary") or "公司背调完成"
    l2 = c.execute("select status from stage_runs where job_id=? and stage='L2'", (job_id,)).fetchone()
    if l2 and l2["status"] == "pass":
        mark_stage(c, job_id, "L3", "complete", version="2.0", agent_id=agent_id, score=score, summary=summary[:500], output={"company_key": company_key, "risk_level": risk, "company_score": score, "communication_advice": report.get("communication_advice") or []})
    else:
        mark_stage(c, job_id, "L3", "blocked", version="2.0", agent_id=agent_id, score=score, summary="公司背调缓存已完成，但等待L2通过后挂接", output={"company_key": company_key, "risk_level": risk, "company_score": score, "cached_ready": True})
    c.commit(); c.close()
    out_path = RESEARCH_DIR / f"report_{job_id}.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "job_id": job_id, "company_key": company_key, "sources": len(sources), "company_score": score, "risk_level": risk}


def attach_cached_company_report(job_id: str) -> dict | None:
    c = connect()
    job = c.execute("select company_key,l0_detail_status from jobs where job_id=?", (job_id,)).fetchone()
    l1 = c.execute("select status from stage_runs where job_id=? and stage='L1'", (job_id,)).fetchone()
    l2 = c.execute("select status from stage_runs where job_id=? and stage='L2'", (job_id,)).fetchone()
    if not job or job["l0_detail_status"] != "detail_complete" or not l1 or l1["status"] != "pass" or not l2 or l2["status"] != "pass":
        c.close(); return None
    r = c.execute("select * from company_reports where company_key=?", (job["company_key"],)).fetchone()
    if not r:
        c.close(); return None
    cache_hours = int(load_system()["due_diligence"].get("company_cache_hours", 72))
    try:
        researched = datetime.fromisoformat(r["researched_at"])
        if datetime.now(TZ) - researched > timedelta(hours=cache_hours):
            c.close(); return None
    except Exception:
        c.close(); return None
    report = json.loads(r["report_json"])
    mark_stage(c, job_id, "L3", "complete", version="2.1", agent_id="company_cache", score=r["company_score"], summary=f"复用同公司{cache_hours}小时内背调", output={"company_key": job["company_key"], "risk_level": r["risk_level"], "cached": True, "cache_hours": cache_hours})
    c.commit(); c.close(); return report
