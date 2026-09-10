"""把 API 路径映射成可能的前端页面路由，供 Playwright 探针使用。

推断结果只是候选值，不是授权证据；用户手工填写的 ``page_url`` 始终优先，并通过
返回值中的布尔标记区分“人工确认”和“系统推断”。
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

API_PREFIX_RE = re.compile(r"^/(?:api|openapi|gateway|admin-api)(?:/v?\d+(?:\.\d+)*)?/?", re.I)
ID_SEGMENT_RE = re.compile(
    r"^(?:\d+|[0-9a-f]{8,}(?:-[0-9a-f]{4,}){2,}|[A-Za-z0-9_-]{20,})$",
    re.I,
)
TRAILING_ACTIONS = {
    "add",
    "batch",
    "create",
    "delete",
    "detail",
    "export",
    "get",
    "import",
    "info",
    "list",
    "page",
    "query",
    "remove",
    "save",
    "search",
    "update",
}


def infer_page_url_from_api_path(path: Any) -> str:
    """移除 API/version 前缀、动态 ID 和尾部动作词，得到页面候选路由。"""
    raw = str(path or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    route = parsed.path or raw.split("?", 1)[0]
    if not route.startswith("/"):
        route = f"/{route}"
    route = API_PREFIX_RE.sub("/", route)
    parts = [part for part in route.split("/") if part]
    while parts and ID_SEGMENT_RE.fullmatch(parts[-1]):
        parts.pop()
    if len(parts) > 1 and parts[-1].lower() in TRAILING_ACTIONS:
        parts.pop()
    if not parts:
        return ""
    return "/" + "/".join(parts)


def page_url_for_endpoint(endpoint: dict[str, Any]) -> tuple[str, bool]:
    """返回 ``(page_url, inferred)``；手工页面地址优先且 inferred=False。"""
    manual = str(endpoint.get("page_url") or "").strip()
    if manual:
        return manual, False
    return infer_page_url_from_api_path(endpoint.get("path")), True
