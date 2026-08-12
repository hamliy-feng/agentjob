from __future__ import annotations

import json
import mimetypes
import subprocess
import sys
import urllib.request
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from config_loader import load_system, validate
from ranking import dashboard_payload, job_detail_payload
from profile_manager import get_preferences_view, get_profile_view, save_preferences, save_supplement, upload_resume
from workflow_db import ROOT, connect, mark_stage, now_iso

HOST = "127.0.0.1"
PORT = 8799
FRONTEND = ROOT / "frontend"
ACTION_LOG = ROOT / "data" / "executor_actions.log"


def _json_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")


def _cdp_online() -> bool:
    endpoint = load_system().get("radar", {}).get("cdp_endpoint", "http://127.0.0.1:9227").rstrip("/")
    try:
        with urllib.request.urlopen(endpoint + "/json/version", timeout=0.45) as response:
            return response.status == 200
    except Exception:
        return False


def api_dashboard() -> dict:
    payload = dashboard_payload()
    payload["runtime"] = {
        "boss_9227_online": _cdp_online(),
        "phase": "Phase 2",
        "phase1_frozen": True,
        "frontend_version": "2.0",
    }
    return payload


def set_ui_state(job_id: str, state: str, note: str = "") -> dict:
    if state not in {"active", "hold", "skip", "applied"}:
        raise ValueError("invalid ui state")
    c = connect()
    job = c.execute("select content_hash from jobs where job_id=?", (job_id,)).fetchone()
    if not job:
        c.close(); raise KeyError(job_id)
    c.execute(
        """insert into job_ui_state(job_id,state,favorite,state_content_hash,note,updated_at)
           values(?,?,0,?,?,?)
           on conflict(job_id) do update set state=excluded.state,state_content_hash=excluded.state_content_hash,note=excluded.note,updated_at=excluded.updated_at""",
        (job_id, state, job["content_hash"] or "", note, now_iso()),
    )
    c.commit(); c.close()
    return {"ok": True, "job_id": job_id, "state": state}


def set_favorite(job_id: str, favorite: bool) -> dict:
    c = connect()
    job = c.execute("select content_hash from jobs where job_id=?", (job_id,)).fetchone()
    if not job:
        c.close(); raise KeyError(job_id)
    existing = c.execute("select state,note from job_ui_state where job_id=?", (job_id,)).fetchone()
    state = existing["state"] if existing else "active"
    note = existing["note"] if existing else ""
    c.execute(
        """insert into job_ui_state(job_id,state,favorite,state_content_hash,note,updated_at)
           values(?,?,?,?,?,?)
           on conflict(job_id) do update set favorite=excluded.favorite,updated_at=excluded.updated_at""",
        (job_id, state, 1 if favorite else 0, job["content_hash"] or "", note, now_iso()),
    )
    c.commit(); c.close()
    return {"ok": True, "job_id": job_id, "favorite": bool(favorite)}


def confirm_apply(job_id: str) -> dict:
    c = connect()
    job = c.execute("select job_id,title,company,content_hash from jobs where job_id=?", (job_id,)).fetchone()
    l4 = c.execute("select status from stage_runs where job_id=? and stage='L4'", (job_id,)).fetchone()
    gate = c.execute("select * from application_gates where job_id=?", (job_id,)).fetchone()
    if not job:
        c.close(); raise KeyError(job_id)
    if not l4 or l4["status"] != "complete":
        c.close(); return {"ok": False, "status": "blocked", "reason": "L4材料尚未完成"}
    if not gate:
        c.close(); return {"ok": False, "status": "blocked", "reason": "L5 application gate不存在"}
    current_hash = job["content_hash"] or ""
    already = c.execute("select status,verified_at from application_history where job_id=? and content_hash=? and status='verified_sent'", (job_id, current_hash)).fetchone()
    if already:
        c.close(); return {"ok": False, "status": "already_executed", "reason": "当前JD版本已确认投递成功，禁止重复投递"}
    ui = c.execute("select state from job_ui_state where job_id=?", (job_id,)).fetchone()
    if ui and ui["state"] in {"skip", "applied"}:
        c.close(); return {"ok": False, "status": "blocked", "reason": f"当前岗位状态={ui['state']}，除非JD更新否则不再投递"}
    if gate["executed"]:
        c.close(); return {"ok": False, "status": "already_executed", "reason": "该岗位已经执行过初始动作"}
    if gate["confirmed"] or gate["gate_status"] in {"confirmed_pending_execution", "executing_initial_action"}:
        c.close(); return {"ok": False, "status": "already_pending", "reason": "已经确认，正在等待/执行初始动作"}
    if gate["gate_status"] not in {"awaiting_confirmation", "execution_error"}:
        c.close(); return {"ok": False, "status": "blocked", "reason": f"当前gate={gate['gate_status']}不可投递"}
    c.execute(
        """update application_gates
           set confirmed=1,executed=0,gate_status='confirmed_pending_execution',confirmation_note=?,content_hash=?,verified_at=null,updated_at=?
           where job_id=?""",
        ("Dashboard点击投递＝人工明确确认", current_hash, now_iso(), job_id),
    )
    mark_stage(
        c, job_id, "L5", "ready", version="2.1", agent_id="human",
        summary="用户在Dashboard点击投递；已确认一次初始动作",
        output={"confirmed": True, "executed": False, "source": "dashboard_apply_click"},
    )
    c.commit(); c.close()
    return {"ok": True, "status": "confirmed_pending_execution", "job_id": job_id}


def _spawn_executor(action: str, job_id: str) -> int:
    ACTION_LOG.parent.mkdir(parents=True, exist_ok=True)
    flag = {"open": "--open-job", "apply": "--apply-job", "verify": "--verify-job"}.get(action)
    if not flag:
        raise ValueError("unknown executor action")
    log = ACTION_LOG.open("a", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "executor.py"), flag, job_id],
        cwd=str(ROOT), stdout=log, stderr=log,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    log.close()
    return int(proc.pid)


def action_open(job_id: str) -> dict:
    c = connect(); row = c.execute("select detail_url from jobs where job_id=?", (job_id,)).fetchone(); c.close()
    if not row:
        raise KeyError(job_id)
    if not row["detail_url"]:
        return {"ok": False, "status": "blocked", "reason": "没有BOSS detail_url"}
    return {"ok": True, "status": "opening", "pid": _spawn_executor("open", job_id), "job_id": job_id}


def action_apply(job_id: str) -> dict:
    result = confirm_apply(job_id)
    if not result.get("ok"):
        return result
    result["pid"] = _spawn_executor("apply", job_id)
    return result


def action_verify(job_id: str) -> dict:
    c = connect(); gate = c.execute("select gate_status,executed from application_gates where job_id=?", (job_id,)).fetchone(); c.close()
    if not gate:
        return {"ok": False, "status": "blocked", "reason": "L5 application gate不存在"}
    if gate["executed"] or gate["gate_status"] == "verified_sent":
        return {"ok": True, "status": "already_verified", "job_id": job_id}
    if gate["gate_status"] != "verification_pending":
        return {"ok": False, "status": "blocked", "reason": "当前没有待验证的投递动作"}
    return {"ok": True, "status": "verifying", "pid": _spawn_executor("verify", job_id), "job_id": job_id}


def _safe_static(path: str) -> Path | None:
    rel = "index.html" if path in {"", "/"} else path.lstrip("/")
    candidate = (FRONTEND / rel).resolve()
    try:
        candidate.relative_to(FRONTEND.resolve())
    except Exception:
        return None
    return candidate if candidate.is_file() else None


class DashboardHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class H(BaseHTTPRequestHandler):
    server_version = "JobAgentDashboard/2.1"

    def _send_json(self, status: int, value) -> None:
        body = _json_bytes(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        n = int(self.headers.get("Content-Length", "0") or 0)
        if not n:
            return {}
        raw = self.rfile.read(n).decode("utf-8")
        return json.loads(raw) if raw.strip() else {}

    def do_GET(self):
        try:
            self._do_get()
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as e:
            traceback.print_exc()
            try:
                self._send_json(500, {"ok": False, "error": "api_failed", "detail": str(e)[:800]})
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _do_get(self):
        u = urlparse(self.path)
        if u.path == "/api/dashboard":
            self._send_json(200, api_dashboard()); return
        if u.path == "/api/health":
            self._send_json(200, {"ok": True, "service": "job-agent-dashboard", "version": "2.1", "boss_9227_online": _cdp_online(), "config": validate()["ok"]}); return
        if u.path == "/api/jobs":
            payload = api_dashboard()
            self._send_json(200, payload["today_top"] + payload["qualified"]); return
        if u.path == "/api/profile":
            self._send_json(200, get_profile_view()); return
        if u.path == "/api/preferences":
            self._send_json(200, get_preferences_view()); return
        if u.path == "/api/profile/resume/status":
            self._send_json(200, get_profile_view()); return
        if u.path.startswith("/api/jobs/"):
            parts = [x for x in u.path.split("/") if x]
            job_id = parts[2] if len(parts) == 3 and parts[:2] == ["api", "jobs"] else ""
            if not job_id:
                self._send_json(400, {"ok": False, "error": "missing_job_id"}); return
            data = job_detail_payload(job_id)
            self._send_json(200 if data else 404, data or {"ok": False, "error": "job_not_found"}); return
        static = _safe_static(u.path)
        if static:
            body = static.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(str(static))[0] or "application/octet-stream")
            self.send_header("Cache-Control", "no-cache" if static.suffix in {".html", ".js", ".css"} else "public, max-age=3600")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body); return
        self._send_json(404, {"ok": False, "error": "not_found"})

    def do_POST(self):
        u = urlparse(self.path)
        parts = [x for x in u.path.split("/") if x]
        try:
            body = self._read_json()
            if parts == ["api", "profile", "resume"]:
                result = upload_resume(str(body.get("filename") or "resume.pdf"), str(body.get("data_base64") or ""))
                self._send_json(200, {"ok": True, **result}); return
            if parts == ["api", "profile", "supplement"]:
                result = save_supplement(str(body.get("text") or ""))
                self._send_json(200, {"ok": True, **result}); return
            if parts == ["api", "preferences"]:
                result = save_preferences(str(body.get("text") or ""))
                self._send_json(200, {"ok": True, **result}); return
            if len(parts) != 4 or parts[0] != "api" or parts[1] != "jobs":
                self._send_json(404, {"ok": False, "error": "not_found"}); return
            job_id, action = parts[2], parts[3]
            if action == "open":
                result = action_open(job_id); status = 202 if result.get("ok") else 409
            elif action == "apply":
                result = action_apply(job_id); status = 202 if result.get("ok") else 409
            elif action == "verify":
                result = action_verify(job_id); status = 202 if result.get("ok") and result.get("status") == "verifying" else (200 if result.get("ok") else 409)
            elif action == "hold":
                result = set_ui_state(job_id, "hold", str(body.get("note") or "")); status = 200
            elif action == "skip":
                result = set_ui_state(job_id, "skip", str(body.get("note") or "")); status = 200
            elif action == "restore":
                result = set_ui_state(job_id, "active", str(body.get("note") or "")); status = 200
            elif action == "favorite":
                result = set_favorite(job_id, bool(body.get("favorite", True))); status = 200
            else:
                self._send_json(404, {"ok": False, "error": "unknown_action"}); return
            self._send_json(status, result)
        except KeyError:
            self._send_json(404, {"ok": False, "error": "job_not_found"})
        except ValueError as e:
            self._send_json(400, {"ok": False, "error": str(e)})
        except Exception as e:
            self._send_json(500, {"ok": False, "error": str(e)[:1000]})

    def log_message(self, *_):
        pass


def main() -> None:
    print(f"Job Agent Dashboard: http://{HOST}:{PORT}")
    print("API: /api/dashboard | /api/jobs/{id} | /api/profile | /api/preferences")
    DashboardHTTPServer((HOST, PORT), H).serve_forever(poll_interval=0.35)


if __name__ == "__main__":
    main()
