from __future__ import annotations

import argparse
import json
from pathlib import Path

from config_loader import validate
from workflow_db import connect, mark_stage, now_iso, status_snapshot, upsert_job
from screening import run_l1, run_pending as run_l1_pending
from ai_fit import run_l2, run_pending as run_l2_pending
from due_diligence import attach_cached_company_report, build_request, ingest_report, save_request
from materials import generate_l4
from agent_bridge import build_l2_request, save_l2_request, ingest_l2_review
from orchestrator import advance_job, advance_all


def cmd_ingest(path: str):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    jobs = payload.get("jobs", []) if isinstance(payload, dict) and "jobs" in payload else payload if isinstance(payload, list) else [payload]
    ids = [upsert_job(j) for j in jobs]
    print(json.dumps({"ingested": len(ids), "job_ids": ids}, ensure_ascii=False, indent=2))


def cmd_status():
    print(json.dumps(status_snapshot(), ensure_ascii=False, indent=2))


def cmd_doctor():
    print(json.dumps(validate(), ensure_ascii=False, indent=2))


def cmd_l1(job_id: str | None):
    out = run_l1(job_id) if job_id else run_l1_pending()
    print(json.dumps(out, ensure_ascii=False, indent=2))


def cmd_l2(job_id: str | None):
    out = run_l2(job_id) if job_id else run_l2_pending()
    print(json.dumps(out, ensure_ascii=False, indent=2))


def cmd_l2_request(job_id: str):
    path = save_l2_request(job_id)
    print(json.dumps({"path": str(path), "request": build_l2_request(job_id)}, ensure_ascii=False, indent=2))


def cmd_l2_ingest(path: str, agent_id: str):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    print(json.dumps(ingest_l2_review(payload, agent_id=agent_id), ensure_ascii=False, indent=2))


def cmd_advance(job_id: str | None):
    out = advance_job(job_id) if job_id else advance_all()
    print(json.dumps(out, ensure_ascii=False, indent=2))


def cmd_research_request(job_id: str):
    path = save_request(job_id)
    print(json.dumps({"path": str(path), "request": build_request(job_id)}, ensure_ascii=False, indent=2))


def cmd_research_ingest(path: str):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    print(json.dumps(ingest_report(payload), ensure_ascii=False, indent=2))


def cmd_attach_l3(job_id: str):
    report = attach_cached_company_report(job_id)
    print(json.dumps({"attached": bool(report), "report": report}, ensure_ascii=False, indent=2))


def cmd_l4(job_id: str):
    print(json.dumps(generate_l4(job_id), ensure_ascii=False, indent=2))


def cmd_confirm(job_id: str, note: str):
    c = connect()
    gate = c.execute("select * from application_gates where job_id=?", (job_id,)).fetchone()
    if not gate or gate["gate_status"] != "awaiting_confirmation":
        c.close(); raise SystemExit("该岗位尚未进入L5 awaiting_confirmation。")
    c.execute("update application_gates set confirmed=1,confirmation_note=?,updated_at=? where job_id=?", (note or "user confirmed", now_iso(), job_id))
    mark_stage(c, job_id, "L5", "ready", version="2.0", agent_id="human", summary="人工已确认，允许执行初始投递/沟通动作", output={"confirmed": True, "executed": False})
    c.commit(); c.close(); print(json.dumps({"job_id": job_id, "confirmed": True, "executed": False}, ensure_ascii=False, indent=2))


def cmd_report(job_id: str):
    c = connect()
    job = c.execute("select * from jobs where job_id=?", (job_id,)).fetchone()
    stages = [dict(r) for r in c.execute("select * from stage_runs where job_id=? order by stage", (job_id,))]
    report = c.execute("select * from company_reports where company_key=(select company_key from jobs where job_id=?)", (job_id,)).fetchone()
    material = c.execute("select * from materials where job_id=?", (job_id,)).fetchone()
    gate = c.execute("select * from application_gates where job_id=?", (job_id,)).fetchone()
    c.close()
    out = {"job": dict(job) if job else None, "stages": stages, "company_report": json.loads(report["report_json"]) if report else None, "material": json.loads(material["material_json"]) if material else None, "gate": dict(gate) if gate else None}
    print(json.dumps(out, ensure_ascii=False, indent=2))


def main():
    p = argparse.ArgumentParser(description="Portable Job Agent v2")
    sp = p.add_subparsers(dest="cmd", required=True)
    sp.add_parser("doctor")
    x = sp.add_parser("ingest"); x.add_argument("file")
    x = sp.add_parser("l1"); x.add_argument("job_id", nargs="?")
    x = sp.add_parser("l2"); x.add_argument("job_id", nargs="?")
    x = sp.add_parser("l2-request"); x.add_argument("job_id")
    x = sp.add_parser("l2-ingest"); x.add_argument("file"); x.add_argument("--agent-id", default="codex_agent")
    x = sp.add_parser("advance"); x.add_argument("job_id", nargs="?")
    x = sp.add_parser("research-request"); x.add_argument("job_id")
    x = sp.add_parser("research-ingest"); x.add_argument("file")
    x = sp.add_parser("attach-l3"); x.add_argument("job_id")
    x = sp.add_parser("l4"); x.add_argument("job_id")
    x = sp.add_parser("confirm"); x.add_argument("job_id"); x.add_argument("--note", default="")
    x = sp.add_parser("report"); x.add_argument("job_id")
    sp.add_parser("status")
    a = p.parse_args()
    {"doctor": lambda: cmd_doctor(), "ingest": lambda: cmd_ingest(a.file), "l1": lambda: cmd_l1(a.job_id), "l2": lambda: cmd_l2(a.job_id), "l2-request": lambda: cmd_l2_request(a.job_id), "l2-ingest": lambda: cmd_l2_ingest(a.file, a.agent_id), "advance": lambda: cmd_advance(a.job_id), "research-request": lambda: cmd_research_request(a.job_id), "research-ingest": lambda: cmd_research_ingest(a.file), "attach-l3": lambda: cmd_attach_l3(a.job_id), "l4": lambda: cmd_l4(a.job_id), "confirm": lambda: cmd_confirm(a.job_id, a.note), "report": lambda: cmd_report(a.job_id), "status": lambda: cmd_status()}[a.cmd]()


if __name__ == "__main__":
    main()
