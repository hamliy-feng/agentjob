from __future__ import annotations

import json
import re
from config_loader import load_candidate, load_preferences, load_system
from workflow_db import connect, mark_stage


def _contains(text: str, words: list[str]) -> list[str]:
    low = (text or "").lower()
    return [w for w in words if w and w.lower() in low]


def _avoid_role_hits(job: dict, words: list[str]) -> list[str]:
    """Match explicitly unwanted job content without treating collaborator mentions as the job itself."""
    title = (job.get("title") or "").lower()
    tags = " ".join(job.get("tags") or []).lower()
    desc = (job.get("description") or "").lower()
    hits: list[str] = []
    for word in words:
        if not word:
            continue
        w = word.lower()
        if w in title or w in tags:
            hits.append(word); continue
        if len(w) >= 4 and w in desc:
            negated = any(x + w in desc for x in ("非", "不是", "无需", "不做", "不涉及"))
            if not negated:
                hits.append(word); continue
        if w == "客服":
            patterns = (
                r"客服(?:岗位|工作|专员|运营|接待|咨询|坐席)",
                r"(?:负责|从事|承担|主要工作.{0,8})(?:在线|售前|售后|电话)?客服",
                r"(?:在线|售前|售后|电话)客服",
            )
            if any(re.search(p, desc) for p in patterns):
                hits.append(word)
    return list(dict.fromkeys(hits))


def _daily_salary_ranges(text: str) -> list[tuple[float, float]]:
    t = (text or "").replace(" ", "")
    out: list[tuple[float, float]] = []
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*[-~—至]\s*(\d+(?:\.\d+)?)元/天", t, re.I):
        a, b = float(m.group(1)), float(m.group(2))
        out.append((min(a, b), max(a, b)))
    for m in re.finditer(r"(?<![-~—至\d.])(\d+(?:\.\d+)?)元/天", t, re.I):
        v = float(m.group(1))
        out.append((v, v))
    return out


def _schedule_daily_rules(raw_text: str) -> list[tuple[float, float]]:
    """Parse user-authored schedule-linked daily salary rules, e.g. 250的上4休3；300一天的可以双休."""
    text = (raw_text or "").replace(" ", "")
    rules: list[tuple[float, float]] = []
    for m in re.finditer(r"(\d+(?:\.\d+)?)(?:元?/天|一天|的)?[^；;\n]{0,20}?上(\d+(?:\.\d+)?)休(\d+(?:\.\d+)?)", text):
        pay, work_days = float(m.group(1)), float(m.group(2))
        rules.append((work_days, pay))
    for m in re.finditer(r"(\d+(?:\.\d+)?)(?:元?/天|一天|的)?[^；;\n]{0,20}?(?:双休|五天工作制|做五休二)", text):
        rules.append((5.0, float(m.group(1))))
    dedup: dict[float, float] = {}
    for days, pay in rules:
        dedup[days] = max(pay, dedup.get(days, 0.0))
    return sorted(dedup.items())


def _required_daily_for_schedule(raw_text: str, work_days: float | None) -> float | None:
    rules = _schedule_daily_rules(raw_text)
    if not rules or work_days is None:
        return None
    for days, pay in rules:
        if work_days <= days + 1e-9:
            return pay
    return rules[-1][1]


def salary_info(s: str) -> dict:
    t = (s or "").replace(" ", "").upper()
    out = {"kind": "unknown", "min": None, "max": None}
    m = re.search(r"(\d+(?:\.\d+)?)\s*[-~—至]\s*(\d+(?:\.\d+)?)K", t)
    if m:
        return {"kind": "monthly_k", "min": float(m.group(1)), "max": float(m.group(2))}
    m = re.search(r"(\d+(?:\.\d+)?)\s*[-~—至]\s*(\d+(?:\.\d+)?)元/天", t, re.I)
    if m:
        return {"kind": "daily", "min": float(m.group(1)), "max": float(m.group(2))}
    m = re.search(r"(?<![-~—至\d.])(\d+(?:\.\d+)?)元/天", t, re.I)
    if m:
        v = float(m.group(1)); return {"kind": "daily", "min": v, "max": v}
    m = re.search(r"(\d+(?:\.\d+)?)\s*[-~—至]\s*(\d+(?:\.\d+)?)元/时", t, re.I)
    if m:
        return {"kind": "hourly", "min": float(m.group(1)), "max": float(m.group(2))}
    return out


def evaluate_job(job: dict) -> dict:
    candidate = load_candidate()
    prefs = load_preferences()
    system = load_system()
    policy = system["screening"]
    full = " ".join([
        job.get("title", ""), job.get("company", ""), job.get("location", ""), job.get("salary", ""),
        job.get("experience", ""), job.get("education", ""), " ".join(job.get("tags") or []), job.get("description", "")
    ])
    hard_reasons = []
    risks = []
    reasons = []
    score = 45.0

    hard_words = list(dict.fromkeys((prefs.get("hard_reject") or []) + (prefs.get("roles", {}).get("avoid_content") or [])))
    hits = _avoid_role_hits(job, hard_words)
    if hits:
        hard_reasons.append("命中硬性淘汰：" + "、".join(hits))

    blocked_industry = prefs.get("industry", {}).get("blocked") or []
    ind_hits = _contains(full, blocked_industry)
    if ind_hits:
        hard_reasons.append("命中不要的行业：" + "、".join(ind_hits))

    sal = salary_info(job.get("salary", ""))
    sp = prefs.get("salary", {})
    if sal["kind"] == "monthly_k" and sp.get("min_monthly_k") is not None and sal["min"] is not None and sal["min"] < sp["min_monthly_k"]:
        hard_reasons.append(f"月薪下限 {sal['min']:g}K 低于最低要求 {sp['min_monthly_k']:g}K")
    if sal["kind"] in {"daily", "hourly"}:
        accept = sp.get("accept_daily_hourly")
        if accept is False:
            hard_reasons.append("明确不接受日薪/时薪岗位")
        if sal["kind"] == "daily" and sp.get("min_daily") is not None and sal["min"] is not None:
            min_daily = float(sp["min_daily"])
            if sal["min"] < min_daily:
                hard_reasons.append(f"日薪下限 {sal['min']:g} 低于最低实习日薪 {min_daily:g}")
            detail_salary_text = " ".join([job.get("description", "") or "", job.get("company_intro", "") or ""])
            other_ranges = _daily_salary_ranges(detail_salary_text)
            if other_ranges:
                detail_min = min(x[0] for x in other_ranges)
                if detail_min < min_daily:
                    hard_reasons.append(f"BOSS详情内另有日薪下限 {detail_min:g}，低于最低实习日薪 {min_daily:g}")

    city_constraints = (prefs.get("location", {}).get("preferred_cities") or []) + (prefs.get("location", {}).get("acceptable_cities") or [])
    if city_constraints and not _contains(job.get("location", ""), city_constraints):
        hard_reasons.append("城市不在优先/可接受城市范围")

    if prefs.get("work_style", {}).get("accept_internship") is False and ("实习" in job.get("title", "") or sal["kind"] in {"daily", "hourly"}):
        hard_reasons.append("明确不接受实习")

    min_rest = prefs.get("work_style", {}).get("min_rest_days_per_week")
    rest_days = None
    rest_evidence = "未说明"
    if job.get("work_days_per_week") is not None:
        try:
            rest_days = max(0.0, 7.0 - float(job.get("work_days_per_week")))
            rest_evidence = f"{float(job.get('work_days_per_week')):g}天/周"
        except Exception:
            pass
    if rest_days is None:
        m = re.search(r"周休\s*(\d+(?:\.\d+)?)\s*天", full)
        if m:
            rest_days = float(m.group(1)); rest_evidence = m.group(0)
        else:
            m = re.search(r"月休\s*(\d+(?:\.\d+)?)\s*[-~—至]\s*(\d+(?:\.\d+)?)\s*天", full)
            if m:
                monthly_max = max(float(m.group(1)), float(m.group(2)))
                rest_days = monthly_max / 4.345; rest_evidence = m.group(0)
            else:
                m = re.search(r"月休\s*(\d+(?:\.\d+)?)\s*天", full)
                if m:
                    rest_days = float(m.group(1)) / 4.345; rest_evidence = m.group(0)
        if rest_days is None:
            m = re.search(r"(\d+(?:\.\d+)?)\s*天/周", full)
            if m:
                rest_days = max(0.0, 7.0 - float(m.group(1))); rest_evidence = m.group(0)
            elif re.search(r"(?:周末)?双休|五天工作制|做五休二", full):
                rest_days = 2.0; rest_evidence = "双休/五天工作制"
            elif re.search(r"大小周|单双休", full):
                rest_days = 1.5; rest_evidence = "大小周/单双休"
            elif re.search(r"单休|六天工作制|做六休一", full):
                rest_days = 1.0; rest_evidence = "单休/六天制"
    if min_rest is not None:
        if rest_days is not None and rest_days < float(min_rest):
            hard_reasons.append(f"休息制度不足：{rest_evidence}，每周约休 {rest_days:g} 天 < 要求 {float(min_rest):g} 天")
        elif rest_days is None:
            risks.append(f"JD未明确休息制度；沟通时必须确认每周至少休 {float(min_rest):g} 天")

    # 用户可在求职要求里写“250的上4休3；300一天的可以双休”这类联动硬条件。
    work_days = None
    if rest_days is not None:
        work_days = max(0.0, 7.0 - rest_days)
    elif job.get("work_days_per_week") is not None:
        try: work_days = float(job.get("work_days_per_week"))
        except Exception: pass
    schedule_min_daily = _required_daily_for_schedule(prefs.get("raw_text", ""), work_days)
    if sal["kind"] == "daily" and sal.get("min") is not None:
        if schedule_min_daily is not None and sal["min"] < schedule_min_daily:
            hard_reasons.append(f"当前工作制约 {work_days:g} 天/周，日薪下限 {sal['min']:g} 低于该工作制要求 {schedule_min_daily:g}")
        elif schedule_min_daily is not None:
            reasons.append(f"工作制联动日薪满足：约{work_days:g}天/周 ≥ {schedule_min_daily:g}元/天")
        elif _schedule_daily_rules(prefs.get("raw_text", "")) and work_days is None:
            risks.append("求职要求包含按每周工作天数区分的日薪条件，但JD未明确每周工作天数，需沟通确认")

    targets = prefs.get("roles", {}).get("targets") or []
    title_hits = _contains(job.get("title", ""), targets)
    content_hits = _contains(full, prefs.get("roles", {}).get("preferred_content") or [])
    must = prefs.get("roles", {}).get("must_content") or []
    must_hits = _contains(full, must)
    if must and len(must_hits) < len(must):
        missing = [x for x in must if x not in must_hits]
        hard_reasons.append("缺少必须工作内容：" + "、".join(missing))

    if title_hits:
        score += min(18, 7 + 3 * len(title_hits)); reasons.append("目标岗位命中：" + "、".join(title_hits))
    if content_hits:
        score += min(15, 3 * len(content_hits)); reasons.append("喜欢工作内容命中：" + "、".join(content_hits))
    if min_rest is not None and rest_days is not None and rest_days >= float(min_rest):
        score += 4; reasons.append(f"休息制度满足：{rest_evidence}")
    if sal["kind"] == "daily" and sp.get("min_daily") is not None and sal["min"] is not None and sal["min"] >= sp["min_daily"]:
        score += 5; reasons.append(f"日薪下限满足 ≥{sp['min_daily']:g}")

    for kw, weight in (policy.get("weights", {}).get("positive") or {}).items():
        if kw.lower() in full.lower():
            score += float(weight); reasons.append(f"{kw} +{weight}")
    for kw, weight in (policy.get("weights", {}).get("negative") or {}).items():
        matched = bool(_avoid_role_hits(job, [kw])) if kw in hard_words else kw.lower() in full.lower()
        if matched:
            score -= abs(float(weight)); risks.append(f"{kw} -{abs(float(weight)):g}")

    education = candidate.get("education")
    if not education and job.get("education"):
        risks.append(f"你的学历尚未填写；岗位要求 {job.get('education')}")
    years = candidate.get("formal_work_years")
    if years is None and job.get("experience"):
        risks.append(f"你的正式工作年限尚未填写；岗位要求 {job.get('experience')}")

    score = max(0.0, min(100.0, score))
    if hard_reasons:
        return {"status": "reject", "score": 0.0, "hard_reasons": hard_reasons, "reasons": reasons, "risks": risks}
    strong = float(policy.get("strong_threshold", 78)); rec = float(policy.get("recommend_threshold", 65)); watch = float(policy.get("watch_threshold", 50))
    bucket = "strong" if score >= strong else "recommend" if score >= rec else "watch" if score >= watch else "reject"
    return {"status": "pass" if bucket != "reject" else "reject", "score": round(score, 1), "bucket": bucket, "hard_reasons": [], "reasons": reasons, "risks": risks, "salary_parse": sal}


def run_l1(job_id: str) -> dict:
    c = connect(); row = c.execute("select * from jobs where job_id=?", (job_id,)).fetchone()
    if not row:
        c.close(); raise KeyError(job_id)
    if row["l0_detail_status"] != "detail_complete":
        result = {"status": "blocked", "reason": f"L0不是BOSS完整JD：{row['l0_detail_status']}"}
        mark_stage(c, job_id, "L1", "blocked", version="2.1", summary=result["reason"], output=result)
        c.commit(); c.close(); return result
    job = dict(row); job["tags"] = json.loads(job.pop("tags_json"))
    try:
        raw = json.loads(job.get("raw_json") or "{}")
    except Exception:
        raw = {}
    job["company_intro"] = raw.get("company_intro") or ""
    result = evaluate_job(job)
    mark_stage(c, job_id, "L1", result["status"], score=result["score"], version="2.1", summary="基础规则筛选", output=result)
    c.commit(); c.close(); return result


def run_pending() -> dict:
    c = connect(); ids = [r[0] for r in c.execute("select j.job_id from jobs j left join stage_runs s on s.job_id=j.job_id and s.stage='L1' where j.l0_detail_status='detail_complete' and (s.job_id is null or s.status not in ('pass','reject'))")]; c.close()
    out = {"processed": 0, "pass": 0, "reject": 0}
    for jid in ids:
        r = run_l1(jid); out["processed"] += 1; out[r["status"]] += 1
    return out
