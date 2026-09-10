"""IDOR Workbench 的 HTTP 入口与应用编排层。

设计边界：
1. FastAPI 负责项目配置、文件导入、AI 分析、任务调度和产物下载。
2. 真正执行接口测试的逻辑在 idor_workbench.domains.idor.execution。
3. runner 接收已加载的 project dict，不再反向依赖本地 project.json 作为唯一配置源。
4. SQLite 保存可检索元数据；大文件、报告、截图继续放在 data/projects/<project_id>/。

主业务链路：认证用户 -> 导入证据 -> 保存项目 -> 生成/审查计划 -> 创建后台任务
-> 领域引擎执行 -> 归档结果 -> 人工复核/报告/历史对比。路由函数负责鉴权和
输入输出适配；真正的请求执行、断言、场景、录制和报告分别位于 domains/idor。
"""
from __future__ import annotations

import csv
import hashlib
import hmac
import json
import os
import re
import secrets
import shlex
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urljoin, urlparse

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from idor_workbench.domains.idor.findings import (
    close_finding_by_id,
    findings_to_dicts,
    load_findings,
    mark_finding_retested_by_id,
    mark_finding_waiting_fix_by_id,
    review_finding_by_id,
)

from idor_workbench.domains.idor.ai import (
    analyze_config as ai_analyze_config_core,
)
from idor_workbench.domains.idor.ai import (
    analyze_import as ai_analyze_import_core,
)
from idor_workbench.domains.idor.ai import (
    analyze_plan as ai_analyze_plan_core,
)
from idor_workbench.domains.idor.ai import (
    analyze_run as ai_analyze_run_core,
)
from idor_workbench.domains.idor.application import PROJECTS_DIR, ROOT_DIR, STATIC_DIR, WorkbenchDB, get_settings
from idor_workbench.domains.idor.assertions import apply_ai_tuning
from idor_workbench.domains.idor.page_mapping import page_url_for_endpoint
from idor_workbench.domains.idor.scope import apply_goal_scope_to_project

ROOT = ROOT_DIR
DATA_DIR = PROJECTS_DIR
DB_PATH = ROOT / "data" / "workbench.sqlite3"
SETTINGS = get_settings()
TASK_WORKERS = SETTINGS.task_workers
AI_TASK_WORKERS = SETTINGS.ai_task_workers

DATA_DIR.mkdir(parents=True, exist_ok=True)
DB = WorkbenchDB(DB_PATH)
TASK_EXECUTOR = ThreadPoolExecutor(max_workers=TASK_WORKERS)
AI_TASK_EXECUTOR = ThreadPoolExecutor(max_workers=AI_TASK_WORKERS)
TASK_LOCK = threading.Lock()
TASKS: dict[str, dict[str, Any]] = {}
MANUAL_RECORDINGS: dict[str, Any] = {}
MANUAL_RECORDING_LOCK = threading.Lock()
SESSION_COOKIE = "idor_session"

app = FastAPI(title="IDOR Test Workbench", version="0.2.0")
app.add_middleware(CORSMiddleware, allow_origins=SETTINGS.cors_origins, allow_methods=["*"], allow_headers=["*"])


# ---------------------------------------------------------------------------
# 通用安全与文件助手：所有用户输入在接触文件系统前都必须经过这一层。
# ---------------------------------------------------------------------------


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def safe_text(value: Any, limit: int = 1000) -> str:
    text = "" if value is None else str(value)
    cleaned = []
    for ch in text:
        code = ord(ch)
        cleaned.append(ch if ch in "\n\r\t" or code >= 32 else " ")
    return "".join(cleaned)[:limit]


def _safe_project_id(name: str | None) -> str:
    base = re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]+", "-", (name or "").strip()).strip("-")
    return f"{base or 'project'}-{uuid.uuid4().hex[:8]}"


def _safe_id(value: str, label: str = "id") -> str:
    """校验服务端能力 ID，拒绝斜杠和 ``..`` 等路径穿越表达。"""
    if not re.fullmatch(r"[0-9A-Za-z._\-\u4e00-\u9fff]+", value or ""):
        raise HTTPException(status_code=400, detail=f"Invalid {label}")
    return value


def _project_dir(project_id: str) -> Path:
    """返回校验后的项目目录；这里只保证路径安全，不代表调用者已经获得授权。"""
    return DATA_DIR / _safe_id(project_id, "project id")


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    # PowerShell and some export tools write UTF-8 with BOM. Project
    # artifacts must remain readable regardless of that harmless marker.
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _relative_to_root(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.name


def _is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _resolve_source_path(stored_path: str) -> Path:
    """把数据库相对路径解析到工作区，并确保最终路径没有逃逸出工作区根目录。"""
    candidate = (ROOT / stored_path).resolve()
    root = ROOT.resolve()
    if candidate != root and root not in candidate.parents:
        raise HTTPException(status_code=400, detail="Invalid source path")
    return candidate


def _source_file_meta(kind: str, original_name: str, path: Path) -> dict[str, Any]:
    return {
        "source_id": f"{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}",
        "kind": kind,
        "original_name": original_name,
        "stored_path": _relative_to_root(path),
        "size": path.stat().st_size if path.exists() else 0,
        "created_at": now_text(),
    }


def _content_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _store_upload_file(
    kind: str,
    file: UploadFile,
    owner_user_id: str,
    max_bytes: int,
) -> tuple[Path, dict[str, Any]]:
    """以流式、限量、用户隔离的方式保存一个上传文件。

    内容哈希只是证据完整性元数据，不是全局身份。两个用户上传完全相同的字节也会
    获得独立 source_id 和物理文件，避免删除、归档或项目采用动作互相影响。
    读取时按 1 MiB 分块累计，超过 ``max_bytes`` 立即删除半成品并返回 413。
    """
    owner_user_id = _safe_id(owner_user_id, "owner user id")
    upload_dir = ROOT / "data" / "uploads" / owner_user_id / kind
    upload_dir.mkdir(parents=True, exist_ok=True)
    original_name = file.filename or f"{kind}-upload"
    safe_name = re.sub(r"[^0-9A-Za-z_.\-\u4e00-\u9fff]+", "-", original_name).strip("-") or f"{kind}-upload"
    temp_path = upload_dir / f"{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}-{safe_name}"
    total = 0
    try:
        with temp_path.open("wb") as fh:
            while chunk := file.file.read(1024 * 1024):
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(status_code=413, detail=f"Upload exceeds {max_bytes} bytes")
                fh.write(chunk)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise

    digest = _content_sha256(temp_path)
    target = upload_dir / f"{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}-{safe_name}"
    temp_path.replace(target)

    source_file = _source_file_meta(kind, original_name, target)
    source_file["owner_user_id"] = owner_user_id
    source_file["content_sha256"] = digest
    return target, source_file


def _delete_stored_source_file(stored_path: str) -> None:
    if not stored_path:
        return
    path = _resolve_source_path(stored_path)
    if path.exists() and path.is_file():
        path.unlink()


def _archive_project_sources(project_id: str, source_files: list[dict[str, Any]], owner_user_id: str) -> list[dict[str, Any]]:
    """把临时上传复制进项目证据目录，并同步数据库中的最终归属和路径。"""
    folder = _project_dir(project_id)
    source_dir = folder / "sources"
    source_dir.mkdir(parents=True, exist_ok=True)
    archived = []
    for source in source_files or []:
        stored = str(source.get("stored_path") or "")
        if not stored:
            continue
        src = _resolve_source_path(stored)
        if not src.exists():
            continue
        safe_name = re.sub(r"[^0-9A-Za-z_.\-\u4e00-\u9fff]+", "-", source.get("original_name") or src.name).strip("-") or src.name
        target = source_dir / f"{source.get('source_id') or uuid.uuid4().hex[:8]}-{safe_name}"
        if src.resolve() != target.resolve():
            shutil.copy2(src, target)
            if _is_under(src, ROOT / "data" / "uploads"):
                src.unlink()
        item = dict(source)
        item.update({
            "stored_path": _relative_to_root(target),
            "size": target.stat().st_size,
            "project_id": project_id,
            "owner_user_id": owner_user_id,
        })
        archived.append(item)
        DB.upsert_source_file(item, project_id=project_id)
    return archived


def _sync_project_metadata(project_id: str, project: dict[str, Any]) -> None:
    """补齐项目 ID/更新时间后同时刷新 SQLite 项目快照。"""
    project.setdefault("project_id", project_id)
    DB.upsert_project(project)
    for source in project.get("source_files") or []:
        if isinstance(source, dict) and source.get("source_id") and source.get("stored_path"):
            DB.upsert_source_file(source, project_id=project_id)


def _server_ai_config() -> dict[str, Any]:
    """读取服务端 AI 默认配置；用户配置可在后续请求级合并中覆盖。"""
    return {
        "provider": os.environ.get("IDOR_AI_PROVIDER", "disabled"),
        "base_url": os.environ.get("IDOR_AI_BASE_URL", ""),
        "model": os.environ.get("IDOR_AI_MODEL", ""),
        "api_key": os.environ.get("IDOR_AI_API_KEY", ""),
        "timeout": os.environ.get("IDOR_AI_TIMEOUT", "600"),
        "temperature": os.environ.get("IDOR_AI_TEMPERATURE", "0.2"),
        "sync_model": True,
    }


def _hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000).hex()
    return f"pbkdf2_sha256${salt}${digest}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, digest = stored.split("$", 2)
    except ValueError:
        return False
    return hmac.compare_digest(_hash_password(password, salt).split("$", 2)[2], digest)


def _public_user(user: dict[str, Any] | None) -> dict[str, Any] | None:
    if not user:
        return None
    return {"user_id": user.get("user_id"), "username": user.get("username"), "created_at": user.get("created_at")}


def _current_user(request: Request) -> dict[str, Any] | None:
    """从 HttpOnly 会话 Cookie 恢复当前用户；匿名请求返回 ``None``。"""
    token = request.cookies.get(SESSION_COOKIE, "")
    return DB.get_session_user(token) if token else None


def _require_user(request: Request) -> dict[str, Any]:
    """认证依赖：匿名访问受保护能力时统一返回 401。"""
    user = _current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")
    return user


def _require_owned_project(request: Request, project_id: str) -> dict[str, Any]:
    """Authorize every project-scoped operation before touching its files.

    Project artifacts contain test accounts, captured requests and execution
    evidence. Knowing a project id must never be sufficient to access them.
    """
    user = _require_user(request)
    project_id = _safe_id(project_id, "project id")
    if not DB.project_owned_by(project_id, user["user_id"]):
        # Do not reveal whether another user's project id exists.
        raise HTTPException(status_code=404, detail="Project not found")
    return user


def _require_payload_project_access(request: Request, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Require login for AI/import work and validate an optional project id."""
    user = _require_user(request)
    payload = payload or {}
    submitted_project = payload.get("project")
    project = submitted_project if isinstance(submitted_project, dict) else payload
    project_id = safe_text(payload.get("project_id") or project.get("project_id"), 160).strip()
    if project_id and not DB.project_owned_by(_safe_id(project_id, "project id"), user["user_id"]):
        raise HTTPException(status_code=404, detail="Project not found")
    return user


def _canonicalize_project_sources(project: dict[str, Any], user_id: str) -> list[dict[str, Any]]:
    """Use server-issued source metadata instead of browser-supplied paths.

    ``project`` is an HTTP payload. Its ``stored_path`` must not be trusted,
    otherwise a crafted save request could copy arbitrary workspace files into
    a project. Source ids are checked as user-owned server capabilities.
    """
    sources: list[dict[str, Any]] = []
    for source in project.get("source_files") or []:
        source_id = safe_text(source.get("source_id") if isinstance(source, dict) else "", 160).strip()
        if not source_id:
            raise HTTPException(status_code=400, detail="Source file is missing source_id")
        source_id = _safe_id(source_id, "source id")
        stored = DB.get_source_file(source_id)
        if not stored or not DB.source_file_accessible_by(source_id, user_id):
            raise HTTPException(status_code=404, detail="Source file not found")
        canonical = dict(stored)
        if isinstance(source, dict) and source.get("source_role"):
            canonical["source_role"] = safe_text(source["source_role"], 80)
        sources.append(canonical)
    return sources


# ---------------------------------------------------------------------------
# AI 配置合并：请求临时值 > 当前用户已保存值 > 服务端环境变量默认值。
# 浏览器没有再次提交 API Key 时保留已保存值，但任何响应都只返回 has_api_key。
# ---------------------------------------------------------------------------


def _mask_ai_settings(settings: dict[str, Any] | None) -> dict[str, Any]:
    settings = dict(settings or {})
    api_key = settings.pop("api_key", "")
    settings["has_api_key"] = bool(api_key)
    return settings


def _sanitize_ai_settings(raw: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    previous = previous or {}
    provider = str(raw.get("provider") or previous.get("provider") or "disabled").strip().lower()
    if provider not in {"disabled", "ollama", "openai", "openai-compatible"}:
        provider = "openai-compatible"
    settings = {
        "provider": provider,
        "base_url": safe_text(raw.get("base_url") or previous.get("base_url") or "", 500).strip().rstrip("/"),
        "model": safe_text(raw.get("model") or previous.get("model") or "", 200).strip(),
        "timeout": str(raw.get("timeout") or previous.get("timeout") or os.environ.get("IDOR_AI_TIMEOUT", "600")),
        "temperature": str(raw.get("temperature") or previous.get("temperature") or "0.2"),
        "sync_model": bool(raw.get("sync_model", previous.get("sync_model", True))),
    }
    if "api_key" in raw:
        incoming_key = str(raw.get("api_key") or "")
        if incoming_key:
            settings["api_key"] = incoming_key
        elif raw.get("clear_api_key"):
            settings["api_key"] = ""
        else:
            settings["api_key"] = previous.get("api_key", "")
    else:
        settings["api_key"] = previous.get("api_key", "")
    if settings["provider"] == "openai":
        settings["provider"] = "openai-compatible"
    return settings


def _ai_config_for_request(request: Request, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """构造本次调用使用的 AI 配置，不把其他用户或服务端密钥暴露给浏览器。"""
    payload = payload or {}
    user = _current_user(request)
    # 前端传了 ai_config 时，用它覆盖 DB 保存的配置（比如用户在测试新的 API Key）
    incoming = payload.get("ai_config")
    if isinstance(incoming, dict) and incoming.get("provider") != "server":
        # The submitted configuration is the configuration for this request.
        # Keep only a saved key when the browser intentionally leaves it blank.
        saved = DB.get_user_ai_settings(user["user_id"]) if user else None
        incoming = _sanitize_ai_settings(incoming, saved or {})
        incoming["sync_model"] = bool(incoming.get("sync_model", True))
        if user:
            if saved:
                if not incoming.get("api_key"):
                    incoming["api_key"] = saved.get("api_key", "")
        return incoming
    if user:
        saved = DB.get_user_ai_settings(user["user_id"])
        if saved:
            saved.setdefault("sync_model", True)
            return saved
    return _server_ai_config()


# ---------------------------------------------------------------------------
# 证据解析与计划生成：不同来源先标准化为统一 Endpoint，再生成角色权限用例。
# ---------------------------------------------------------------------------


def _roles_from_project(project: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for role in project.get("roles") or []:
        if isinstance(role, dict) and role.get("name"):
            names.append(str(role["name"]))
    return names


def _role_key(role: dict[str, Any]) -> tuple[str, str] | None:
    username = safe_text(role.get("username"), 160).strip()
    password = safe_text(role.get("password"), 160)
    if not username or not password:
        return None
    return username, password


def _role_reference_map(old_project: dict[str, Any] | None, new_project: dict[str, Any]) -> dict[str, str]:
    """Map stale role references to current role names by account credentials."""
    mapping: dict[str, str] = {}
    old_by_key = {
        key: role
        for role in (old_project or {}).get("roles") or []
        if isinstance(role, dict) and (key := _role_key(role))
    }
    for role in new_project.get("roles") or []:
        if not isinstance(role, dict):
            continue
        new_name = safe_text(role.get("name"), 80).strip()
        if not new_name:
            continue
        for value in (role.get("name"), role.get("username")):
            text = safe_text(value, 160).strip()
            if text:
                mapping[text] = new_name
        old_role = old_by_key.get(_role_key(role))
        if old_role:
            for value in (old_role.get("name"), old_role.get("username")):
                text = safe_text(value, 160).strip()
                if text:
                    mapping[text] = new_name
    return {old: new for old, new in mapping.items() if old and new and old != new}


def _replace_role_list(values: Any, role_map: dict[str, str]) -> list[str]:
    return list(dict.fromkeys(role_map.get(str(value), str(value)) for value in (values or []) if value))


def _role_name_by_username(project: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for role in project.get("roles") or []:
        if not isinstance(role, dict):
            continue
        username = safe_text(role.get("username"), 160).strip()
        name = safe_text(role.get("name"), 80).strip()
        if username and name:
            result[username] = name
    return result


def _roles_from_source_usernames(item: dict[str, Any], username_to_role: dict[str, str]) -> list[str]:
    usernames = []
    if item.get("source_username"):
        usernames.append(item["source_username"])
    usernames.extend(item.get("source_usernames") or [])
    return list(dict.fromkeys(username_to_role[name] for name in usernames if name in username_to_role))


def _sync_role_references(project: dict[str, Any], old_project: dict[str, Any] | None = None) -> None:
    role_map = _role_reference_map(old_project, project)
    username_to_role = _role_name_by_username(project)
    if not role_map and not username_to_role:
        return
    for item in [*(project.get("endpoints") or []), *(project.get("page_routes") or [])]:
        for field in ("allowed_roles", "observed_roles", "discovered_by", "source_roles"):
            if field in item:
                item[field] = _replace_role_list(item.get(field), role_map)
        source_roles = _roles_from_source_usernames(item, username_to_role)
        if source_roles:
            for field in ("allowed_roles", "observed_roles", "discovered_by", "source_roles"):
                if field in item:
                    item[field] = list(dict.fromkeys([*(item.get(field) or []), *source_roles]))
            item["source_role"] = source_roles[0]
        if item.get("source_role"):
            item["source_role"] = role_map.get(str(item["source_role"]), item["source_role"])
    for case in ((project.get("testcases") or {}).get("cases") or []):
        if "roles" in case:
            case["roles"] = _replace_role_list(case.get("roles"), role_map)
    for source in project.get("source_files") or []:
        if source.get("source_role"):
            source["source_role"] = role_map.get(str(source["source_role"]), source["source_role"])


def _roles_from_payload(payload: dict[str, Any] | None = None, project_id: str | None = None) -> list[str]:
    payload = payload or {}
    raw_project = payload.get("project")
    project: dict[str, Any] = dict(raw_project) if isinstance(raw_project, dict) else {}
    roles = _roles_from_project(project)
    if not roles and isinstance(payload.get("roles"), list):
        roles = [str(role.get("name")) if isinstance(role, dict) else str(role) for role in payload["roles"] if role and (not isinstance(role, dict) or role.get("name"))]
    if not roles and project_id:
        loaded_project = DB.get_project(project_id) or _load_json(_project_dir(project_id) / "project.json", {})
        stored_project: dict[str, Any] = dict(loaded_project) if isinstance(loaded_project, dict) else {}
        roles = _roles_from_project(stored_project)
    return roles or ["待确认"]


def _guess_module(path: str) -> str:
    parts = [p for p in str(path).split("/") if p and p not in {"api", "v1", "v2"}]
    return parts[0] if parts else "default"


def _endpoint_from_path(path: str, method: str, roles: list[str], name: str | None = None, source_role: str | None = None) -> dict[str, Any]:
    """把最小的 method/path 证据扩展为工作台统一 Endpoint 数据结构。"""
    observed = [source_role] if source_role else []
    allowed = observed or roles[:1]
    endpoint: dict[str, Any] = {
        "name": name or path.strip("/").replace("/", "-") or "未命名接口",
        "description": "",
        "path": path,
        "method": method.upper(),
        "params": {},
        "headers": {},
        "body": None,
        "sample_response": {},
        "module": _guess_module(path),
        "allowed_roles": allowed,
        "observed_roles": observed,
        "discovered_by": observed,
        "risk": "中",
        "operation": "查询" if method.upper() == "GET" else "写入",
        "page_url": "",
        "needs_review": bool(source_role),
        "confidence": "role-observed" if source_role else "heuristic",
    }
    page_url, inferred = page_url_for_endpoint(endpoint)
    endpoint["page_url"] = page_url
    endpoint["page_url_inferred"] = inferred
    return endpoint


def _extract_body(text: Any) -> Any:
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        return json.loads(text)
    except ValueError:
        return text[:4000]


def _dedupe_endpoints(endpoints: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 method+path 合并重复接口，同时保留角色、页面和来源章节的并集证据。"""
    result: dict[str, dict[str, Any]] = {}
    for ep in endpoints:
        key = f"{str(ep.get('method') or 'GET').upper()}:{ep.get('path')}"
        if not ep.get("path"):
            continue
        if key not in result:
            result[key] = ep
            continue
        existing = result[key]
        for field in ("allowed_roles", "observed_roles", "discovered_by", "source_sections", "source_pages", "source_page_titles"):
            existing[field] = list(dict.fromkeys([*(existing.get(field) or []), *(ep.get(field) or [])]))
        if not existing.get("source_section") and ep.get("source_section"):
            existing["source_section"] = ep["source_section"]
        if not existing.get("page_url") and ep.get("page_url"):
            existing["page_url"] = ep["page_url"]
            existing["page_url_inferred"] = bool(ep.get("page_url_inferred"))
        existing["needs_review"] = bool(existing.get("needs_review") or ep.get("needs_review"))
    return list(result.values())


def _parse_har(path: Path, roles: list[str], source_role: str | None = None) -> list[dict[str, Any]]:
    """解析浏览器 HAR 请求，过滤无路径项并转成统一 Endpoint。"""
    har = json.loads(path.read_text(encoding="utf-8-sig"))
    endpoints = []
    for entry in ((har.get("log") or {}).get("entries") or []):
        request = entry.get("request") or {}
        response = entry.get("response") or {}
        parsed = urlparse(request.get("url") or "")
        if "/api/" not in parsed.path and not parsed.path.startswith("/api"):
            continue
        ep = _endpoint_from_path(parsed.path, request.get("method", "GET"), roles, source_role=source_role)
        ep["params"] = dict(parse_qsl(parsed.query, keep_blank_values=True))
        for item in request.get("queryString", []) or []:
            if item.get("name"):
                ep["params"][item["name"]] = item.get("value", "")
        ep["headers"] = {
            h.get("name", ""): h.get("value", "")
            for h in request.get("headers", []) or []
            if h.get("name") and h.get("name", "").lower() not in {"authorization", "cookie"}
        }
        ep["body"] = _extract_body((request.get("postData") or {}).get("text"))
        ep["sample_response"] = {"status_code": response.get("status"), "body": _extract_body((response.get("content") or {}).get("text"))}
        ep["source"] = "har"
        endpoints.append(ep)
    return _dedupe_endpoints(endpoints)


def _normalize_url(raw: Any) -> tuple[str, dict[str, str]]:
    if isinstance(raw, dict):
        raw = raw.get("raw") or raw.get("url") or raw.get("href") or "/".join(raw.get("path") or [])
    text = str(raw or "").strip().strip('"').strip("'")
    parsed = urlparse(text)
    if parsed.scheme:
        return parsed.path or "/", dict(parse_qsl(parsed.query, keep_blank_values=True))
    path, _, query = text.partition("?")
    return path if path.startswith("/") else f"/{path}", dict(parse_qsl(query, keep_blank_values=True))


def _endpoint_from_request(raw_url: Any, method: Any, roles: list[str], name: str = "", headers: Any = None, body: Any = None, source: str = "import", source_role: str | None = None) -> dict[str, Any] | None:
    path, params = _normalize_url(raw_url)
    if "/api/" not in path and not path.startswith("/api"):
        return None
    ep = _endpoint_from_path(path, str(method or "GET").upper(), roles, name=name, source_role=source_role)
    ep["params"] = params
    ep["headers"] = headers if isinstance(headers, dict) else {}
    ep["body"] = _extract_body(body) if isinstance(body, str) else body
    ep["source"] = source
    return ep


def _parse_collection_json(path: Path, roles: list[str], source_role: str | None = None) -> list[dict[str, Any]]:
    """递归展开 Postman/同类 Collection 的嵌套 item 并提取请求样例。"""
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    endpoints = []

    def walk(items: list[Any]) -> None:
        for item in items or []:
            if item.get("item"):
                walk(item.get("item") or [])
                continue
            req = item.get("request") or item
            ep = _endpoint_from_request(
                req.get("url") or req.get("path"),
                req.get("method", "GET"),
                roles,
                name=item.get("name", ""),
                headers=req.get("headers") or req.get("header") or {},
                body=((req.get("body") or {}).get("raw") if isinstance(req.get("body"), dict) else req.get("body")),
                source="collection",
                source_role=source_role,
            )
            if ep:
                endpoints.append(ep)

    if isinstance(data.get("item"), list):
        walk(data["item"])
    elif isinstance(data, list):
        walk(data)
    return _dedupe_endpoints(endpoints)


def _parse_curl(text: str, roles: list[str], source_role: str | None = None) -> list[dict[str, Any]]:
    """把一段或多段 cURL 命令解析为 Endpoint；不会执行用户提交的命令。"""
    try:
        parts = shlex.split(text)
    except ValueError:
        parts = text.split()
    method = "GET"
    url = ""
    headers: dict[str, str] = {}
    body = None
    i = 0
    while i < len(parts):
        part = parts[i]
        if part.lower() == "curl":
            i += 1
            continue
        if part in {"-X", "--request"} and i + 1 < len(parts):
            method = parts[i + 1].upper()
            i += 2
            continue
        if part in {"-H", "--header"} and i + 1 < len(parts):
            name, _, value = parts[i + 1].partition(":")
            if name and name.lower() not in {"authorization", "cookie"}:
                headers[name.strip()] = value.strip()
            i += 2
            continue
        if part in {"-d", "--data", "--data-raw", "--data-binary"} and i + 1 < len(parts):
            body = _extract_body(parts[i + 1])
            if method == "GET":
                method = "POST"
            i += 2
            continue
        if part.startswith("http") or part.startswith("/"):
            url = part
        i += 1
    ep = _endpoint_from_request(url, method, roles, headers=headers, body=body, source="curl", source_role=source_role)
    return [ep] if ep else []


def _parse_testcases(path: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    if path.suffix.lower() in {".xlsx", ".xls"}:
        from openpyxl import load_workbook

        ws = load_workbook(path, data_only=True).active
        headers = [safe_text(cell.value, 80) for cell in next(ws.iter_rows(min_row=1, max_row=1))]
        for raw in ws.iter_rows(min_row=2, values_only=True):
            item = {headers[i] or f"col_{i+1}": raw[i] for i in range(min(len(headers), len(raw)))}
            if any(value not in (None, "") for value in item.values()):
                rows.append(item)
    else:
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
    cases = []
    for index, row in enumerate(rows, start=1):
        title = row.get("标题") or row.get("用例标题") or row.get("title") or row.get("name") or f"用例 {index}"
        expected = row.get("预期") or row.get("预期结果") or row.get("expected") or ""
        roles = [safe_text(row.get("角色") or row.get("role") or "", 100)] if (row.get("角色") or row.get("role")) else []
        cases.append({"case_id": row.get("ID") or row.get("id") or str(index), "title": safe_text(title, 300), "expected": safe_text(expected, 500), "roles": roles, "raw": row})
    digest = "\n".join(f"- [{case['case_id']}] {case['title']}\n  预期：{case['expected']}" for case in cases[:30])
    return {"total": len(cases), "cases": cases, "digest": digest}


def _inspect_missing(project: dict[str, Any]) -> list[str]:
    missing = []
    if not project.get("project_name"):
        missing.append("缺少项目名称")
    if not project.get("base_url"):
        missing.append("缺少被测环境地址")
    if not safe_text(project.get("goal"), 1000).strip():
        missing.append("缺少测试目标")
    if len(project.get("roles") or []) < 2:
        missing.append("至少需要两个角色账号")
    if not (project.get("endpoints") or []) and not ((project.get("testcases") or {}).get("cases")):
        missing.append("缺少接口列表或测试用例")
    return missing


def _endpoint_risk(endpoint: dict[str, Any]) -> str:
    """Rule baseline used before AI review; never leave every endpoint at medium."""
    method = str(endpoint.get("method") or "GET").upper()
    text = " ".join(str(endpoint.get(key) or "") for key in ("path", "name", "module", "operation")).lower()
    sensitive = ("user", "role", "permission", "auth", "token", "password", "security", "audit", "blacklist", "export", "download", "import", "mcp", "organization", "staff", "system")
    destructive = ("delete", "remove", "del", "save", "update", "edit", "create", "add", "grant", "reset")
    if any(word in text for word in sensitive) and (method != "GET" or any(word in text for word in destructive)):
        return "高"
    if method in {"POST", "PUT", "PATCH", "DELETE"} or any(word in text for word in sensitive):
        return "中高"
    return "中"


def _make_plan(project: dict[str, Any]) -> dict[str, Any]:
    """根据接口允许角色生成基线权限矩阵，再追加匿名和前端检查用例。

    规则计划是可审计的确定性基线；AI 只能提出受白名单校验的增删改建议，不能绕过
    这里直接生成任意可执行请求。业务目标会先经 ``apply_goal_scope_to_project`` 收窄。
    """
    scoped_project = apply_goal_scope_to_project(project)
    scope = scoped_project.get("plan_scope") or {}
    roles = _roles_from_project(scoped_project)
    endpoints = scoped_project.get("endpoints") or []
    cases = []
    summary = {
        "allowed": 0,
        "role_isolation": 0,
        "vertical": 0,
        "horizontal": 0,
        "anonymous": 0,
        "frontend": 0,
        "testcase": 0,
        "scenario": 0,
    }
    for ep in endpoints:
        ep = {**ep, "risk": _endpoint_risk(ep)}
        allowed = set(ep.get("allowed_roles") or [])
        for role in roles:
            allowed_case = role in allowed
            category = "allowed" if allowed_case else "role_isolation"
            summary[category] += 1
            cases.append(_build_case(role, allowed, ep, allowed_case, category))
        summary["anonymous"] += 1
        cases.append(_build_case("未登录用户", allowed, ep, False, "anonymous"))
        page_url, inferred = page_url_for_endpoint(ep)
        if page_url:
            summary["frontend"] += 1
            cases.append({
                "category": "frontend",
                "type": "前端拦截",
                "method": "PAGE",
                "path": page_url,
                "actor": "未授权角色组合",
                "name": ep.get("name", ""),
                "expected": "前端弹窗拦截，不能进入页面",
                "module": ep.get("module", ""),
                "target": "、".join(allowed) or "未配置",
                "risk": ep.get("risk") or "中",
                "page_url_inferred": inferred,
            })
    for case in ((project.get("testcases") or {}).get("cases") or []):
        case_roles = [role for role in case.get("roles") or [] if role]
        if scope.get("scoped") and case_roles and not set(case_roles) & set(roles):
            continue
        summary["testcase"] += 1
        cases.append({
            "category": "testcase",
            "type": "用例导入",
            "actor": "按用例步骤",
            "target": case.get("title", ""),
            "method": "CASE",
            "path": case.get("title", ""),
            "name": case.get("case_id", ""),
            "expected": case.get("expected", ""),
        })
    return {
        "generated_at": now_text(),
        "objective": {
            "text": safe_text(project.get("goal"), 1000),
            "weight": "high",
            "roles": roles,
            "scope_reason": scope.get("reason", ""),
        },
        "plan_scope": scope,
        "summary": summary,
        "missing": _inspect_missing(project),
        "cases": cases,
    }


def _build_case(actor: str, allowed: set[str], ep: dict[str, Any], allowed_case: bool, category: str) -> dict[str, Any]:
    return {
        "category": category,
        "type": "自身权限" if allowed_case else "角色隔离越权",
        "actor": actor,
        "target": "、".join(allowed) or "未配置",
        "method": ep.get("method", "GET"),
        "path": ep.get("path", ""),
        "name": ep.get("name", ""),
        "module": ep.get("module", ""),
        "source_section": ep.get("source_section", ""),
        "source_sections": ep.get("source_sections") or ([ep["source_section"]] if ep.get("source_section") else []),
        "expected": "允许访问" if allowed_case else "后端拒绝访问，不能返回业务数据",
        "risk": ep.get("risk") or "中",
    }


def _apply_plan_modifications(plan: dict[str, Any], modifications: list[dict[str, Any]]) -> dict[str, Any]:
    """将 AI 返回的 plan_modifications 应用到计划上，返回修改后的计划。"""
    if not modifications:
        return plan
    import copy
    plan = copy.deepcopy(plan)
    cases = plan.setdefault("cases", [])
    applied: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    valid_actors = {
       str(case.get("actor") or "")
       for case in cases
       if str(case.get("method") or "").upper() not in {"CASE", "PAGE"}
   }
    valid_endpoints = {
        (str(case.get("method") or "").upper(), str(case.get("path") or ""))
        for case in cases
        if str(case.get("method") or "").upper() not in {"CASE", "PAGE"}
    }
    existing_case_keys = {
        (str(case.get("actor") or ""), str(case.get("method") or "").upper(), str(case.get("path") or ""))
        for case in cases
        if str(case.get("method") or "").upper() not in {"CASE", "PAGE"}
    }

    for mod in modifications:
        action = mod.get("action", "")
        reason = mod.get("reason", "")
        priority = mod.get("priority", "P2")
        actor = mod.get("actor", "")
        method = (mod.get("method") or "").upper()
        path = mod.get("path", "")

        if action == "add":
            endpoint_key = (method, path)
            if not actor or not method or not path:
                rejected.append({"action": "add", "reason": "缺少 actor、method 或 path", **mod})
                continue
            if actor not in valid_actors:
                rejected.append({"action": "add", "reason": "角色不在当前测试范围", **mod})
                continue
            if endpoint_key not in valid_endpoints:
                rejected.append({"action": "add", "reason": "接口不在当前接口清单", **mod})
                continue
            case_key = (actor, method, path)
            if case_key in existing_case_keys:
                rejected.append({"action": "add", "reason": "角色、方法和接口路径相同的计划项已存在", **mod})
                continue
            new_case = _build_case(
                actor,
                set(),
                {"method": method, "path": path, "name": mod.get("name", path), "module": mod.get("module", ""), "risk": priority},
                mod.get("type", "角色隔离越权") == "自身权限",
                mod.get("category", "role_isolation"),
            )
            new_case["type"] = mod.get("type", new_case["type"])
            new_case["expected"] = mod.get("expected", new_case["expected"])
            if reason:
                new_case["ai_reason"] = reason
            cases.append(new_case)
            existing_case_keys.add(case_key)
            applied.append({"action": "add", **mod})

        elif action == "remove":
            if not actor or not method or not path:
                rejected.append({"action": "remove", "reason": "删除操作必须精确指定 actor、method 和 path", **mod})
                continue
            removed = 0
            for i in reversed(range(len(cases))):
                c = cases[i]
                if c.get("actor") == actor and \
                   c.get("method", "").upper() == method and \
                   c.get("path") == path:
                    cases.pop(i)
                    removed += 1
            if removed:
                applied.append({"action": "remove", "removed_count": removed, **mod})
            else:
                rejected.append({"action": "remove", "reason": "未找到匹配的 case", **mod})

        elif action == "modify":
            if not actor or not method or not path:
                rejected.append({"action": "modify", "reason": "修改操作必须精确指定 actor、method 和 path", **mod})
                continue
            found = 0
            for c in cases:
                if c.get("actor") == actor and \
                   c.get("method", "").upper() == method and \
                   c.get("path") == path:
                    if mod.get("new_type"):
                        c["type"] = mod["new_type"]
                    if mod.get("new_expected"):
                        c["expected"] = mod["new_expected"]
                    if reason:
                        c["ai_reason"] = reason
                    found += 1
            if found:
                applied.append({"action": "modify", "modified_count": found, **mod})
            else:
                rejected.append({"action": "modify", "reason": "未找到匹配的 case", **mod})

    plan["ai_modifications"] = {"applied": applied, "rejected": rejected}
    return plan


def _write_generated_config(folder: Path, project: dict[str, Any]) -> None:
    """生成便于离线运行的配置示例；密码只写环境变量名，不落入生成代码。"""
    scoped_project = apply_goal_scope_to_project(project)
    def password_env_name(role_name: Any) -> str:
        """Return a portable env-var name without exporting the real secret."""
        raw_name = str(role_name or "role")
        normalized = re.sub(r"[^0-9A-Za-z]+", "_", raw_name.upper()).strip("_") or "ROLE"
        # Chinese-only role names collapse to ROLE after ASCII normalization.
        # The deterministic suffix keeps each secret reference unique.
        fingerprint = hashlib.sha256(raw_name.encode("utf-8")).hexdigest()[:8].upper()
        return f"IDOR_ROLE_{normalized}_{fingerprint}_PASSWORD"

    accounts = {
        role.get("name"): {
            "username": role.get("username", ""),
            # The runner uses the protected project configuration. This
            # downloadable helper exposes a secret reference only, so it can
            # safely be attached to tickets or committed as a template.
            "password_env": password_env_name(role.get("name")),
            "login_type": role.get("login_type", "WEB"),
        }
        for role in scoped_project.get("roles") or []
        if role.get("name")
    }
    endpoints = scoped_project.get("endpoints") or []
    lines = [
        "# -*- coding: utf-8 -*-",
        "# 由 IDOR Workbench 生成。密码未导出；运行时请从环境变量或密钥管理服务注入。",
        f"BASE_URL = {project.get('base_url', '')!r}",
        f"TOKEN_URL = {(project.get('auth') or {}).get('token_url', '')!r}",
        f"AUTH_METHOD = {(project.get('auth') or {}).get('auth_method', 'json_post')!r}",
        f"TOKEN_LOGIN_TYPE = {(project.get('auth') or {}).get('login_type', 'WEB')!r}",
        f"USERNAME_FIELD = {(project.get('auth') or {}).get('username_field', 'username')!r}",
        f"PASSWORD_FIELD = {(project.get('auth') or {}).get('password_field', 'password')!r}",
        f"REQUEST_TIMEOUT = {int(project.get('request_timeout') or 10)!r}",
        f"ACCOUNTS = {accounts!r}",
        f"TARGET_ENDPOINTS = {endpoints!r}",
    ]
    (folder / "generated_config.py").write_text("\n".join(lines), encoding="utf-8")


def _json_safe(value: Any, limit: int = 8000) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v, limit) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v, limit) for v in value]
    if isinstance(value, str):
        return safe_text(value, limit)
    return value


def _result_replay_info(row: dict[str, Any], env: str = "") -> dict[str, Any]:
    request = row.get("request") or {}
    path = request.get("path") or row.get("path") or ""
    url = request.get("url") or (urljoin(f"{env.rstrip('/')}/", str(path).lstrip("/")) if env and path else path)
    headers = request.get("headers") or row.get("req_headers") or {}
    return {
        "method": request.get("method") or row.get("method") or "GET",
        "url": url,
        "path": path,
        "params": _json_safe(request.get("params") or row.get("req_params") or {}),
        "body": _json_safe(request.get("body") if "body" in request else row.get("req_body")),
        "headers": _json_safe(headers),
        "authorization": request.get("authorization") or row.get("authorization") or headers.get("Authorization", ""),
    }


def _diagnose_empty_run(project: dict[str, Any], run_results: dict[str, Any], returncode: int, log_text: str) -> dict[str, Any]:
    roles = project.get("roles") or []
    endpoints = project.get("endpoints") or []
    auth = project.get("auth") or {}
    login_errors = [safe_text(item, 300) for item in (run_results.get("login_errors") or []) if item]
    evidence = []
    suggestions = []
    category = "unknown"

    if not roles:
        category = "missing_roles"
        evidence.append("项目没有配置任何角色账号。")
        suggestions.append("在基础配置里至少新增一个角色，并填写用户名；需要越权对比时建议至少两个角色。")
    if not endpoints:
        category = "missing_endpoints"
        evidence.append("项目接口清单为空。")
        suggestions.append("在接口导入页通过 HAR、Collection、cURL 或手工接口补充至少一个接口。")
    if login_errors:
        category = "auth_failed"
        evidence.extend(login_errors[:8])
        suggestions.append("检查 getToken URL 是否能访问、Auth Method 是否匹配后端、Token 提取路径是否指向真实 token 字段。")
        suggestions.append("如果接口是 GET 免密登录，Auth Method 需要选择 GET (免密)；如果返回字段是 access_token，就把 Token 提取路径改成 access_token。")
    if not auth.get("token_url"):
        category = "auth_config_missing"
        evidence.append("getToken URL 为空。")
        suggestions.append("填写 getToken URL，或在后端支持无 token 执行前不要启动 API 测试。")
    if returncode == 2 and not evidence:
        evidence.append("后台任务结束，但 run_results.json 中 total=0。")
        suggestions.append("检查被测环境 base_url 是否可连通、接口 allowed_roles 是否和角色名称一致、接口请求是否全部在执行前被跳过。")
    if log_text and log_text not in evidence:
        evidence.append(safe_text(log_text, 500))
    if not suggestions:
        suggestions.append("打开 last_run.log 查看原始执行日志，并核对项目配置中的角色、token 和接口清单。")

    return {
        "category": category,
        "title": "API 测试没有产生有效执行结果",
        "evidence": evidence[:10],
        "suggestions": suggestions[:6],
        "log_file": "last_run.log",
        "returncode": returncode,
    }


def _summarize_run_results(data: dict[str, Any]) -> dict[str, Any]:
    """把领域引擎结果压缩为前端、历史和报告共用的稳定摘要结构。"""
    rows = data.get("results") or []
    role_stats: dict[str, dict[str, int]] = {}
    for row in rows:
        role = row.get("role") or "unknown"
        item = role_stats.setdefault(role, {"total": 0, "pass": 0, "fail": 0, "error": 0})
        item["total"] += 1
        if row.get("status") == "PASS":
            item["pass"] += 1
        elif row.get("status") == "ERROR":
            item["error"] += 1
        else:
            item["fail"] += 1
    env = data.get("env", "")
    return {
        "env": env,
        "time": data.get("time", ""),
        "total": data.get("total", len(rows)),
        "passed": data.get("passed", sum(1 for r in rows if r.get("status") == "PASS")),
        "failed": data.get("failed", sum(1 for r in rows if r.get("status") == "FAIL")),
        "skipped": data.get("skipped", sum(1 for r in rows if r.get("status") == "ERROR")),
        "run_error": safe_text(data.get("run_error"), 1000),
        "diagnostics": data.get("diagnostics") or {},
        "login_errors": data.get("login_errors") or [],
        "roles": data.get("roles") or list(role_stats),
        "role_stats": role_stats,
        "scenario": (data.get("scenario_results") or {}).get("summary", {}),
        "frontend": data.get("frontend_results") or {},
        "assertion_profile": data.get("assertion_profile") or {},
        "ai_analysis": data.get("ai_analysis") or data.get("ai_run_analysis") or {},
        "engine": data.get("engine") or {},
        "results": [
            {
                "role": r.get("role"),
                "section": r.get("section"),
                "status": r.get("status"),
                "name": r.get("name"),
                "path": r.get("path"),
                "method": r.get("method"),
                "http": r.get("status_code"),
                "expected": r.get("expected"),
                "verdict": (r.get("assertion") or {}).get("verdict"),
                "confidence": (r.get("assertion") or {}).get("confidence"),
                "failures": r.get("failures") or [],
                "body_preview": r.get("body_preview"),
                "response_body": safe_text(r.get("response_body"), 3000),
                "request": r.get("request") or {},
                "replay": _result_replay_info(r, env),
            }
            for r in rows
        ],
    }


def _task_snapshot(task_id: str) -> dict[str, Any]:
    """优先读进程内最新任务，再回退 SQLite 中的持久化状态。"""
    with TASK_LOCK:
        task = TASKS.get(task_id)
        if task:
            return dict(task)
    persisted = DB.get_task(task_id)
    if persisted:
        return persisted
    raise HTTPException(status_code=404, detail="Task not found")


def _update_task(task_id: str, **updates: Any) -> None:
    with TASK_LOCK:
        task = TASKS.get(task_id)
        if not task:
            return
        task.update(updates)
        task["updated_at"] = now_text()
        snapshot = dict(task)
    DB.update_task(snapshot)


def _run_background_task(task_id: str, fn: Any) -> None:
    """执行一个后台任务并保证异常也进入 failed 终态，避免前端永久轮询。"""
    _update_task(task_id, status="running", message="任务执行中")
    try:
        result = fn()
        _update_task(task_id, status="done", message="任务已完成", result=result)
    except Exception as exc:
        _update_task(task_id, status="failed", message="任务执行失败", error=safe_text(str(exc), 2000))


def _enqueue_task(kind: str, project_id: str, fn: Any) -> dict[str, Any]:
    """创建可轮询任务并提交线程池；返回值只暴露 task_id 和初始状态。"""
    task_id = f"{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"
    task = {"task_id": task_id, "kind": kind, "project_id": project_id, "status": "queued", "message": "任务已入队", "created_at": now_text(), "updated_at": now_text(), "result": None, "error": ""}
    with TASK_LOCK:
        TASKS[task_id] = dict(task)
    DB.create_task(task)
    executor = AI_TASK_EXECUTOR if str(kind).startswith("ai-") else TASK_EXECUTOR
    executor.submit(_run_background_task, task_id, fn)
    return _task_snapshot(task_id)


def _archive_existing_run(folder: Path) -> None:
    """新运行开始前归档当前结果，保证历史比较不会被后一次执行覆盖。"""
    run_results = folder / "run_results.json"
    if not run_results.exists():
        return
    data = _load_json(run_results, {})
    run_id = f"{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"
    run_dir = folder / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    for name in ("run_results.json", "last_run.log", "assertion_profile.json", "scenario_results.json", "frontend_results.json", "ai_run_analysis.json"):
        src = folder / name
        if src.exists():
            shutil.copy2(src, run_dir / name)
    meta = {"run_id": run_id, "time": data.get("time", now_text()), "env": data.get("env", ""), "returncode": 0, "total": data.get("total", 0), "passed": data.get("passed", 0), "failed": data.get("failed", 0), "skipped": data.get("skipped", 0), "roles": data.get("roles", [])}
    _write_json(run_dir / "meta.json", meta)
    DB.insert_run_history(folder.name, meta)


def _archive_run(folder: Path, data: dict[str, Any], log_text: str, returncode: int) -> dict[str, Any]:
    """将一次运行的结果、日志和元数据固化到不可变 runs/<run_id>/ 目录。"""
    run_id = f"{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"
    run_dir = folder / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_json(run_dir / "run_results.json", data)
    (run_dir / "last_run.log").write_text(log_text or "", encoding="utf-8")
    meta = {"run_id": run_id, "time": data.get("time", now_text()), "env": data.get("env", ""), "returncode": returncode, "total": data.get("total", 0), "passed": data.get("passed", 0), "failed": data.get("failed", 0), "skipped": data.get("skipped", 0), "roles": data.get("roles", [])}
    _write_json(run_dir / "meta.json", meta)
    DB.insert_run_history(folder.name, meta)
    return meta


def _api_request_log(data: dict[str, Any], returncode: int) -> str:
    """Write inspectable request evidence without exposing credentials."""
    engine = data.get("engine") or {}
    rows = data.get("results") or []
    lines = [
        f"API execution finished returncode={returncode}",
        f"time={data.get('time', '')} env={data.get('env', '')} engine={engine.get('name', '')} concurrency={engine.get('concurrency', '')}",
        f"total={data.get('total', len(rows))} passed={data.get('passed', 0)} failed={data.get('failed', 0)} skipped={data.get('skipped', 0)}",
    ]
    for index, row in enumerate(rows, start=1):
        lines.append(
            f"[{index}] status={row.get('status', '')} role={row.get('role', '')} section={row.get('section', '')} "
            f"method={row.get('method', '')} path={row.get('path', '')} http={row.get('status_code', 0)}"
        )
    return "\n".join(lines)


def _safe_rmtree(path: Path, retries: int = 6, delay: float = 0.25) -> None:
    """Windows 下后台线程刚写完文件时可能短暂占用句柄，删除目录需要重试。"""
    last_error: Exception | None = None
    for _ in range(retries):
        try:
            if path.exists():
                shutil.rmtree(path)
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(delay)
    if last_error:
        raise last_error


def _queue_config_ai(project_id: str, project: dict[str, Any], ai_config: dict[str, Any] | None = None) -> dict[str, Any]:
    """保存项目后后台分析基础配置，不阻塞页面切换。"""
    folder = _project_dir(project_id)
    task_ai_config = ai_config or _server_ai_config()

    def job() -> dict[str, Any]:
        latest = _load_json(folder / "project.json", project)
        ai_review = ai_analyze_config_core(latest, task_ai_config)
        _write_json(folder / "ai_config_analysis.json", ai_review)
        return {"ai_config_analysis": ai_review}

    return _enqueue_task("ai-config", project_id, job)


# ---------------------------------------------------------------------------
# 启动与认证 API：bootstrap 只返回公开初始化信息和当前会话的脱敏用户信息。
# ---------------------------------------------------------------------------

@app.get("/healthz", include_in_schema=False)
def healthz() -> dict[str, str]:
    """进程存活探针：能响应即可，不承诺外部被测系统或 AI 服务可用。"""
    """Liveness endpoint for a container orchestrator."""
    return {"status": "ok"}


@app.get("/readyz", include_in_schema=False)
def readyz() -> dict[str, str]:
    """就绪探针：确认本地数据库已初始化，可开始接收业务请求。"""
    """Readiness endpoint; verifies the metadata store is reachable."""
    DB.list_projects()
    return {"status": "ready"}


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    return Response(status_code=204)


@app.get("/api/bootstrap")
def bootstrap(request: Request) -> dict[str, Any]:
    """返回前端首屏所需的能力开关、当前用户和脱敏后的 AI 配置。"""
    user = _current_user(request)
    ai_settings = DB.get_user_ai_settings(user["user_id"]) if user else None
    return {
        "runtime_config": {"base_url": "", "roles": [], "endpoints": []},
        "user": _public_user(user),
        "ai_settings": _mask_ai_settings(ai_settings) if ai_settings else {"provider": "server"},
    }


@app.post("/api/auth/register")
def register(payload: dict[str, Any], response: Response) -> dict[str, Any]:
    """注册用户并立即建立 HttpOnly 会话；数据库只保存加盐密码哈希。"""
    username = safe_text(payload.get("username"), 80).strip()
    password = str(payload.get("password") or "")
    confirm_password = str(payload.get("confirm_password") or "")
    if not re.fullmatch(r"[0-9A-Za-z_\-\u4e00-\u9fff]{2,40}", username):
        raise HTTPException(status_code=400, detail="账号只能包含中文、字母、数字、下划线和短横线，长度 2-40")
    if len(password) < 4:
        raise HTTPException(status_code=400, detail="密码至少 4 位")
    if not confirm_password:
        raise HTTPException(status_code=400, detail="请确认密码")
    if password != confirm_password:
        raise HTTPException(status_code=400, detail="两次输入的密码不一致")
    if DB.get_user_by_username(username):
        raise HTTPException(status_code=409, detail="账号已存在")
    user = DB.create_user(uuid.uuid4().hex, username, _hash_password(password))
    token = secrets.token_urlsafe(32)
    DB.save_session(token, user["user_id"])
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 14)
    return {"user": _public_user(user)}


@app.post("/api/auth/login")
def login(payload: dict[str, Any], response: Response) -> dict[str, Any]:
    """验证密码并签发新的随机会话 token，客户端脚本无法读取 Cookie。"""
    username = safe_text(payload.get("username"), 80).strip()
    password = str(payload.get("password") or "")
    user = DB.get_user_by_username(username)
    if not user or not _verify_password(password, user.get("password_hash") or ""):
        raise HTTPException(status_code=401, detail="账号或密码错误")
    token = secrets.token_urlsafe(32)
    DB.save_session(token, user["user_id"])
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 14)
    return {"user": _public_user(user), "ai_settings": _mask_ai_settings(DB.get_user_ai_settings(user["user_id"]))}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response) -> dict[str, Any]:
    token = request.cookies.get(SESSION_COOKIE, "")
    if token:
        DB.delete_session(token)
    response.delete_cookie(SESSION_COOKIE)
    return {"logged_out": True}


@app.get("/api/auth/me")
def me(request: Request) -> dict[str, Any]:
    user = _current_user(request)
    return {
        "authenticated": bool(user),
        "user": _public_user(user),
        "ai_settings": _mask_ai_settings(DB.get_user_ai_settings(user["user_id"])) if user else {"provider": "server"},
    }


@app.get("/api/me/ai-settings")
def get_my_ai_settings(request: Request) -> dict[str, Any]:
    user = _require_user(request)
    return {"settings": _mask_ai_settings(DB.get_user_ai_settings(user["user_id"]) or {"provider": "server"})}


@app.post("/api/me/ai-settings")
def save_my_ai_settings(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    user = _require_user(request)
    previous = DB.get_user_ai_settings(user["user_id"]) or {}
    settings = _sanitize_ai_settings(payload, previous)
    DB.save_user_ai_settings(user["user_id"], settings)
    return {"saved": True, "settings": _mask_ai_settings(settings)}


# ---------------------------------------------------------------------------
# 项目聚合 API：项目保存是“浏览器草稿 -> 服务端可信项目”的边界。
# ---------------------------------------------------------------------------

@app.get("/api/projects")
def list_projects(request: Request) -> dict[str, Any]:
    """列出当前用户自己的项目摘要；匿名用户返回空列表，避免泄露项目存在性。"""
    user = _current_user(request)
    if not user:
        return {"projects": []}
    projects = []
    for project in DB.list_projects(user["user_id"]):
        project_id = project.get("project_id") or ""
        if not project_id:
            continue
        folder = _project_dir(project_id)
        plan = _load_json(folder / "test_plan.json", {})
        projects.append({"project_id": project_id, "project_name": project.get("project_name") or project_id, "base_url": project.get("base_url", ""), "model_type": project.get("model_type", ""), "updated_at": project.get("updated_at", ""), "roles_count": len(project.get("roles") or []), "endpoints_count": len(project.get("endpoints") or []), "cases_count": len(plan.get("cases") or []), "has_report": (folder / "report.docx").exists(), "has_last_run": (folder / "last_run.log").exists()})
    projects.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
    return {"projects": projects}


@app.post("/api/projects")
def save_project(project: dict[str, Any], request: Request) -> dict[str, Any]:
    """创建或更新项目，并生成可审计的规则基线计划。

    关键顺序不能交换：先认证/校验项目所有者，再用 source_id 恢复服务端可信文件
    元数据，之后才允许复制文件和写项目目录。保存完成后异步触发 AI 配置审查，
    因此 AI 不可用不会阻断项目的确定性保存流程。
    """
    user = _require_user(request)
    project_id = project.get("project_id") or _safe_project_id(project.get("project_name"))
    if project.get("project_id") and not DB.project_owned_by(project_id, user["user_id"]):
        raise HTTPException(status_code=403, detail="无权修改其他用户的项目")
    old_project = DB.get_project(project_id) or _load_json(_project_dir(project_id) / "project.json", {})
    project["project_id"] = project_id
    project["owner_user_id"] = user["user_id"]
    project["updated_at"] = now_text()
    folder = _project_dir(project_id)
    folder.mkdir(parents=True, exist_ok=True)
    # Replace paths supplied by the browser before any filesystem operation.
    project["source_files"] = _canonicalize_project_sources(project, user["user_id"])
    _sync_role_references(project, old_project if isinstance(old_project, dict) else None)
    DB.upsert_project(project)
    project["source_files"] = _archive_project_sources(project_id, project.get("source_files") or [], user["user_id"])
    _write_json(folder / "project.json", project)
    if project.get("testcases"):
        _write_json(folder / "testcases.json", project["testcases"])
    plan = _make_plan(project)
    _write_json(folder / "test_plan.json", plan)
    _write_generated_config(folder, project)
    DB.upsert_project(project)
    ai_task = _queue_config_ai(project_id, project, _ai_config_for_request(request, {"ai_config": project.get("ai_config")}))
    return {"project_id": project_id, "missing": plan["missing"], "plan": plan, "ai_task": ai_task}


@app.get("/api/projects/{project_id}")
def get_project(project_id: str, request: Request) -> dict[str, Any]:
    """加载一个项目及计划、最近结果和产物状态；跨账号统一返回 404。"""
    user = _require_user(request)
    if not DB.project_owned_by(project_id, user["user_id"]):
        raise HTTPException(status_code=404, detail="Project not found")
    folder = _project_dir(project_id)
    project = DB.get_project(project_id)
    if project is None:
        project = _load_json(folder / "project.json", None)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    _sync_project_metadata(project_id, project)
    plan = _load_json(folder / "test_plan.json", {}) or _make_plan(project)
    # 不再自动返回历史 run_results，避免在环境不可达时展示旧数据造成误判。
    # 前端如需历史执行结果，应通过 /api/projects/{id}/runs 接口显式获取。
    has_report = (folder / "report.docx").exists()
    last_run_time = ""
    if (folder / "run_results.json").exists():
        cached = _load_json(folder / "run_results.json", {})
        last_run_time = cached.get("time", "")
    return {"project": project, "source_files": DB.list_source_files(project_id), "plan": plan, "run_summary": {}, "has_report": has_report, "last_run_time": last_run_time}


@app.delete("/api/projects/{project_id}")
def delete_project(project_id: str, request: Request) -> dict[str, Any]:
    user = _require_user(request)
    if not DB.project_owned_by(project_id, user["user_id"]):
        raise HTTPException(status_code=404, detail="Project not found")
    folder = _project_dir(project_id)
    if folder.exists():
        _safe_rmtree(folder)
    DB.delete_project(project_id)
    return {"deleted": True, "project_id": project_id}


@app.post("/api/plan")
def make_plan(project: dict[str, Any], request: Request) -> dict[str, Any]:
    """对尚未保存或正在编辑的项目草稿生成规则计划，不在此处执行请求。"""
    _require_payload_project_access(request, project)
    return _make_plan(project)


@app.post("/api/projects/{project_id}/plan")
def save_project_plan(project_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    user = _require_user(request)
    if not DB.project_owned_by(project_id, user["user_id"]):
        raise HTTPException(status_code=404, detail="Project not found")
    plan = payload.get("plan") if isinstance(payload.get("plan"), dict) else None
    if not plan or not isinstance(plan.get("cases"), list):
        raise HTTPException(status_code=400, detail="Invalid plan")
    _write_json(_project_dir(project_id) / "test_plan.json", plan)
    return {"saved": True, "cases": len(plan["cases"])}


# ---------------------------------------------------------------------------
# AI 审查 API：始终以本地规则结果为底座，模型失败时返回可解释的降级结果。
# ---------------------------------------------------------------------------

@app.post("/api/ai/analyze-config")
def ai_analyze_config(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    """审查项目基础配置完整性，不执行被测系统请求。"""
    _require_payload_project_access(request, payload)
    raw_project = payload.get("project")
    project: dict[str, Any] = dict(raw_project) if isinstance(raw_project, dict) else payload
    return ai_analyze_config_core(project, _ai_config_for_request(request, payload))


@app.post("/api/ai/analyze-import")
def ai_analyze_import(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    """审查导入接口的权限、来源和噪声风险；输入 endpoint 会先收窄为对象列表。"""
    _require_payload_project_access(request, payload)
    raw_project = payload.get("project")
    project: dict[str, Any] = dict(raw_project) if isinstance(raw_project, dict) else {}
    submitted_endpoints = payload.get("endpoints")
    project_endpoints = project.get("endpoints")
    raw_endpoints: list[Any] = submitted_endpoints if isinstance(submitted_endpoints, list) else (project_endpoints if isinstance(project_endpoints, list) else [])
    endpoints: list[dict[str, Any]] = [dict(item) for item in raw_endpoints if isinstance(item, dict)]
    return ai_analyze_import_core(project, endpoints, _ai_config_for_request(request, payload))


@app.post("/api/ai/analyze-plan")
def ai_analyze_plan(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    """让 AI 审查规则计划，并只应用通过白名单校验的结构化修改。"""
    _require_payload_project_access(request, payload)
    raw_project = payload.get("project")
    project: dict[str, Any] = dict(raw_project) if isinstance(raw_project, dict) else payload
    raw_plan = payload.get("plan")
    plan: dict[str, Any] = dict(raw_plan) if isinstance(raw_plan, dict) else _make_plan(project)
    # 追踪 AI config
    ai_cfg = _ai_config_for_request(request, payload)
    import logging
    logger = logging.getLogger("idor.ai")
    logger.info(f"[analyze-plan] ai_config provider={ai_cfg.get('provider')} model={ai_cfg.get('model')} base_url={ai_cfg.get('base_url')} timeout={ai_cfg.get('timeout')} has_key={bool(ai_cfg.get('api_key'))} sync_model={ai_cfg.get('sync_model')}")
    # 调用 AI 审查计划，获取修改建议
    analysis = ai_analyze_plan_core(project, plan, ai_cfg)
    # 将 AI 的修改建议应用到计划上
    modifications = analysis.get("plan_modifications") or []
    if modifications:
        modified_plan = _apply_plan_modifications(plan, modifications)
        analysis["modified_plan"] = modified_plan
        applied = modified_plan.get("ai_modifications", {}).get("applied", [])
        rejected = modified_plan.get("ai_modifications", {}).get("rejected", [])
        analysis["plan_modifications"] = applied
        analysis["rejected_modifications"] = rejected
        analysis["modifications_applied"] = len(applied)
        analysis["modifications_rejected"] = len(rejected)
    return analysis


@app.post("/api/ai/analyze-run")
def ai_analyze_run(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    _require_payload_project_access(request, payload)
    project_id = str(payload.get("project_id") or "").strip()
    folder = _project_dir(project_id) if project_id else None
    summary = payload.get("run_summary") if isinstance(payload.get("run_summary"), dict) else {}
    if (not summary or not (summary.get("results") or [])) and folder and (folder / "run_results.json").exists():
        summary = _summarize_run_results(_load_json(folder / "run_results.json", {}))
    if not summary:
        raise HTTPException(status_code=400, detail="No run results to analyze")
    assertion_profile = _load_json(folder / "assertion_profile.json", {}) if folder and (folder / "assertion_profile.json").exists() else summary.get("assertion_profile") or {}
    result = ai_analyze_run_core(summary, assertion_profile, _ai_config_for_request(request, payload))
    if folder:
        tuning = result.get("assertion_tuning") if isinstance(result.get("assertion_tuning"), dict) else {}
        if tuning:
            tuned = apply_ai_tuning(assertion_profile, tuning)
            _write_json(folder / "assertion_profile.json", tuned)
            _write_json(folder / "assertion_ai_tuning.json", result)
        _write_json(folder / "ai_run_analysis.json", result)
    return result


def _friendly_ai_connect_error(exc: Exception) -> str:
    raw = safe_text(exc, 500).strip()
    lowered = raw.lower()
    if isinstance(exc, httpx.ConnectError) or "connection refused" in lowered or "failed to establish" in lowered:
        return f"无法连接 AI Base URL。请检查地址、端口、代理和内网连通性。原始错误：{raw}"
    if isinstance(exc, httpx.TimeoutException) or "timed out" in lowered or "timeout" in lowered:
        return f"AI 连通性检测超时。请确认模型服务是否启动，或把超时秒数调大后重试。原始错误：{raw}"
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in {401, 403}:
            return f"AI 服务拒绝鉴权，通常是 API Key 无效或权限不足。HTTP {status}"
        if status == 404:
            return "AI 接口返回 404。请检查 Base URL 是否应以 /v1 结尾、模型名是否存在。"
        if status == 429:
            return "AI 服务返回 429，当前 Key 或服务限流。请稍后重试或更换额度充足的 Key。"
        if status >= 500:
            return f"AI 服务端返回 HTTP {status}。请检查模型服务状态、网关代理或模型名。"
        return f"AI 服务返回 HTTP {status}。请检查 Base URL、模型名和 API Key。"
    return f"AI 连通性检测失败。请检查 Base URL、模型名、API Key 和网络。原始错误：{raw}"


@app.post("/api/ai/test")
def ai_test(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    """测试当前 AI 配置的网络与模型可用性；认证错误保持原 HTTP 状态。"""
    # Authorization errors are part of the HTTP contract and must not be
    # converted into an apparently successful AI diagnostic response.
    _require_payload_project_access(request, payload)
    try:
        config = _ai_config_for_request(request, payload)
        provider = str(config.get("provider") or "ollama").lower()
        base_url = str(config.get("base_url") or "").rstrip("/")
        model = str(config.get("model") or "")
        if provider == "ollama":
            url = base_url if base_url.endswith("/api/tags") else f"{base_url}/api/tags"
            resp = httpx.get(url, timeout=5, verify=SETTINGS.ai_verify_tls, trust_env=False)
            resp.raise_for_status()
            models = [item.get("name", "") for item in (resp.json().get("models") or [])]
            matched = (not model) or any(name == model or name.startswith(f"{model}:") for name in models)
            return {
                "ok": matched,
                "provider": provider,
                "model": model,
                "content": "Ollama 服务可访问" if matched else "Ollama 可访问，但未找到配置模型",
                "error": "" if matched else f"未找到模型：{model}",
            }
        if not (base_url and model and config.get("api_key")):
            return {"ok": False, "provider": provider, "model": model, "content": "", "error": "base_url/model/api_key 未配置完整"}
        url = base_url if base_url.endswith("/chat/completions") else f"{base_url.rstrip('/')}/chat/completions"
        resp = httpx.post(
            url,
            headers={"Authorization": f"Bearer {config['api_key']}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": "回复 OK"}],
                "temperature": 0,
                "max_tokens": 8,
            },
            timeout=10,
            verify=SETTINGS.ai_verify_tls,
            trust_env=False,
        )
        resp.raise_for_status()
        return {"ok": True, "provider": provider, "model": model, "content": "自定义 AI API 连通成功", "error": ""}
    except Exception as exc:
        return {"ok": False, "error": _friendly_ai_connect_error(exc)}


# ---------------------------------------------------------------------------
# 证据导入 API：上传返回 source_id，后续项目保存只能引用该服务端能力 ID。
# ---------------------------------------------------------------------------

@app.post("/api/import-curl")
def import_curl(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    """解析而不执行 cURL 文本，并按可选来源角色标记观察证据。"""
    _require_payload_project_access(request, payload)
    roles = _roles_from_payload(payload, safe_text(payload.get("project_id"), 120).strip() or None)
    source_role = safe_text(payload.get("source_role"), 80).strip() or None
    if source_role and source_role not in roles:
        roles.append(source_role)
    return {"endpoints": _parse_curl(str(payload.get("text") or ""), roles, source_role)}


@app.post("/api/import/{kind}")
async def import_file(kind: str, request: Request, file: UploadFile = File(...), source_role: str | None = Form(None), project_id: str | None = Form(None)) -> dict[str, Any]:
    """保存并解析 HAR/Collection/PRD/用例；每种格式返回统一来源元数据。"""
    if kind not in {"har", "collection", "prd", "testcase", "api_tool"}:
        raise HTTPException(status_code=400, detail="Unsupported import kind")
    user = _require_user(request)
    if project_id:
        _require_owned_project(request, project_id)
    target, source_file = _store_upload_file(kind, file, user["user_id"], SETTINGS.max_upload_bytes)
    if source_role:
        source_file["source_role"] = source_role
    DB.upsert_source_file(source_file)
    roles = _roles_from_payload(project_id=project_id)
    if source_role and source_role not in roles:
        roles.append(source_role)
    if kind == "har":
        return {"saved_to": str(target), "source_file": source_file, "endpoints": _parse_har(target, roles, source_role)}
    if kind in {"collection", "api_tool"}:
        return {"saved_to": str(target), "source_file": source_file, "endpoints": _parse_collection_json(target, roles, source_role)}
    if kind == "prd":
        return {"saved_to": str(target), "source_file": source_file, "prd_text": target.read_text(encoding="utf-8-sig", errors="ignore")}
    return {"saved_to": str(target), "source_file": source_file, "testcases": _parse_testcases(target)}


@app.delete("/api/source-files/{source_id}")
def delete_source_file(source_id: str, request: Request) -> dict[str, Any]:
    source_id = _safe_id(source_id, "source id")
    user = _require_user(request)
    if not DB.source_file_accessible_by(source_id, user["user_id"]):
        raise HTTPException(status_code=404, detail="Source file not found")
    source = DB.get_source_file(source_id)
    if source:
        _delete_stored_source_file(source.get("stored_path") or "")
        DB.delete_source_file(source_id)
    return {"deleted": True, "source_id": source_id}


# ---------------------------------------------------------------------------
# Playwright 人工录制：同一项目同一时刻只允许一个 headed 浏览器会话。
# ---------------------------------------------------------------------------

@app.post("/api/projects/{project_id}/manual-recording/start")
def start_manual_recording(project_id: str, request: Request, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """启动可见浏览器会话，让用户真实操作并采集带角色/页面来源的网络证据。"""
    """打开一个 headed Playwright 浏览器，让用户人工操作并录制接口/HAR/页面归属。"""
    from idor_workbench.domains.idor.recorder import ManualRecordingSession

    _require_owned_project(request, project_id)
    folder = _project_dir(project_id)
    project = DB.get_project(project_id) or _load_json(folder / "project.json", None)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    roles = _roles_from_project(project)
    role_name = safe_text((payload or {}).get("role"), 120).strip() or (roles[0] if roles else "")
    if not role_name:
        raise HTTPException(status_code=400, detail="role is required")
    if role_name not in roles:
        raise HTTPException(status_code=400, detail="role is not in current project")

    with MANUAL_RECORDING_LOCK:
        existing = MANUAL_RECORDINGS.get(project_id)
        if existing and not existing.done_event.is_set():
            return {"already_running": True, **existing.status()}
        session = ManualRecordingSession(project, folder, role_name)
        MANUAL_RECORDINGS[project_id] = session
        session.start()
    session.ready_event.wait(timeout=20)
    return session.status()

@app.get("/api/projects/{project_id}/findings")
def get_project_findings(project_id: str, request: Request) -> dict[str, Any]:
    """读取当前项目的 Finding 风险池，供 Vue 人工复核面板展示。"""
    _require_owned_project(request, project_id)
    folder = _project_dir(project_id)
    findings = load_findings(folder / "findings.json")
    return {
        "items": findings_to_dicts(findings),
        "count": len(findings),
    }


@app.post("/api/projects/{project_id}/findings/{finding_id}/transition")
def transition_project_finding(
    project_id: str,
    finding_id: str,
    payload: dict[str, Any],
    request: Request,
) -> dict[str, Any]:
    """执行一张 Finding 的受控状态流转；所有规则仍由领域层校验。"""
    _require_owned_project(request, project_id)
    folder = _project_dir(project_id)
    path = folder / "findings.json"
    action = str(payload.get("action") or "")

    try:
        if action == "review":
            finding = review_finding_by_id(
                path,
                finding_id,
                str(payload.get("decision") or ""),
                str(payload.get("note") or ""),
            )
        elif action == "waiting_fix":
            finding = mark_finding_waiting_fix_by_id(
                path, finding_id, str(payload.get("fix_version") or "")
            )
        elif action == "retest":
            finding = mark_finding_retested_by_id(
                path,
                finding_id,
                str(payload.get("retest_run_id") or ""),
                bool(payload.get("passed")),
                str(payload.get("note") or ""),
            )
        elif action == "close":
            finding = close_finding_by_id(path, finding_id, str(payload.get("note") or ""))
        else:
            raise ValueError(f"invalid finding transition action: {action}")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return {"item": finding.to_dict()}



@app.get("/api/projects/{project_id}/manual-recording/status")
def get_manual_recording_status(project_id: str, request: Request) -> dict[str, Any]:
    _require_owned_project(request, project_id)
    with MANUAL_RECORDING_LOCK:
        session = MANUAL_RECORDINGS.get(project_id)
    if not session:
        return {"status": "idle", "project_id": project_id}
    return session.status()


@app.post("/api/projects/{project_id}/manual-recording/stop")
def stop_manual_recording(project_id: str, request: Request) -> dict[str, Any]:
    _require_owned_project(request, project_id)
    with MANUAL_RECORDING_LOCK:
        session = MANUAL_RECORDINGS.get(project_id)
    if not session:
        return {"status": "idle", "project_id": project_id, "merged_endpoints": [], "total_endpoints": 0}
    result = session.stop(timeout=45)
    if result.get("status") != "stopping":
        with MANUAL_RECORDING_LOCK:
            MANUAL_RECORDINGS.pop(project_id, None)
    return result


def _execute_api_run(project_id: str, payload: dict[str, Any] | None = None, ai_config: dict[str, Any] | None = None) -> dict[str, Any]:
    """后台任务体：场景造数 -> API 权限扫描 -> AI 复核 -> 结果归档。"""
    from idor_workbench.domains.idor.runner import run_api_project

    folder = _project_dir(project_id)
    project = DB.get_project(project_id) or _load_json(folder / "project.json", None)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    payload = payload or {}
    report = bool(payload.get("report"))
    if isinstance(payload.get("manual_review"), dict):
        _write_json(folder / "manual_review.json", payload["manual_review"])
    _archive_existing_run(folder)
    try:
        returncode = run_api_project(project, folder, report=report, ai_config=ai_config)
        log_text = f"run_api_project finished returncode={returncode}"
    except Exception as exc:
        returncode = 1
        log_text = safe_text(exc, 4000)
    run_results = _load_json(folder / "run_results.json", {})
    if returncode != 0 and not report:
        run_results = {"env": project.get("base_url", ""), "time": now_text(), "total": 0, "passed": 0, "failed": 0, "skipped": 0, "roles": [], "results": [], "assertion_profile": {}, "run_error": log_text}
    elif not run_results:
        run_results = {"env": project.get("base_url", ""), "time": now_text(), "total": 0, "passed": 0, "failed": 0, "skipped": 0, "roles": [], "results": [], "assertion_profile": {}, "run_error": log_text}
        _write_json(folder / "run_results.json", run_results)
    if not report and int(run_results.get("total") or 0) == 0:
        returncode = returncode or 2
        run_results["run_error"] = run_results.get("run_error") or "未产生 API 执行结果，请检查角色账号、登录接口、被测环境连通性和接口列表。"
        log_text = run_results["run_error"]
        run_results["diagnostics"] = _diagnose_empty_run(project, run_results, returncode, log_text)
    elif not report:
        log_text = _api_request_log(run_results, returncode)
    _write_json(folder / "run_results.json", run_results)
    (folder / "last_run.log").write_text(log_text, encoding="utf-8")
    run_meta = _archive_run(folder, run_results, log_text, returncode)
    return {"returncode": returncode, "stdout": log_text[-12000:], "stderr": "" if returncode == 0 else log_text[-4000:], "has_report": (folder / "report.docx").exists(), "has_run_results": True, "run_id": run_meta["run_id"], "run_summary": _summarize_run_results(run_results)}


@app.post("/api/projects/{project_id}/run")
def run_project(project_id: str, request: Request, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """鉴权后创建 API 执行任务；HTTP 请求立即返回 task_id，不阻塞连接。"""
    _require_owned_project(request, project_id)
    task_ai_config = _ai_config_for_request(request, payload)
    task = _enqueue_task("api-run", project_id, lambda: _execute_api_run(project_id, payload or {}, task_ai_config))
    return {"async": True, **task}


def _execute_frontend_run(project_id: str) -> dict[str, Any]:
    """后台任务体：按角色访问页面并输出前端拦截证据、截图和诊断。"""
    from idor_workbench.domains.idor.frontend_probe import run_frontend_checks

    folder = _project_dir(project_id)
    project = DB.get_project(project_id) or _load_json(folder / "project.json", {})
    # Frontend checks have their own artifact and must not replace API results.
    data = run_frontend_checks(project, folder)
    _write_json(folder / "frontend_results.json", data)
    # This transient shape is only for the frontend task response.
    existing_assertion_profile = _load_json(folder / "assertion_profile.json", {}) if (folder / "assertion_profile.json").exists() else {}
    run_results = {
        "env": project.get("base_url", ""),
        "time": now_text(),
        "results": [],
        "total": 0,
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "roles": _roles_from_project(project),
        "login_errors": [],
        "assertion_profile": existing_assertion_profile,
        "frontend_results": data,
    }
    frontend_log = {
        "summary": data.get("summary", {}),
        "note": data.get("note", ""),
        "setup_error": data.get("setup_error", ""),
        "login_errors": data.get("login_errors", []),
        "diagnostics": data.get("diagnostics", {}),
    }
    (folder / "last_frontend_run.log").write_text(json.dumps(frontend_log, ensure_ascii=False, indent=2), encoding="utf-8")
    frontend_summary = data.get("summary") or {}
    if data.get("setup_error"):
        returncode = 3
    elif data.get("note") and not frontend_summary.get("total"):
        returncode = 2
    elif frontend_summary.get("error") or frontend_summary.get("failed"):
        returncode = 1
    else:
        returncode = 0
    return {"returncode": returncode, "has_frontend_results": True, "frontend_diagnostics": frontend_log, "run_summary": _summarize_run_results(run_results)}


@app.post("/api/projects/{project_id}/run-frontend")
def run_frontend_project(project_id: str, request: Request) -> dict[str, Any]:
    """创建前端权限检测任务，与 API 执行使用独立任务类型。"""
    _require_owned_project(request, project_id)
    task = _enqueue_task("frontend-run", project_id, lambda: _execute_frontend_run(project_id))
    return {"async": True, **task}


@app.post("/api/projects/{project_id}/manual-review")
def save_manual_review(project_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    _require_owned_project(request, project_id)
    folder = _project_dir(project_id)
    _write_json(folder / "manual_review.json", payload)
    return {"saved": True, "items": len(payload.get("items") or [])}


@app.post("/api/projects/{project_id}/report")
def generate_report_task(project_id: str, request: Request, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    _require_owned_project(request, project_id)
    task_ai_config = _ai_config_for_request(request, payload)
    task = _enqueue_task("report", project_id, lambda: _execute_api_run(project_id, {"report": True, **(payload or {})}, task_ai_config))
    return {"async": True, **task}


# ---------------------------------------------------------------------------
# 任务、历史与产物：所有读取再次校验所属项目，task_id/run_id 不是权限凭据。
# ---------------------------------------------------------------------------

@app.get("/api/tasks/{task_id}")
def get_task(task_id: str, request: Request) -> dict[str, Any]:
    """返回任务状态快照；先由任务关联 project_id，再做项目所有权校验。"""
    task = _task_snapshot(_safe_id(task_id, "task id"))
    project_id = task.get("project_id") or ""
    _require_owned_project(request, project_id)
    log_name = "last_frontend_run.log" if task.get("kind") == "frontend-run" else "last_run.log"
    if project_id:
        log_path = _project_dir(project_id) / log_name
        if log_path.exists():
            task["log_tail"] = log_path.read_text(encoding="utf-8", errors="ignore")[-8000:]
    return task


@app.get("/api/projects/{project_id}/runs")
def list_runs(project_id: str, request: Request) -> dict[str, Any]:
    """列出不可变执行归档摘要，供历史搜索和左右结果比较。"""
    _require_owned_project(request, project_id)
    runs_dir = _project_dir(project_id) / "runs"
    runs = []
    if runs_dir.exists():
        for folder in runs_dir.iterdir():
            if folder.is_dir() and (folder / "meta.json").exists():
                meta = _load_json(folder / "meta.json", {})
                runs.append({"run_id": folder.name, "meta": meta, **meta})
    runs.sort(key=lambda item: item.get("time") or "", reverse=True)
    return {"runs": runs}


@app.get("/api/projects/{project_id}/runs/{run_id}")
def get_run(project_id: str, run_id: str, request: Request) -> dict[str, Any]:
    _require_owned_project(request, project_id)
    run_dir = _project_dir(project_id) / "runs" / _safe_id(run_id, "run id")
    if not run_dir.exists():
        raise HTTPException(status_code=404, detail="Run not found")
    data = _load_json(run_dir / "run_results.json", {})
    meta = _load_json(run_dir / "meta.json", {})
    return {"run_id": run_id, "meta": meta, "run_summary": _summarize_run_results(data)}


@app.patch("/api/projects/{project_id}/runs/{run_id}")
def update_run_note(project_id: str, run_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    _require_owned_project(request, project_id)
    run_dir = _project_dir(project_id) / "runs" / _safe_id(run_id, "run id")
    meta_path = run_dir / "meta.json"
    meta = _load_json(meta_path, {})
    meta["note"] = safe_text(payload.get("note"), 1000)
    _write_json(meta_path, meta)
    return {"saved": True, "meta": meta}


@app.delete("/api/projects/{project_id}/runs/{run_id}")
def delete_run(project_id: str, run_id: str, request: Request) -> dict[str, Any]:
    _require_owned_project(request, project_id)
    run_dir = _project_dir(project_id) / "runs" / _safe_id(run_id, "run id")
    if run_dir.exists():
        shutil.rmtree(run_dir)
    DB.delete_run_history(run_id)
    return {"deleted": True}


@app.get("/api/projects/{project_id}/download/{name}")
def download_artifact(project_id: str, name: str, request: Request) -> FileResponse:
    """下载白名单产物；禁止用任意文件名读取项目目录中的其他内容。"""
    _require_owned_project(request, project_id)
    allowed = {"project.json", "test_plan.json", "generated_config.py", "last_run.log", "last_frontend_run.log", "report.docx", "assertion_profile.json", "run_results.json", "frontend_results.json", "scenario_results.json", "manual_review.json", "ai_run_analysis.json", "ai_config_analysis.json", "assertion_ai_tuning.json","findings.json"}
    if name not in allowed:
        raise HTTPException(status_code=400, detail="Unsupported artifact")
    path = _project_dir(project_id) / name
    if not path.exists():
        raise HTTPException(status_code=404, detail="Artifact not found")
    return FileResponse(path, filename=name)


@app.get("/api/projects/{project_id}/screenshots/{name}")
def frontend_screenshot(project_id: str, name: str, request: Request) -> FileResponse:
    _require_owned_project(request, project_id)
    safe_name = Path(name).name
    if safe_name != name or not safe_name.lower().endswith(".png"):
        raise HTTPException(status_code=400, detail="Invalid screenshot name")
    path = _project_dir(project_id) / "frontend_screenshots" / safe_name
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Screenshot not found")
    return FileResponse(path)


@app.get("/vue", include_in_schema=False)
def vue_index() -> FileResponse:
    """提供 Vite 构建后的 Vue 工作台；未构建时给出可行动的提示。"""
    entry = STATIC_DIR / "vue-app" / "index.html"
    if not entry.exists():
        raise HTTPException(status_code=503, detail="Vue frontend is not built; run npm run build in webapp/frontend")
    return FileResponse(entry)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
