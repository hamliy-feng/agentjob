from __future__ import annotations

import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "dashboard.py"
LOG_PATH = ROOT / "data" / "dashboard_service.log"
HEALTH_URL = "http://127.0.0.1:8799/api/health"
GUARD_ADDR = ("127.0.0.1", 18799)


def log(message: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(f"[{stamp}] {message}\n")


def healthy(timeout: float = 0.8) -> bool:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def spawn_dashboard() -> subprocess.Popen:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    stream = LOG_PATH.open("a", encoding="utf-8")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(
        [sys.executable, str(DASHBOARD)],
        cwd=str(ROOT),
        stdout=stream,
        stderr=stream,
        creationflags=flags,
    )
    stream.close()
    log(f"spawn dashboard pid={proc.pid}")
    return proc


def main() -> int:
    guard = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    guard.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        guard.bind(GUARD_ADDR)
        guard.listen(1)
    except OSError:
        # 已有 watchdog 在运行；重复启动直接退出。
        return 0

    log("watchdog started")
    proc: subprocess.Popen | None = None
    unhealthy_streak = 0

    while True:
        if healthy():
            unhealthy_streak = 0
            time.sleep(2.0)
            continue

        unhealthy_streak += 1
        if proc is not None and proc.poll() is None and unhealthy_streak < 3:
            time.sleep(1.0)
            continue

        if proc is not None and proc.poll() is None:
            log(f"dashboard unhealthy pid={proc.pid}; restarting")
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

        proc = spawn_dashboard()
        unhealthy_streak = 0
        # 给 dashboard 启动时间；失败时下一轮会再次拉起。
        for _ in range(12):
            if healthy():
                log(f"dashboard healthy pid={proc.pid}")
                break
            if proc.poll() is not None:
                log(f"dashboard exited code={proc.returncode}")
                break
            time.sleep(0.5)
        time.sleep(1.0)


if __name__ == "__main__":
    raise SystemExit(main())
