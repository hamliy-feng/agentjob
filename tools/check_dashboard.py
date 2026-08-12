from __future__ import annotations

import json
import urllib.request

BASE = "http://127.0.0.1:8799"


def get(path: str):
    with urllib.request.urlopen(BASE + path, timeout=5) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def main() -> int:
    status, health = get("/api/health")
    if status != 200 or not health.get("ok"):
        raise SystemExit("health failed")

    status, dashboard = get("/api/dashboard")
    if status != 200:
        raise SystemExit("dashboard failed")
    jobs = (dashboard.get("today_top") or []) + (dashboard.get("qualified") or [])
    if not jobs:
        raise SystemExit("dashboard has no visible jobs")

    jid = jobs[0]["job_id"]
    status, detail = get(f"/api/jobs/{jid}")
    if status != 200 or not detail.get("job"):
        raise SystemExit("detail failed")

    print(json.dumps({
        "ok": True,
        "service_version": health.get("version"),
        "boss_9227_online": health.get("boss_9227_online"),
        "top_count": len(dashboard.get("today_top") or []),
        "qualified_count": len(dashboard.get("qualified") or []),
        "detail_job_id": jid,
        "detail_company": detail["job"].get("company"),
        "detail_title": detail["job"].get("title"),
        "detail_l3": (detail.get("stages") or {}).get("L3", {}).get("status"),
        "detail_l5": (detail.get("application_state") or {}).get("gate_status"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
