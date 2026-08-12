from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
CANDIDATE_TXT = ROOT / "我的资料.txt"
PREFERENCES_TXT = ROOT / "求职要求.txt"
SYSTEM_JSON = ROOT / "系统配置.json"

SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")
KV_RE = re.compile(r"^\s*([^：:]+?)\s*[：:]\s*(.*?)\s*$")


def _clean_line(line: str) -> str:
    return line.strip().lstrip("-•* ").strip()


def _split_list(value: str) -> list[str]:
    if not value:
        return []
    parts = re.split(r"[，,、；;|/]+", value)
    return [x.strip() for x in parts if x.strip() and x.strip() not in {"未定", "未知", "不限"}]


def _number(value: str) -> float | None:
    if not value or value in {"未定", "不限", "无", "不知道"}:
        return None
    m = re.search(r"\d+(?:\.\d+)?", value)
    return float(m.group()) if m else None


def _bool3(value: str) -> bool | None:
    s = (value or "").strip().lower()
    if not s or s in {"未定", "未知", "不限", "不确定"}:
        return None
    if s in {"是", "可以", "接受", "yes", "y", "true", "1"}:
        return True
    if s in {"否", "不", "不接受", "no", "n", "false", "0"}:
        return False
    return None


def parse_txt(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8-sig") if path.exists() else ""
    sections: dict[str, dict[str, Any]] = {}
    current = "未分组"
    sections[current] = {"_lines": []}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        sm = SECTION_RE.match(line)
        if sm:
            current = sm.group(1).strip()
            sections.setdefault(current, {"_lines": []})
            continue
        km = KV_RE.match(line)
        if km:
            key, value = km.group(1).strip(), km.group(2).strip()
            sections[current][key] = value
        else:
            cleaned = _clean_line(line)
            if cleaned:
                sections[current].setdefault("_lines", []).append(cleaned)
    return {"raw_text": text, "sections": sections}


def load_candidate() -> dict[str, Any]:
    p = parse_txt(CANDIDATE_TXT)
    s = p["sections"]
    basic = s.get("基本信息", {})
    projects = s.get("项目经历", {})
    return {
        "raw_text": p["raw_text"],
        "name": basic.get("姓名") or None,
        "current_city": basic.get("当前城市") or None,
        "education": basic.get("最高学历") or None,
        "graduation_year": basic.get("毕业年份") or None,
        "formal_work_years": _number(basic.get("正式工作年限", "")),
        "email": basic.get("邮箱") or None,
        "phone": basic.get("电话") or None,
        "skills": s.get("技能与能力", {}).get("_lines", []),
        "projects": {k: v for k, v in projects.items() if not k.startswith("_")},
        "proof": s.get("可证明经历/作品", {}).get("_lines", []),
        "never_invent": s.get("绝对不能虚构", {}).get("_lines", []),
        "other_notes": "\n".join(s.get("其他说明", {}).get("_lines", [])),
    }


def load_preferences() -> dict[str, Any]:
    p = parse_txt(PREFERENCES_TXT)
    s = p["sections"]
    salary = s.get("薪资", {})
    location = s.get("地点", {})
    roles = s.get("岗位方向", {})
    industry = s.get("行业", {})
    company = s.get("公司类型", {})
    work = s.get("工作方式", {})
    return {
        "raw_text": p["raw_text"],
        "salary": {
            "min_monthly_k": _number(salary.get("最低月薪K", "")),
            "target_monthly_k": _number(salary.get("目标月薪K", "")),
            "ideal_monthly_k": _number(salary.get("理想月薪K", "")),
            "min_salary_months": _number(salary.get("最低薪资月数", "")),
            "accept_daily_hourly": _bool3(salary.get("是否接受日薪/时薪实习", "")),
            "min_daily": _number(salary.get("最低实习日薪", "")),
        },
        "location": {
            "preferred_cities": _split_list(location.get("优先城市", "")),
            "acceptable_cities": _split_list(location.get("可接受城市", "")),
            "remote": _bool3(location.get("是否接受远程", "")),
            "hybrid": _bool3(location.get("是否接受混合办公", "")),
            "relocation": _bool3(location.get("是否接受搬家", "")),
            "max_commute_minutes": _number(location.get("最大通勤分钟", "")),
        },
        "roles": {
            "targets": _split_list(roles.get("目标岗位", "")),
            "preferred_content": _split_list(roles.get("喜欢的工作内容", "")),
            "must_content": _split_list(roles.get("必须包含的工作内容", "")),
            "acceptable_content": _split_list(roles.get("可以接受的工作内容", "")),
            "avoid_content": _split_list(roles.get("不想做的工作内容", "")),
        },
        "industry": {
            "preferred": _split_list(industry.get("优先行业", "")),
            "acceptable": _split_list(industry.get("可接受行业", "")),
            "blocked": _split_list(industry.get("不要的行业", "")),
        },
        "company": {
            "accept_startup": _bool3(company.get("是否接受初创", "")),
            "accept_outsourcing": _bool3(company.get("是否接受外包公司", "")),
            "accept_dispatch": _bool3(company.get("是否接受劳务派遣", "")),
            "min_size": _number(company.get("最低公司规模", "")),
            "max_size": _number(company.get("最高公司规模", "")),
        },
        "work_style": {
            "accept_996": _bool3(work.get("是否接受996", "")),
            "accept_frequent_overtime": _bool3(work.get("是否接受频繁加班", "")),
            "accept_travel": _bool3(work.get("是否接受出差", "")),
            "accept_onsite": _bool3(work.get("是否接受长期驻场客户", "")),
            "accept_part_time": _bool3(work.get("是否接受兼职", "")),
            "accept_internship": _bool3(work.get("是否接受实习", "")),
            "internship_only": _bool3(work.get("当前仅接受实习", "")),
            "min_rest_days_per_week": _number(work.get("最低每周休息天数", "")),
        },
        "hard_reject": s.get("硬性淘汰", {}).get("_lines", []),
        "soft_preferences": s.get("软偏好", {}).get("_lines", []),
        "other_notes": "\n".join(s.get("其他说明", {}).get("_lines", [])),
    }


def load_system() -> dict[str, Any]:
    return json.loads(SYSTEM_JSON.read_text(encoding="utf-8"))


def validate() -> dict[str, Any]:
    candidate = load_candidate()
    prefs = load_preferences()
    system = load_system()
    warnings = []
    if prefs["salary"]["min_monthly_k"] is None and prefs["salary"]["min_daily"] is None:
        warnings.append("最低薪资未填写：L1不会按薪资硬淘汰。")
    if not prefs["location"]["preferred_cities"] and not prefs["location"]["acceptable_cities"]:
        warnings.append("城市未填写：L1不会按城市硬淘汰。")
    if candidate["education"] is None:
        warnings.append("最高学历未填写：L2/L4不得自行补造学历。")
    if candidate["formal_work_years"] is None:
        warnings.append("正式工作年限未填写：只作为未知风险，不得自行判定。")
    return {"ok": True, "candidate": candidate, "preferences": prefs, "system": system, "warnings": warnings}


if __name__ == "__main__":
    print(json.dumps(validate(), ensure_ascii=False, indent=2))
