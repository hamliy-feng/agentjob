from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent

# L0 的核心目标不是“抓到一张卡片”，而是拿到 BOSS 登录态里的完整 JD。
CORE_FIELDS = ["title", "company", "salary", "location", "detail_url"]
JD_FIELDS = ["responsibilities", "requirements"]
RICH_FIELDS = [
    "education", "experience", "tags", "work_days_per_week", "internship_months",
    "recruiter_name", "recruiter_title", "recruiter_activity", "address",
    "benefits", "company_financing", "company_size", "company_industry",
    "company_intro", "business_info"
]


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict, set)):
        return bool(value)
    return True


def completeness(job: dict) -> dict:
    core_present = [f for f in CORE_FIELDS if _present(job.get(f))]
    core_missing = [f for f in CORE_FIELDS if not _present(job.get(f))]
    rich_present = [f for f in RICH_FIELDS if _present(job.get(f))]
    rich_missing = [f for f in RICH_FIELDS if not _present(job.get(f))]

    responsibilities = (job.get("responsibilities") or "").strip()
    requirements = (job.get("requirements") or "").strip()
    description = (job.get("description") or "").strip()
    jd_text = "\n".join(x for x in [responsibilities, requirements] if x).strip() or description
    jd_chars = len(jd_text)

    # 优先要求职责+要求都能分段；BOSS部分页面会把职责和要求合并成一个长区块。
    # 合并区块仅在登录态、核心上下文丰富且无登录墙标记时兜底，避免把截断摘要误判完整JD。
    structured_jd = len(responsibilities) >= 60 and len(requirements) >= 60
    raw_page = (job.get("raw_page_text") or jd_text or "")
    login_wall = ("登录查看完整内容" in raw_page) or ("我要招聘 我要找工作 登录/注册" in raw_page)
    source_evidence = job.get("source_evidence") or {}
    logged_in = source_evidence.get("logged_in") is True
    combined_block = max(len(responsibilities), len(requirements), len(description)) >= 300
    combined_fallback = jd_chars >= 300 and combined_block and len(rich_present) >= 6 and logged_in and not login_wall
    text_fallback = jd_chars >= 280 and ("岗位" in jd_text or "职责" in jd_text or "要求" in jd_text) and not login_wall
    full_jd = structured_jd or combined_fallback or text_fallback

    source = (job.get("source") or "").strip()
    boss_primary = source in {"boss_9227", "boss_logged_in", "boss_official_api"}
    core_complete = not core_missing and full_jd and boss_primary

    core_score = (len(core_present) / len(CORE_FIELDS)) * 45
    jd_score = 35 if full_jd else min(35, jd_chars / 280 * 35 if jd_chars else 0)
    rich_score = (len(rich_present) / len(RICH_FIELDS)) * 20
    score = round(min(100.0, core_score + jd_score + rich_score), 1)

    return {
        "core_complete": core_complete,
        "boss_primary": boss_primary,
        "full_jd": full_jd,
        "structured_jd": structured_jd,
        "score": score,
        "jd_chars": jd_chars,
        "core_present": core_present,
        "core_missing": core_missing,
        "rich_present": rich_present,
        "rich_missing": rich_missing,
    }


def detail_status(job: dict, *, security_blocked: bool = False, external: bool = False) -> str:
    if security_blocked:
        return "blocked_security"
    comp = completeness(job)
    if comp["core_complete"]:
        return "detail_complete"
    # 外部公开来源只能补充/发现，不能替代 BOSS 完整 JD 作为 L2 主输入。
    if external:
        return "external_enriched_needs_boss"
    return "needs_detail"


def can_enter_l1(job: dict) -> bool:
    return detail_status(job) == "detail_complete"


def compare_jobs(expected: dict, actual: dict) -> dict:
    fields = CORE_FIELDS + JD_FIELDS + RICH_FIELDS
    checks = []
    for field in fields:
        ev, av = expected.get(field), actual.get(field)
        if field in {"description", "responsibilities", "requirements", "company_intro"}:
            e = (ev or "").replace(" ", "")
            a = (av or "").replace(" ", "")
            probes = [x.strip() for x in e.split("\n") if len(x.strip()) >= 10][:10]
            matched = [p for p in probes if p.replace(" ", "") in a]
            checks.append({"field": field, "expected_chars": len(e), "actual_chars": len(a), "probe_count": len(probes), "probe_matched": len(matched), "match": (len(matched) >= max(1, len(probes)//2)) if probes else bool(a)})
        elif isinstance(ev, list):
            es = {str(x).strip().lower() for x in ev if str(x).strip()}
            aset = {str(x).strip().lower() for x in (av or []) if str(x).strip()}
            checks.append({"field": field, "expected": ev, "actual": av, "missing": sorted(es-aset), "match": not (es-aset)})
        else:
            checks.append({"field": field, "expected": ev, "actual": av, "match": (str(ev).strip() == str(av).strip()) if _present(ev) else True})
    return {
        "expected_completeness": completeness(expected),
        "actual_completeness": completeness(actual),
        "checks": checks,
        "matched_fields": sum(1 for x in checks if x["match"]),
        "total_fields": len(checks),
    }


def load_gold(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
