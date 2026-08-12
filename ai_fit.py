from __future__ import annotations

import json
import os
import urllib.request

from config_loader import load_candidate, load_preferences, load_system
from workflow_db import connect, mark_stage


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def model_ready() -> bool:
    system = load_system()["ai"]
    return bool(_env(system["endpoint_env"]) and _env(system["model_env"]))


def _call_model(job: dict, l1: dict) -> dict:
    system = load_system()["ai"]
    endpoint = _env(system["endpoint_env"])
    model = _env(system["model_env"])
    key = _env(system["api_key_env"])
    candidate = load_candidate()
    prefs = load_preferences()
    sys_prompt = (
        "你是求职岗位适配审阅器。只依据候选人真实资料、求职要求和JD判断，不得虚构学历、任职经历、项目成果或技能。"
        "输出严格JSON：score(0-100), verdict(pass/watch/reject), fit_summary(<=180字), strengths(array), gaps(array), risks(array), questions(array), resume_focus(array)。"
        "重点区分真正的AI/数据/产品工作与挂名岗位，并考虑学历、经验、实习时长、工作内容迁移性。"
    )
    user = {"candidate": candidate, "preferences": prefs, "job": job, "l1": l1}
    payload = {"model": model, "messages": [{"role": "system", "content": sys_prompt}, {"role": "user", "content": json.dumps(user, ensure_ascii=False)}], "temperature": 0.2, "response_format": {"type": "json_object"}}
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(endpoint, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=90) as r:
        data = json.loads(r.read().decode("utf-8"))
    return json.loads(data["choices"][0]["message"]["content"])


def run_l2(job_id: str, *, force: bool = False) -> dict:
    c = connect()
    job_row = c.execute("select * from jobs where job_id=?", (job_id,)).fetchone()
    l1_row = c.execute("select * from stage_runs where job_id=? and stage='L1'", (job_id,)).fetchone()
    existing_l2 = c.execute("select * from stage_runs where job_id=? and stage='L2'", (job_id,)).fetchone()
    if not job_row:
        c.close(); raise KeyError(job_id)
    if not l1_row or l1_row["status"] != "pass":
        result = {"status": "blocked", "reason": "L1未通过"}
        mark_stage(c, job_id, "L2", "blocked", version="2.0", summary="L1未通过，L2不运行", output=result)
        c.commit(); c.close(); return result
    if job_row["l0_detail_status"] != "detail_complete":
        result = {"status": "needs_detail", "reason": f"L0不是BOSS完整JD：{job_row['l0_detail_status']}；必须先由9227已登录BOSS页面补全"}
        mark_stage(c, job_id, "L2", "needs_detail", version="2.1", summary=result["reason"], output=result)
        c.commit(); c.close(); return result
    if existing_l2 and existing_l2["status"] in {"pass","watch","reject"} and not force:
        try: out=json.loads(existing_l2["output_json"] or "{}")
        except Exception: out={}
        out.setdefault("status", existing_l2["status"])
        out["preserved_existing_review"] = True
        c.close(); return out
    if not model_ready():
        c.close()
        from agent_bridge import save_l2_request
        request_path = save_l2_request(job_id)
        result = {"status": "needs_ai", "reason": "真实AI模型尚未配置；已生成标准L2请求，可由任意外部Agent复核，不把规则分冒充AI适配分", "request_path": str(request_path)}
        c = connect(); mark_stage(c, job_id, "L2", "needs_ai", version="2.1", summary=result["reason"], output=result)
        c.commit(); c.close(); return result
    job = dict(job_row); job["tags"] = json.loads(job.pop("tags_json"))
    l1 = json.loads(l1_row["output_json"])
    try:
        review = _call_model(job, l1)
        score = max(0.0, min(100.0, float(review.get("score", 0))))
        verdict = review.get("verdict") if review.get("verdict") in {"pass", "watch", "reject"} else ("pass" if score >= 65 else "watch" if score >= 50 else "reject")
        status = "pass" if verdict == "pass" else verdict
        review["score"] = score; review["verdict"] = verdict
        mark_stage(c, job_id, "L2", status, version="2.0", agent_id="llm", score=score, summary=review.get("fit_summary", "")[:500], output=review)
        c.commit(); c.close(); return review
    except Exception as e:
        result = {"status": "error", "error": str(e)}
        mark_stage(c, job_id, "L2", "error", version="2.0", summary="AI适配调用失败", output={}, error=str(e)[:1000])
        c.commit(); c.close(); return result


def run_pending() -> dict:
    c = connect(); ids = [r[0] for r in c.execute("select s.job_id from stage_runs s left join stage_runs l2 on l2.job_id=s.job_id and l2.stage='L2' where s.stage='L1' and s.status='pass' and (l2.job_id is null or l2.status in ('needs_ai','needs_detail','error'))")]; c.close()
    out = {"processed": 0, "pass": 0, "watch": 0, "reject": 0, "needs_ai": 0, "needs_detail": 0, "error": 0, "blocked": 0}
    for jid in ids:
        r = run_l2(jid); st = r.get("status") or r.get("verdict") or "error"; out["processed"] += 1; out[st] = out.get(st, 0) + 1
    return out
