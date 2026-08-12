from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from config_loader import parse_txt
from workflow_db import ROOT, TZ, now_iso

LEGACY_PROFILE = ROOT / "我的资料.txt"
PREFERENCES_TXT = ROOT / "求职要求.txt"
SUPPLEMENT_TXT = ROOT / "补充资料.txt"
PROFILE_DIR = ROOT / "data" / "profile"
RESUME_DIR = PROFILE_DIR / "resume"
HISTORY_DIR = PROFILE_DIR / "history"
PROFILE_JSON = PROFILE_DIR / "profile.json"
AGENT_REQUEST = PROFILE_DIR / "agent_request.json"
CURRENT_RESUME = RESUME_DIR / "current.pdf"
MAX_RESUME_BYTES = 12 * 1024 * 1024


def _ensure_dirs() -> None:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    RESUME_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)


def _stamp() -> str:
    return datetime.now(TZ).strftime("%Y%m%d_%H%M%S_%f")[:-3]


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "_", Path(name or "resume.pdf").name).strip("._")
    if not cleaned.lower().endswith(".pdf"):
        cleaned += ".pdf"
    return cleaned or "resume.pdf"


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    _atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _backup(path: Path, label: str | None = None) -> str | None:
    if not path.exists():
        return None
    _ensure_dirs()
    suffix = path.suffix or ".txt"
    target = HISTORY_DIR / f"{_stamp()}_{label or path.stem}{suffix}"
    shutil.copy2(path, target)
    return str(target)


def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
    except Exception:
        return default


def _section_lines(section: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key, value in section.items():
        if key == "_lines":
            out.extend(str(x).strip() for x in value or [] if str(x).strip())
        elif not key.startswith("_"):
            out.append(f"{key}：{value}")
    return out


def _initial_supplement() -> str:
    if SUPPLEMENT_TXT.exists():
        return SUPPLEMENT_TXT.read_text(encoding="utf-8-sig")
    parsed = parse_txt(LEGACY_PROFILE)
    sections = parsed.get("sections", {})
    blocks = [
        "# 这里由前端“补充资料”维护。可以自由增删，但建议保留分组。",
        "# 保存后系统会自动重建兼容版 我的资料.txt，并保留历史版本。",
        "",
    ]
    mapping = [
        ("技能与能力", "技能与能力"),
        ("项目经历", "项目经历"),
        ("可证明经历/作品", "可证明经历/作品"),
        ("绝对不能虚构", "绝对不能虚构"),
        ("其他说明", "其他说明"),
    ]
    for source, target in mapping:
        blocks.append(f"[{target}]")
        lines = _section_lines(sections.get(source, {}))
        blocks.extend(lines)
        blocks.append("")
    text = "\n".join(blocks).rstrip() + "\n"
    _atomic_text(SUPPLEMENT_TXT, text)
    return text


def ensure_profile_store() -> dict[str, Any]:
    _ensure_dirs()
    supplement = _initial_supplement()
    if PROFILE_JSON.exists():
        data = _read_json(PROFILE_JSON, {})
        if data:
            return data
    parsed = parse_txt(LEGACY_PROFILE)
    basic = parsed.get("sections", {}).get("基本信息", {})
    profile = {
        "version": 1,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "base_basic": {k: v for k, v in basic.items() if not k.startswith("_")},
        "resume": {
            "status": "not_uploaded",
            "filename": None,
            "uploaded_at": None,
            "sha256": None,
            "agent_status": "not_requested",
        },
        "supplement": {"updated_at": now_iso(), "text_chars": len(supplement)},
    }
    _atomic_json(PROFILE_JSON, profile)
    return profile


def _dedupe(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        value = str(item).strip()
        key = value.lower()
        if value and key not in seen:
            seen.add(key)
            out.append(value)
    return out


def _supplement_sections() -> dict[str, dict[str, Any]]:
    return parse_txt(SUPPLEMENT_TXT).get("sections", {})


def rebuild_legacy_profile(*, backup: bool = True) -> dict[str, Any]:
    profile = ensure_profile_store()
    if backup:
        _backup(LEGACY_PROFILE, "我的资料")
    base = dict(profile.get("base_basic") or {})
    resume = dict(profile.get("resume") or {})
    basic_keys = ["姓名", "当前城市", "目标期望城市", "最高学历", "毕业年份", "正式工作年限", "邮箱", "电话"]
    sections = _supplement_sections()
    skills = _dedupe(list(sections.get("技能与能力", {}).get("_lines", []) or []))
    projects = sections.get("项目经历", {})
    proof = _section_lines(sections.get("可证明经历/作品", {}))
    never = _section_lines(sections.get("绝对不能虚构", {}))
    other = _section_lines(sections.get("其他说明", {}))

    lines: list[str] = [
        "# 此文件由前端 Profile Builder 自动生成。",
        "# 用户入口：上传简历 PDF / 补充资料；PDF 仅保存源文件，由本地 Agent 结合求职要求处理。", 
        "# 每次改动会自动备份到 data/profile/history。",
        "",
        "[基本信息]",
    ]
    for key in basic_keys:
        lines.append(f"{key}：{base.get(key) or ''}")
    lines += ["", "[技能与能力]"] + skills + ["", "[项目经历]"]
    for key, value in projects.items():
        if not key.startswith("_"):
            lines.append(f"{key}：{value}")
    lines += ["", "[可证明经历/作品]"] + proof
    lines += ["", "[绝对不能虚构]"] + never
    lines += ["", "[其他说明]"] + other

    _atomic_text(LEGACY_PROFILE, "\n".join(lines).rstrip() + "\n")
    profile["updated_at"] = now_iso()
    profile["legacy_profile_updated_at"] = now_iso()
    _atomic_json(PROFILE_JSON, profile)
    return get_profile_view()


def upload_resume(filename: str, data_base64: str) -> dict[str, Any]:
    _ensure_dirs()
    ensure_profile_store()
    try:
        raw = base64.b64decode(data_base64, validate=True)
    except Exception as e:
        raise ValueError("PDF 数据不是有效的 base64") from e
    if not raw.startswith(b"%PDF"):
        raise ValueError("仅支持真实 PDF 文件")
    if len(raw) > MAX_RESUME_BYTES:
        raise ValueError("PDF 不能超过 12MB")
    safe = _safe_filename(filename)
    if CURRENT_RESUME.exists():
        old = HISTORY_DIR / f"{_stamp()}_resume.pdf"
        shutil.copy2(CURRENT_RESUME, old)
    CURRENT_RESUME.write_bytes(raw)
    profile = ensure_profile_store()
    profile["resume"] = {
        "status": "source_saved",
        "filename": safe,
        "uploaded_at": now_iso(),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "agent_status": "pending",
    }
    profile["updated_at"] = now_iso()
    _atomic_json(PROFILE_JSON, profile)
    request = {
        "status": "pending",
        "created_at": now_iso(),
        "resume_path": str(CURRENT_RESUME),
        "resume_filename": safe,
        "profile_path": str(LEGACY_PROFILE),
        "supplement_path": str(SUPPLEMENT_TXT),
        "preferences_path": str(PREFERENCES_TXT),
        "instruction": "先完整阅读当前个人资料、补充资料和求职要求，再读取源 PDF 做候选人画像整理。PDF 仅作为源证据，不使用前端/后端机械提取文本作为正式资料；不得虚构缺失事实。完成后更新结构化个人资料/补充资料，再重建兼容版 我的资料.txt。", 
    }
    _atomic_json(AGENT_REQUEST, request)
    return get_profile_view()


def save_supplement(text: str) -> dict[str, Any]:
    if len(text) > 120000:
        raise ValueError("补充资料过长")
    ensure_profile_store()
    _backup(SUPPLEMENT_TXT, "补充资料")
    _atomic_text(SUPPLEMENT_TXT, text.rstrip() + "\n")
    profile = ensure_profile_store()
    profile["supplement"] = {"updated_at": now_iso(), "text_chars": len(text)}
    profile["updated_at"] = now_iso()
    _atomic_json(PROFILE_JSON, profile)
    return rebuild_legacy_profile(backup=True)


def save_preferences(text: str) -> dict[str, Any]:
    if not text.strip():
        raise ValueError("求职要求不能为空")
    if len(text) > 100000:
        raise ValueError("求职要求过长")
    _backup(PREFERENCES_TXT, "求职要求")
    _atomic_text(PREFERENCES_TXT, text.rstrip() + "\n")
    return get_preferences_view()


def get_preferences_view() -> dict[str, Any]:
    text = PREFERENCES_TXT.read_text(encoding="utf-8-sig") if PREFERENCES_TXT.exists() else ""
    parsed = parse_txt(PREFERENCES_TXT)
    return {
        "raw_text": text,
        "sections": parsed.get("sections", {}),
        "updated_at": datetime.fromtimestamp(PREFERENCES_TXT.stat().st_mtime, TZ).isoformat() if PREFERENCES_TXT.exists() else None,
        "note": "保存后用于后续筛选；只有用户明确要求重新筛选时才重算既有岗位。", 
    }


def get_profile_view() -> dict[str, Any]:
    profile = ensure_profile_store()
    resume = dict(profile.get("resume") or {})
    resume_exists = CURRENT_RESUME.exists()
    # 源文件是最终事实：即使旧 profile.json 状态没及时同步，也不能让前端误报“未上传”。
    if resume_exists and resume.get("status") != "source_saved":
        resume["status"] = "source_saved"
        resume.setdefault("filename", "current.pdf")
        resume.setdefault("uploaded_at", datetime.fromtimestamp(CURRENT_RESUME.stat().st_mtime, TZ).isoformat())
        resume.setdefault("sha256", hashlib.sha256(CURRENT_RESUME.read_bytes()).hexdigest())
        resume.setdefault("agent_status", "pending")
        profile["resume"] = resume
        profile["updated_at"] = now_iso()
        _atomic_json(PROFILE_JSON, profile)
    supplement = SUPPLEMENT_TXT.read_text(encoding="utf-8-sig") if SUPPLEMENT_TXT.exists() else ""
    history = sorted(HISTORY_DIR.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
    request = _read_json(AGENT_REQUEST, {})
    return {
        "profile": profile | {"resume": resume},
        "supplement_text": supplement,
        "resume_uploaded": resume_exists or resume.get("status") == "source_saved",
        "resume_exists": resume_exists,
        "resume_agent_status": request.get("status") or resume.get("agent_status") or "not_requested",
        "history_count": len(history),
        "legacy_profile_path": str(LEGACY_PROFILE),
        "supplement_path": str(SUPPLEMENT_TXT),
        "agent_request_path": str(AGENT_REQUEST),
    }
