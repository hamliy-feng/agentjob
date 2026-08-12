from __future__ import annotations

import json
from pathlib import Path

from config_loader import load_candidate, load_preferences
from workflow_db import ROOT, connect, mark_stage, now_iso

OUT_DIR = ROOT / "data" / "materials"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def _read_stage(c, job_id: str, stage: str):
    r = c.execute("select * from stage_runs where job_id=? and stage=?", (job_id, stage)).fetchone()
    return r, (json.loads(r["output_json"]) if r else None)


def generate_l4(job_id: str) -> dict:
    c = connect()
    job = c.execute("select * from jobs where job_id=?", (job_id,)).fetchone()
    if not job:
        c.close(); raise KeyError(job_id)
    l2_row, l2 = _read_stage(c, job_id, "L2")
    l3_row, l3_stage = _read_stage(c, job_id, "L3")
    if not l2_row or l2_row["status"] != "pass":
        result = {"status": "blocked", "reason": "L2未通过"}
        mark_stage(c, job_id, "L4", "blocked", version="2.0", summary=result["reason"], output=result)
        c.commit(); c.close(); return result
    if not l3_row or l3_row["status"] != "complete":
        result = {"status": "blocked", "reason": "L3公司背调未完成"}
        mark_stage(c, job_id, "L4", "blocked", version="2.0", summary=result["reason"], output=result)
        c.commit(); c.close(); return result
    company_report_row = c.execute("select * from company_reports where company_key=?", (job["company_key"],)).fetchone()
    report = json.loads(company_report_row["report_json"]) if company_report_row else {}
    candidate = load_candidate(); prefs = load_preferences()
    strengths = l2.get("strengths") or candidate.get("skills", [])[:5]
    gaps = l2.get("gaps") or []
    questions = l2.get("questions") or []
    dd_advice = report.get("communication_advice") or []
    company_summary = report.get("company_summary") or "公司背调已完成，详见来源矩阵。"
    risk_flags = report.get("risk_flags") or report.get("operating_and_legal_risk") or {}
    job_notes = report.get("job_specific_notes") or {}

    greeting = (
        f"您好，我在关注贵司的「{job['title']}」岗位。我的项目实践与"
        f"{('、'.join(strengths[:4]) if strengths else '该岗位核心工作')}有较强交集。"
        "我希望进一步了解岗位当前最优先解决的问题、团队分工以及对实习/正式候选人的核心期待。"
    )
    communication = list(dict.fromkeys([str(x) for x in dd_advice + questions]))[:12]
    material = {
        "job_id": job_id,
        "company": job["company"],
        "title": job["title"],
        "company_situation": {
            "summary": company_summary,
            "score": company_report_row["company_score"] if company_report_row else None,
            "risk_level": company_report_row["risk_level"] if company_report_row else "unknown",
            "benefits": report.get("benefits") or {},
            "funding": report.get("funding_investors") or {},
            "public_discussion": report.get("public_discussion") or {},
            "risk_flags": risk_flags
        },
        "job_situation": {
            "salary": job["salary"], "location": job["location"], "education": job["education"],
            "experience": job["experience"], "work_days_per_week": job["work_days_per_week"],
            "internship_months": job["internship_months"], "l2_score": l2_row["score"],
            "fit_summary": l2.get("fit_summary"), "strengths": strengths, "gaps": gaps,
            "job_specific_notes": job_notes
        },
        "communication_advice": communication,
        "greeting": greeting,
        "resume_focus": l2.get("resume_focus") or strengths[:5],
        "candidate_raw_source": "我的资料.txt",
        "preferences_raw_source": "求职要求.txt",
        "generated_at": now_iso()
    }
    c.execute(
        """insert into materials(job_id,company_report_key,communication_advice,greeting,resume_focus_json,material_json,generated_at)
           values(?,?,?,?,?,?,?) on conflict(job_id) do update set company_report_key=excluded.company_report_key,communication_advice=excluded.communication_advice,greeting=excluded.greeting,resume_focus_json=excluded.resume_focus_json,material_json=excluded.material_json,generated_at=excluded.generated_at""",
        (job_id, job["company_key"], "\n".join(communication), greeting, json.dumps(material["resume_focus"], ensure_ascii=False), json.dumps(material, ensure_ascii=False), now_iso())
    )
    mark_stage(c, job_id, "L4", "complete", version="2.1", summary="公司情况+岗位情况+沟通建议+材料已生成", output={"material_path": f"data/materials/{job_id}.md"})
    existing_gate = c.execute("select * from application_gates where job_id=?", (job_id,)).fetchone()
    current_hash = job["content_hash"] or ""
    if not existing_gate:
        c.execute("insert into application_gates(job_id,gate_status,confirmed,executed,confirmation_note,updated_at,content_hash) values(?,?,?,?,?,?,?)", (job_id, "awaiting_confirmation", 0, 0, "L5必须人工确认", now_iso(), current_hash))
        mark_stage(c, job_id, "L5", "ready", version="2.1", summary="等待人工确认后执行投递/立即沟通", output={"confirmed": False, "executed": False})
    elif not existing_gate["confirmed"] and not existing_gate["executed"]:
        c.execute("update application_gates set gate_status='awaiting_confirmation',confirmation_note='L5必须人工确认',content_hash=?,verified_at=null,updated_at=? where job_id=?", (current_hash, now_iso(), job_id))
        mark_stage(c, job_id, "L5", "ready", version="2.1", summary="等待人工确认后执行投递/立即沟通", output={"confirmed": False, "executed": False})
    # 已确认/执行过的L5状态绝不能因重新生成L4材料被重置。
    c.commit(); c.close()

    md = [
        f"# {job['company']}｜{job['title']}", "",
        "## 公司情况", company_summary, "",
        f"- 公司评分：{material['company_situation']['score']}", f"- 风险级别：{material['company_situation']['risk_level']}",
        f"- 融资/资本：{json.dumps(material['company_situation']['funding'], ensure_ascii=False)}",
        f"- 福利：{json.dumps(material['company_situation']['benefits'], ensure_ascii=False)}", "",
        "## 岗位情况", f"- 薪资：{job['salary']}", f"- 地点：{job['location']}", f"- 学历：{job['education']}", f"- 经验：{job['experience']}",
        f"- L2适配分：{l2_row['score']}", f"- 适配结论：{l2.get('fit_summary') or ''}", f"- 优势：{'、'.join(str(x) for x in strengths)}", f"- 缺口：{'、'.join(str(x) for x in gaps)}", "",
        "## 沟通建议", *[f"- {x}" for x in communication], "", "## 首轮沟通话术", greeting, "", "## 简历重点", *[f"- {x}" for x in material['resume_focus']],
        "", "## 注意", "- 不得虚构学历、工作年限、任职经历或成果数字。", "- L5 点击前必须人工确认；L6持续聊天默认关闭。"
    ]
    path = OUT_DIR / f"{job_id}.md"; path.write_text("\n".join(md), encoding="utf-8")
    return {"status": "complete", "path": str(path), "material": material}
