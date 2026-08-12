from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

from config_loader import load_preferences, load_system
from screening import salary_info
from workflow_db import TZ, connect, now_iso


def _loads(value: str | None, default):
    try:
        return json.loads(value) if value else default
    except Exception:
        return default


def _stage(c, job_id: str, stage: str) -> dict[str, Any] | None:
    row = c.execute("select * from stage_runs where job_id=? and stage=?", (job_id, stage)).fetchone()
    if not row:
        return None
    out = dict(row)
    out["output"] = _loads(out.pop("output_json", "{}"), {})
    return out


def _freshness_score(last_seen: str | None) -> float:
    if not last_seen:
        return 40.0
    try:
        dt = datetime.fromisoformat(last_seen)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)
        age_days = max(0.0, (datetime.now(TZ) - dt).total_seconds() / 86400.0)
    except Exception:
        return 40.0
    if age_days <= 1:
        return 100.0
    if age_days <= 3:
        return 95.0
    if age_days <= 7:
        return 85.0
    if age_days <= 14:
        return 70.0
    if age_days <= 30:
        return 55.0
    return 40.0


def _salary_score(job: dict, prefs: dict) -> tuple[float, bool, str]:
    text = str(job.get("salary") or "")
    info = salary_info(text)
    if "面议" in text or info["kind"] == "unknown":
        return 35.0, False, "薪资待确认"
    if info["kind"] == "daily":
        threshold = prefs.get("salary", {}).get("min_daily")
        minimum = float(info.get("min") or 0)
        if threshold is not None and minimum < float(threshold):
            return 0.0, False, f"日薪下限{minimum:g}<要求{float(threshold):g}"
        if minimum >= 300:
            return 100.0, True, f"{minimum:g}+元/天"
        if minimum >= 250:
            return 92.0, True, f"{minimum:g}+元/天"
        return 82.0, True, f"{minimum:g}+元/天"
    if info["kind"] == "monthly_k":
        minimum = float(info.get("min") or 0)
        return min(100.0, 65.0 + minimum * 2.0), True, text
    return 70.0, True, text


def _schedule_score(job: dict, l1: dict | None) -> tuple[float, bool, str]:
    days = job.get("work_days_per_week")
    if days is not None:
        try:
            d = float(days)
            if d <= 4:
                return 100.0, True, f"{d:g}天/周"
            if d <= 5:
                return 90.0, True, f"{d:g}天/周"
            return 0.0, False, f"{d:g}天/周"
        except Exception:
            pass
    output = (l1 or {}).get("output") or {}
    reasons = [str(x) for x in output.get("reasons") or []]
    risks = [str(x) for x in output.get("risks") or []]
    if any("休息制度满足" in x for x in reasons):
        return 90.0, True, next((x.split("：", 1)[-1] for x in reasons if "休息制度满足" in x), "休息制度已确认")
    if any("未明确休息制度" in x for x in risks):
        return 40.0, False, "双休待确认"
    return 50.0, False, "工作制度待确认"


def _activity_score(raw: dict) -> tuple[float, str]:
    text = str(raw.get("recruiter_activity") or "")
    if not text:
        return 50.0, "活跃度未知"
    if "刚刚" in text or "今日" in text or "当前" in text:
        return 100.0, text
    if "3日内" in text or "三日内" in text:
        return 88.0, text
    if "7日内" in text or "一周内" in text:
        return 75.0, text
    if "月内" in text or "30日" in text:
        return 60.0, text
    return 55.0, text


def _role_priority_score(job: dict, raw: dict, prefs: dict) -> tuple[float, str]:
    title = str(job.get("title") or "")
    full = " ".join([title, str(job.get("description") or ""), " ".join(raw.get("tags") or [])]).lower()
    targets = prefs.get("roles", {}).get("targets") or []
    title_hits = [x for x in targets if x and x.lower() in title.lower()]
    if title_hits:
        return 100.0, "目标岗位：" + "、".join(title_hits[:2])
    preferred = prefs.get("roles", {}).get("preferred_content") or []
    hits = [x for x in preferred if x and x.lower() in full]
    if len(hits) >= 3:
        return 90.0, "核心内容：" + "、".join(hits[:3])
    if hits:
        return 76.0, "相关内容：" + "、".join(hits[:2])
    return 55.0, "相邻岗位"


def _growth_score(job: dict, raw: dict, l2: dict | None, report: dict | None) -> tuple[float, list[str]]:
    text = " ".join([
        str(job.get("title") or ""),
        str(job.get("description") or ""),
        str((l2 or {}).get("summary") or ""),
        json.dumps((report or {}).get("job_specific_notes") or {}, ensure_ascii=False),
    ])
    positive = ["产品迭代", "上线", "0到1", "0-1", "闭环", "跨团队", "客户", "AI评测", "评测", "需求", "方案", "推动", "Agent", "大模型"]
    hits = [x for x in positive if x.lower() in text.lower()]
    score = min(100.0, 52.0 + len(hits) * 4.0)
    return score, hits[:5]


def _weighted(values: dict[str, float], weights: dict[str, float]) -> float:
    total = sum(float(weights.get(k, 0)) for k in values)
    if total <= 0:
        return 0.0
    return round(sum(float(values[k]) * float(weights.get(k, 0)) for k in values) / total, 1)


def opportunity(job: dict, raw: dict, l1: dict | None, l2: dict | None, report: dict | None) -> dict[str, Any]:
    system = load_system()
    prefs = load_preferences()
    weights = system.get("ranking", {}).get("opportunity_weights") or {}
    salary_score, salary_confirmed, salary_note = _salary_score(job, prefs)
    schedule_score, schedule_confirmed, schedule_note = _schedule_score(job, l1)
    recruiter_score, recruiter_note = _activity_score(raw)
    role_score, role_note = _role_priority_score(job, raw, prefs)
    growth_score, growth_hits = _growth_score(job, raw, l2, report)
    components = {
        "salary": salary_score,
        "freshness": _freshness_score(job.get("last_seen")),
        "schedule": schedule_score,
        "recruiter_activity": recruiter_score,
        "role_priority": role_score,
        "growth": growth_score,
    }
    return {
        "score": _weighted(components, weights),
        "components": components,
        "signals": {
            "salary": salary_note,
            "schedule": schedule_note,
            "recruiter_activity": recruiter_note,
            "role_priority": role_note,
            "growth_hits": growth_hits,
        },
        "confirmations": {
            "salary_confirmed": salary_confirmed,
            "schedule_confirmed": schedule_confirmed,
        },
    }


def _constraint_state(job: dict, l1: dict | None, opp: dict) -> dict[str, Any]:
    system = load_system()
    cfg = system.get("ranking", {})
    reasons = []
    ok = True
    if cfg.get("top_requires_confirmed_schedule", True) and not opp["confirmations"]["schedule_confirmed"]:
        ok = False; reasons.append("双休/每周休息天数待确认")
    if cfg.get("top_requires_explicit_salary", True) and not opp["confirmations"]["salary_confirmed"]:
        ok = False; reasons.append("薪资待确认")
    if not l1 or l1.get("status") != "pass":
        ok = False; reasons.append("L1未通过")
    return {"confirmed": ok, "pending": reasons}


def _highlights(job: dict, raw: dict, report_row: dict | None, opp: dict) -> list[str]:
    out: list[str] = []
    for value in [job.get("salary"), job.get("location")]:
        if value and value not in out:
            out.append(str(value))
    tags = raw.get("tags") or []
    for t in tags:
        if any(k.lower() in str(t).lower() for k in ["AI", "Agent", "大模型", "RAG", "产品", "FDE"]):
            out.append(str(t))
            break
    if raw.get("company_financing"):
        out.append(str(raw["company_financing"]))
    out.append(opp["signals"]["schedule"])
    if raw.get("recruiter_activity"):
        out.append(str(raw["recruiter_activity"]))
    if report_row and report_row.get("risk_level"):
        out.append(f"风险{report_row['risk_level']}")
    return list(dict.fromkeys(x for x in out if x))[:6]


def _card(c, row) -> dict[str, Any]:
    job = dict(row)
    raw = _loads(job.get("raw_json"), {})
    l1 = _stage(c, job["job_id"], "L1")
    l2 = _stage(c, job["job_id"], "L2")
    l3 = _stage(c, job["job_id"], "L3")
    report_row_raw = c.execute("select * from company_reports where company_key=?", (job.get("company_key"),)).fetchone()
    report_row = dict(report_row_raw) if report_row_raw else None
    report = _loads(report_row.get("report_json") if report_row else None, {})
    opp = opportunity(job, raw, l1, l2, report)
    constraints = _constraint_state(job, l1, opp)
    l2_score = float((l2 or {}).get("score") or 0)
    company_score = float((report_row or {}).get("company_score") or 0)
    final_score = None
    if l3 and l3.get("status") == "complete" and report_row and report_row.get("report_status") == "complete":
        weights = load_system().get("ranking", {}).get("final_weights") or {}
        final_score = _weighted({"l2_fit": l2_score, "company_quality": company_score, "opportunity_quality": opp["score"]}, weights)
    provisional_score = round(l2_score * 0.72 + opp["score"] * 0.28, 1)
    ui_row = c.execute("select * from job_ui_state where job_id=?", (job["job_id"],)).fetchone()
    ui_state = dict(ui_row) if ui_row else {"state": "active", "favorite": 0, "note": None, "updated_at": None}
    ui_state["favorite"] = bool(ui_state.get("favorite"))
    top_eligible = bool(
        job.get("l0_detail_status") == "detail_complete"
        and l1 and l1.get("status") == "pass"
        and l2 and l2.get("status") == "pass"
        and l3 and l3.get("status") == "complete"
        and report_row and report_row.get("report_status") == "complete"
        and constraints["confirmed"]
        and ui_state.get("state") == "active"
    )
    return {
        "job_id": job["job_id"],
        "company": job.get("company") or "",
        "title": job.get("title") or "",
        "salary": job.get("salary") or "",
        "location": job.get("location") or "",
        "education": job.get("education") or "",
        "experience": job.get("experience") or "",
        "work_days_per_week": job.get("work_days_per_week"),
        "internship_months": job.get("internship_months"),
        "detail_url": job.get("detail_url") or "",
        "last_seen": job.get("last_seen"),
        "l0_status": job.get("l0_detail_status"),
        "l1_status": (l1 or {}).get("status"),
        "l2_status": (l2 or {}).get("status"),
        "l3_status": (l3 or {}).get("status"),
        "l4_status": (_stage(c, job["job_id"], "L4") or {}).get("status"),
        "l5_status": (_stage(c, job["job_id"], "L5") or {}).get("status"),
        "l2_score": round(l2_score, 1),
        "company_score": round(company_score, 1) if report_row else None,
        "opportunity_score": opp["score"],
        "opportunity": opp,
        "final_score": final_score,
        "provisional_score": provisional_score,
        "top_eligible": top_eligible,
        "constraint_state": constraints,
        "risk_level": (report_row or {}).get("risk_level"),
        "fit_summary": ((l2 or {}).get("output") or {}).get("fit_summary") or (l2 or {}).get("summary"),
        "highlights": _highlights(job, raw, report_row, opp),
        "ui_state": ui_state,
    }


def dashboard_payload() -> dict[str, Any]:
    c = connect()
    candidates = []
    rows = c.execute("select * from jobs where l0_detail_status='detail_complete' order by last_seen desc").fetchall()
    for row in rows:
        l1 = c.execute("select status from stage_runs where job_id=? and stage='L1'", (row["job_id"],)).fetchone()
        l2 = c.execute("select status from stage_runs where job_id=? and stage='L2'", (row["job_id"],)).fetchone()
        # 当前硬规则是展示门：旧 L2/L3 即使仍为 pass，只要最新 L1 已 reject/blocked 就不得继续出现在工作台候选池。
        if l1 and l1["status"] == "pass" and l2 and l2["status"] == "pass":
            candidates.append(_card(c, row))
    top_limit = int(load_system().get("ranking", {}).get("top_limit") or load_system().get("daily_target") or 7)
    visible = [x for x in candidates if x.get("ui_state", {}).get("state") not in {"skip", "applied"}]
    def _score(x): return x["final_score"] if x["final_score"] is not None else x["provisional_score"]
    favorites = sorted([x for x in visible if x.get("ui_state", {}).get("favorite")], key=lambda x: (_score(x), x["last_seen"] or ""), reverse=True)
    top = sorted([x for x in visible if x["top_eligible"]], key=lambda x: (x.get("ui_state", {}).get("favorite", False), x["final_score"] or 0, x["last_seen"] or ""), reverse=True)[:top_limit]
    top_ids = {x["job_id"] for x in top}
    qualified = sorted([x for x in visible if x["job_id"] not in top_ids], key=lambda x: (x.get("ui_state", {}).get("favorite", False), _score(x), x["last_seen"] or ""), reverse=True)
    stage_counts = {r[0]: r[1] for r in c.execute("select stage||':'||status,count(*) from stage_runs group by stage,status order by stage,status")}
    counts = {
        "jobs": c.execute("select count(*) from jobs").fetchone()[0],
        "l0_complete": stage_counts.get("L0:complete", 0),
        "l1_pass": stage_counts.get("L1:pass", 0),
        "l2_pass": stage_counts.get("L2:pass", 0),
        "l3_complete": stage_counts.get("L3:complete", 0),
        "today_top": len(top),
        "qualified": len(qualified),
    }
    health = [dict(r) for r in c.execute("select * from source_health order by id desc limit 12")]
    latest_l2 = c.execute("select agent_id,updated_at from stage_runs where stage='L2' and agent_id is not null order by updated_at desc limit 1").fetchone()
    c.close()
    ai_cfg = load_system().get("ai", {})
    ai_api_configured = bool(os.getenv(ai_cfg.get("endpoint_env", "BOSS_AI_ENDPOINT")) and os.getenv(ai_cfg.get("api_key_env", "BOSS_AI_API_KEY")))
    return {
        "generated_at": now_iso(),
        "favorites": favorites,
        "today_top": top,
        "qualified": qualified,
        "counts": counts,
        "source_health": health,
        "agent_status": {
            "ai_mode": "api" if ai_api_configured else "agent_bridge",
            "last_ai_agent": latest_l2["agent_id"] if latest_l2 else None,
            "last_ai_at": latest_l2["updated_at"] if latest_l2 else None,
            "l6_unsupervised_chat": False,
        },
        "ranking_contract": load_system().get("ranking", {}),
    }


def job_detail_payload(job_id: str) -> dict[str, Any] | None:
    c = connect()
    row = c.execute("select * from jobs where job_id=?", (job_id,)).fetchone()
    if not row:
        c.close(); return None
    job = dict(row)
    raw = _loads(job.get("raw_json"), {})
    stages = {}
    for stage in ["L0", "L1", "L2", "L3", "L4", "L5", "L6"]:
        s = _stage(c, job_id, stage)
        if s:
            stages[stage] = s
    report_row_raw = c.execute("select * from company_reports where company_key=?", (job.get("company_key"),)).fetchone()
    report_row = dict(report_row_raw) if report_row_raw else None
    report = _loads(report_row.get("report_json") if report_row else None, {})
    sources = [dict(r) for r in c.execute("select * from company_sources where company_key=? order by id", (job.get("company_key"),))]
    for source in sources:
        source["claims"] = _loads(source.pop("claims_json", "[]"), [])
    material_row = c.execute("select * from materials where job_id=?", (job_id,)).fetchone()
    material = _loads(material_row["material_json"] if material_row else None, {})
    gate_row = c.execute("select * from application_gates where job_id=?", (job_id,)).fetchone()
    ranking = _card(c, row) if (stages.get("L2") or {}).get("status") == "pass" else None
    c.close()
    return {
        "job": {
            **{k: v for k, v in job.items() if k not in {"raw_json", "source_evidence_json", "tags_json"}},
            "tags": _loads(job.get("tags_json"), []),
            "source_evidence": _loads(job.get("source_evidence_json"), {}),
            "raw": raw,
        },
        "stages": stages,
        "ranking": ranking,
        "company_report": {
            "meta": {k: v for k, v in (report_row or {}).items() if k != "report_json"},
            "report": report,
            "sources": sources,
        } if report_row else None,
        "material": material or None,
        "application_state": dict(gate_row) if gate_row else {"gate_status": "not_ready", "confirmed": 0, "executed": 0},
    }
