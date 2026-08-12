from __future__ import annotations

import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT.parent / "browser-profile"
ENDPOINT = "http://127.0.0.1:9227/json/version"
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
EDGE_64 = Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe")
EDGE_32 = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")


def online() -> bool:
    try:
        with urllib.request.urlopen(ENDPOINT, timeout=0.8) as r:
            return r.status == 200
    except Exception:
        return False


def main() -> int:
    if online():
        print("9227 already online")
        return 0
    exe = CHROME if CHROME.exists() else EDGE_64 if EDGE_64.exists() else EDGE_32 if EDGE_32.exists() else None
    if exe is None:
        print("Chrome/Edge not found")
        return 1
    PROFILE.mkdir(parents=True, exist_ok=True)
    flags = (
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        | getattr(subprocess, "DETACHED_PROCESS", 0)
        | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
    )
    subprocess.Popen(
        [
            str(exe),
            "--remote-debugging-port=9227",
            f"--user-data-dir={PROFILE}",
            "--no-first-run",
            "--no-default-browser-check",
            "https://www.zhipin.com/",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
        close_fds=True,
    )
    for i in range(24):
        time.sleep(0.5)
        if online():
            print("9227 online", "after_seconds", round((i + 1) * 0.5, 1), "profile", PROFILE)
            return 0
    print("9227 failed to start")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
