"""IDOR Workbench 的浏览器前端拦截检测。

它和接口执行引擎不同：这里关注“页面层面有没有拦住无权限用户”。
执行路线是 runner -> run_frontend_checks() -> Playwright 打开页面。
登录和 token 提取复用 execution 的上下文逻辑，避免维护两套认证实现。
"""
from __future__ import annotations

import hashlib
import json  # L2 IDOR frontend probe.
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx

from idor_workbench.domains.idor.page_mapping import page_url_for_endpoint
from idor_workbench.domains.idor.scope import apply_goal_scope_to_project

DEFAULT_DENIED_KEYWORDS = [
    "无权限",
    "没有权限",
    "未授权",
    "权限不足",
    "禁止访问",
    "访问被拒绝",
    "403",
    "forbidden",
    "unauthorized",
    "access denied",
    "请先登录",
    "重新登录",
]


def _accounts_from_roles(project: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """把项目角色整理成登录客户端所需的账号映射，不改变原项目对象。"""
    accounts = {}
    default_login_type = (project.get("auth") or {}).get("login_type", "WEB")
    for role in project.get("roles") or []:
        name = role.get("name")
        if not name:
            continue
        accounts[name] = {
            "username": role.get("username", ""),
            "password": role.get("password", ""),
            "login_type": role.get("login_type") or default_login_type,
        }
    return accounts


class FrontendAuthClient:
    """前端探针使用的同步登录客户端。

    Playwright 当前使用同步 API，因此这里也用 httpx.Client 做同步登录。
    但请求上下文、token 解析都复用 execution，保证和接口测试引擎的配置含义一致。
    """

    def __init__(self, project: dict[str, Any]):
        from idor_workbench.domains.idor import execution as execution_engine

        self.execution_engine = execution_engine
        self.context = execution_engine.TestContext.from_project(project)
        self.client = httpx.Client(
            verify=False,
            follow_redirects=False,
            timeout=httpx.Timeout(self.context.request_timeout),
            trust_env=False,
        )

    def close(self) -> None:
        """关闭登录 HTTP 连接池；每次探测结束都必须调用。"""
        self.client.close()

    def get_token(self, username: str, password: str, login_type: str = "WEB") -> str | None:
        """按项目认证配置登录并复用 API 引擎的 token 提取规则。"""
        url = urljoin(f"{self.context.base_url}/", str(self.context.token_url).lstrip("/"))
        try:
            headers = {"XloginType": login_type}
            auth_method = (self.context.auth_method or "json_post").lower()
            if auth_method == "form_post":
                resp = self.client.post(
                    url,
                    data={"username": username, "password": password, "grant_type": "password", "loginType": login_type},
                    headers=headers,
                )
            elif auth_method == "get_no_password":
                resp = self.client.get(
                    url,
                    params={"username": username, "loginType": login_type},
                    headers=headers,
                )
            else:
                resp = self.client.post(
                    url,
                    json={"username": username, "password": password},
                    headers={**headers, "Content-Type": "application/json"},
                )
            return self.execution_engine._extract_token_from_response(resp.json(), self.context.token_path)
        except Exception:
            return None


def _browser_launch_options(headless: bool) -> dict[str, Any]:
    options: dict[str, Any] = {"headless": headless}
    configured = os.getenv("IDOR_PLAYWRIGHT_BROWSER_EXECUTABLE", "").strip()
    candidates = [
        configured,
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            options["executable_path"] = candidate
            break
    return options


def _split_keywords(value: Any, fallback: list[str]) -> list[str]:
    if isinstance(value, list):
        items = value
    else:
        items = re.split(r"[\n,，;；]+", str(value or ""))
    cleaned = [str(item).strip() for item in items if str(item).strip()]
    return cleaned or fallback


def _frontend_settings(project: dict[str, Any]) -> dict[str, Any]:
    """标准化前端探测开关、超时、token 存储键和拦截信号关键词。"""
    settings = project.get("frontend_checks") or {}
    return {
        "enabled": bool(settings.get("enabled", True)),
        "headless": bool(settings.get("headless", True)),
        "timeout_ms": int(settings.get("timeout_ms") or 8000),
        "wait_after_load_ms": int(settings.get("wait_after_load_ms") or 1200),
        "token_storage_keys": _split_keywords(
            settings.get("token_storage_keys"),
            ["token", "accessToken", "Authorization"],
        ),
        "denied_keywords": _split_keywords(settings.get("denied_keywords"), DEFAULT_DENIED_KEYWORDS),
        "redirect_keywords": _split_keywords(
            settings.get("redirect_keywords"),
            ["login", "403", "forbidden", "unauthorized", "noPermission", "no-permission"],
        ),
    }


def _page_targets(project: dict[str, Any]) -> list[dict[str, Any]]:
    """将多个接口和补充页面路由聚合成按 page_url 去重的探测目标。"""
    targets_by_page: dict[str, dict[str, Any]] = {}
    for ep in project.get("endpoints") or []:
        page_url, inferred = page_url_for_endpoint(ep)
        if not page_url:
            continue
        existing = targets_by_page.get(page_url)
        if existing:
            existing["allowed_roles"] = list(dict.fromkeys([
                *(existing.get("allowed_roles") or []),
                *(ep.get("allowed_roles") or []),
            ]))
            existing["api_paths"].append(ep.get("path", ""))
            existing["page_url_inferred"] = bool(existing.get("page_url_inferred") and inferred)
            continue
        targets_by_page[page_url] = {
            "name": ep.get("name") or ep.get("path") or page_url,
            "api_method": ep.get("method", "GET"),
            "api_path": ep.get("path", ""),
            "api_paths": [ep.get("path", "")],
            "page_url": page_url,
            "page_url_inferred": inferred,
            "allowed_roles": ep.get("allowed_roles") or [],
            "module": ep.get("module", ""),
        }
    for route in project.get("page_routes") or []:
        page_url = str(route.get("page_url") or "").strip()
        if not page_url:
            continue
        existing = targets_by_page.get(page_url)
        if existing:
            existing["allowed_roles"] = list(dict.fromkeys([
                *(existing.get("allowed_roles") or []),
                *(route.get("allowed_roles") or route.get("observed_roles") or []),
            ]))
            existing["page_route_sources"] = list(dict.fromkeys([*(existing.get("page_route_sources") or []), route.get("source") or "page_url_supplement"]))
            continue
        targets_by_page[page_url] = {
            "name": route.get("name") or page_url,
            "api_method": "PAGE",
            "api_path": "",
            "api_paths": [],
            "page_url": page_url,
            "page_url_inferred": False,
            "allowed_roles": route.get("allowed_roles") or route.get("observed_roles") or [],
            "module": route.get("module", ""),
            "page_route_sources": [route.get("source") or "page_url_supplement"],
        }
    return list(targets_by_page.values())


def _role_groups(project: dict[str, Any]) -> tuple[list[str], list[str]]:
    roles = [str(role.get("name")) for role in project.get("roles") or [] if role.get("name")]
    admins = [role for role in roles if "管理员" in role or "审计员" in role]
    return admins, [role for role in roles if role not in admins]


def _uses_admin_isolation_policy(project: dict[str, Any]) -> bool:
    goal = re.sub(r"\s+", "", str(project.get("goal") or ""))
    return bool(
        ("管理员" in goal or "审计员" in goal)
        and any(text in goal for text in ("权限间要隔离", "管理员权限间", "相互越权", "交叉越权", "各自的管理权限"))
        and any(text in goal for text in ("普通用户", "普通员工", "前台页面", "前台功能"))
    )


def _apply_goal_access_policy(project: dict[str, Any], targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把明确的管理员隔离目标转换成页面访问矩阵。

    录制到的角色只是“谁访问过”的证据，并不等于完整授权策略。在当前管理员模型中，
    ``/space`` 管理路由归录制管理员所有，其他前台路由由管理员和普通员工共享。
    """
    if not _uses_admin_isolation_policy(project):
        return targets

    admins, all_other_roles = _role_groups(project)
    all_roles = [*admins, *all_other_roles]
    adjusted = []
    for target in targets:
        item = {**target}
        recorded = [role for role in item.get("allowed_roles") or [] if role in all_roles]
        admin_owners = [role for role in recorded if role in admins]
        route = str(item.get("page_url") or "")
        if route.startswith("/space/"):
            # 管理路由仍归录制管理员所有；录制时偶然出现的员工流量不能扩大权限范围。
            item["allowed_roles"] = admin_owners or admins
            item["access_policy"] = "目标规则：管理员中心按管理员角色隔离"
        else:
            item["allowed_roles"] = all_roles
            item["access_policy"] = "目标规则：管理员继承普通员工前台权限"
        adjusted.append(item)
    return adjusted


def _probe_assignments(project: dict[str, Any], target: dict[str, Any]) -> list[tuple[str, list[str]]]:
    """生成实际浏览器角色；只合并权限等价的普通用户，管理员始终单独验证。"""
    admins, employees = _role_groups(project)
    roles = [*admins, *employees]
    allowed = set(target.get("allowed_roles") or [])
    if roles and allowed == set(roles):
        representative = employees[0] if employees else admins[0]
        return [(representative, roles)]

    assignments = [(role, [role]) for role in admins]
    if employees:
        assignments.append((employees[0], employees))
    return assignments or [(role, [role]) for role in roles]


def _evaluate_frontend(target: dict[str, Any], role: str, result: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    """综合弹窗、页面文案、跳转和 HTTP 状态判断页面是否正确放行/拦截。"""
    allowed = role in set(target.get("allowed_roles") or [])
    expected = "允许访问页面" if allowed else "前端应拦截访问"
    final_url = (result.get("final_url") or "").lower()
    text = (result.get("page_text") or "").lower()
    dialogs = result.get("dialogs") or []
    http_status = int(result.get("main_status") or 0)

    denied_hits = []
    for keyword in settings["denied_keywords"]:
        if keyword.lower() in text:
            denied_hits.append(keyword)
    redirect_hit = ""
    for keyword in settings["redirect_keywords"]:
        if keyword.lower() in final_url:
            redirect_hit = keyword
            break

    blocked = bool(dialogs or denied_hits or redirect_hit or http_status in {401, 403})
    signals = []
    if dialogs:
        signals.append("弹窗: " + " | ".join(dialogs[:3]))
    if denied_hits:
        signals.append("页面文案: " + "、".join(denied_hits[:5]))
    if redirect_hit:
        signals.append(f"跳转命中: {redirect_hit}")
    if http_status in {401, 403}:
        signals.append(f"页面 HTTP={http_status}")
    if not signals:
        signals.append("未发现弹窗、无权限文案、拒绝状态码或登录/403跳转")

    status = "PASS" if (not allowed and blocked) or (allowed and not blocked) else "FAIL"
    if result.get("error"):
        status = "ERROR"
        signals = [result["error"]]

    conclusion = "前端已拦截" if blocked else "前端未发现拦截"
    if allowed:
        conclusion = "有权页面可访问" if not blocked else "有权页面疑似被误拦截"
    return {
        "status": status,
        "expected": expected,
        "conclusion": conclusion,
        "blocked": blocked,
        "signals": signals,
        "confidence": "高" if blocked or http_status in {200, 401, 403} else "中",
    }


def run_frontend_checks(project: dict[str, Any], project_dir: Path) -> dict[str, Any]:
    """执行页面级权限检测并返回可审计结果。

    链路为：目标范围收窄 -> 页面/角色矩阵 -> 角色登录 -> 浏览器注入 token ->
    访问页面 -> 收集跳转/文案/弹窗/状态码 -> 规则判断 -> 对异常项截图。
    如果没有 page_url，函数会返回明确诊断而不是伪造 0 条“通过”。
    """
    project = apply_goal_scope_to_project(project)
    settings = _frontend_settings(project)
    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    targets = _apply_goal_access_policy(project, _page_targets(project))
    endpoint_count = len(project.get("endpoints") or [])
    if not settings["enabled"]:
        return {
            "started_at": started_at,
            "enabled": False,
            "results": [],
            "summary": {"total": 0, "passed": 0, "failed": 0, "error": 0, "skipped": 0},
            "note": "前端拦截检测已关闭，因此未启动 Playwright。",
            "diagnostics": {"endpoint_count": endpoint_count, "page_url_count": len(targets), "playwright_started": False},
        }
    if not targets:
        return {
            "started_at": started_at,
            "enabled": True,
            "results": [],
            "summary": {"total": 0, "passed": 0, "failed": 0, "error": 0, "skipped": endpoint_count},
            "note": "没有可用的页面 URL，无法从接口路径推断页面路由，因此没有启动 Playwright，也没有执行页面级前端拦截检测。",
            "diagnostics": {
                "endpoint_count": endpoint_count,
                "page_url_count": 0,
                "playwright_started": False,
                "fix": "请补充接口路径，系统会优先自动推断页面 URL；对于路由与接口不一致的页面，再手工填写页面 URL，例如 /admin/users、/system/roles。",
            },
        }

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {
            "started_at": started_at,
            "enabled": True,
            "results": [],
            "summary": {"total": 0, "passed": 0, "failed": 0, "error": 1},
            "setup_error": "缺少 playwright 依赖。请安装 requirements.txt 并执行 playwright install chromium 后再运行前端检测。",
        }

    auth_client = FrontendAuthClient(project)
    accounts = _accounts_from_roles(project)
    tokens: dict[str, str] = {}
    login_errors = []
    for role_name, account in accounts.items():
        token = auth_client.get_token(account.get("username", ""), account.get("password", ""), account.get("login_type", "WEB"))
        if token:
            tokens[role_name] = token
        else:
            login_errors.append(f"{role_name}: 登录失败，无法执行该角色前端检测")

    auth_client.close()

    if not tokens:
        return {
            "started_at": started_at,
            "enabled": True,
            "results": [],
            "summary": {"total": 0, "passed": 0, "failed": 0, "error": 0, "skipped": len(targets)},
            "login_errors": login_errors,
            "note": "所有角色都没有成功获取 token，因此没有启动 Playwright。",
            "diagnostics": {
                "endpoint_count": endpoint_count,
                "page_url_count": len(targets),
                "role_count": len(accounts),
                "token_count": 0,
                "playwright_started": False,
                "fix": "请先修复 getToken URL、Auth Method、账号密码或 Token 提取路径；前端拦截检测需要角色 token 注入浏览器上下文。",
            },
        }

    screenshot_dir = project_dir / "frontend_screenshots"
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    base_url = project.get("base_url", "")
    results = []
    planned_checks = sum(len(_probe_assignments(project, target)) for target in targets)

    with sync_playwright() as p:
        browser = p.chromium.launch(**_browser_launch_options(settings["headless"]))
        try:
            for role_name, token in tokens.items():
                context = browser.new_context(
                    extra_http_headers={"Authorization": f"Bearer {token}"},
                    ignore_https_errors=True,
                )
                context.add_init_script(
                    f"""(() => {{
                      const token = {json.dumps(token)};
                      const keys = {json.dumps(settings["token_storage_keys"], ensure_ascii=False)};
                      for (const key of keys) {{
                        localStorage.setItem(key, token);
                        sessionStorage.setItem(key, token);
                      }}
                      localStorage.setItem('Authorization', 'Bearer ' + token);
                      sessionStorage.setItem('Authorization', 'Bearer ' + token);
                    }})();"""
                )
                page = context.new_page()
                dialogs: list[str] = []

                def handle_dialog(dialog: Any, dialog_messages: list[str] = dialogs) -> None:
                    dialog_messages.append(dialog.message)
                    dialog.dismiss()

                page.on("dialog", handle_dialog)
                for target in targets:
                    assignments = _probe_assignments(project, target)
                    assignment = next((item for item in assignments if item[0] == role_name), None)
                    if not assignment:
                        continue
                    _, equivalent_roles = assignment
                    dialogs.clear()
                    full_url = urljoin(base_url.rstrip("/") + "/", target["page_url"].lstrip("/"))
                    main_status = 0
                    error = ""
                    page_text = ""
                    screenshot_key = hashlib.sha1(f"{role_name}|{target['page_url']}|{target.get('api_path', '')}".encode()).hexdigest()[:10]
                    screenshot_stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", f"{role_name}-{target['name']}-{screenshot_key}")[:120]
                    screenshot_path = screenshot_dir / screenshot_stem
                    screenshot_path = screenshot_path.with_suffix(".png")
                    try:
                        response = page.goto(full_url, wait_until="domcontentloaded", timeout=settings["timeout_ms"])
                        main_status = response.status if response else 0
                        page.wait_for_timeout(settings["wait_after_load_ms"])
                        page_text = page.locator("body").inner_text(timeout=1200)[:3000]
                    except Exception as exc:
                        error = str(exc)
                    raw = {
                        "role": role_name,
                        "name": target["name"],
                        "module": target.get("module", ""),
                        "api_method": target.get("api_method", ""),
                        "api_path": target.get("api_path", ""),
                        "page_url": target["page_url"],
                        "page_url_inferred": target.get("page_url_inferred", False),
                        "full_url": full_url,
                        "allowed_roles": target.get("allowed_roles", []),
                        "access_policy": target.get("access_policy", "录制权限证据"),
                        "equivalent_roles": equivalent_roles,
                        "api_paths": target.get("api_paths", []),
                        "main_status": main_status,
                        "final_url": page.url if not error else "",
                        "dialogs": list(dialogs),
                        "page_text": page_text[:600],
                        "screenshot": str(screenshot_path) if screenshot_path.exists() else "",
                        "error": error,
                        "auth_injected": True,
                    }
                    raw.update(_evaluate_frontend(target, role_name, raw, settings))
                    # Full-page screenshots are expensive. Keep visual evidence
                    # for findings, instead of paying that cost for every PASS.
                    if raw["status"] != "PASS":
                        try:
                            page.screenshot(path=str(screenshot_path), full_page=True)
                        except Exception:
                            pass
                    raw["screenshot"] = str(screenshot_path) if screenshot_path.exists() else ""
                    results.append(raw)
                context.close()
        finally:
            browser.close()

    summary = {
        "total": len(results),
        "passed": sum(1 for r in results if r["status"] == "PASS"),
        "failed": sum(1 for r in results if r["status"] == "FAIL"),
        "error": sum(1 for r in results if r["status"] == "ERROR"),
    }
    return {
        "started_at": started_at,
        "enabled": True,
        "settings": settings,
        "login_errors": login_errors,
        "summary": summary,
        "diagnostics": {
            "endpoint_count": endpoint_count,
            "page_url_count": len(targets),
            "role_count": len(tokens),
            "planned_checks": planned_checks,
            "execution_strategy": "管理员管理页逐管理员交叉校验；同权限普通员工合并为代表校验",
        },
        "results": results,
    }
