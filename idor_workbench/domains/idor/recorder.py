"""Playwright 人工录制会话与请求归一化工具。

人工录制解决“只靠文档不知道真实页面和角色来源”的问题：用户使用指定角色操作
可见浏览器，系统监听实际网络请求，并为每个接口保留来源角色、页面、菜单章节和
样例响应。录制证据不会直接成为授权真相，仍需用户确认 ``allowed_roles``。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlparse

from idor_workbench.domains.idor.scope import apply_goal_scope_to_project

# 静态资源后缀，网络拦截时过滤掉
STATIC_EXT = re.compile(
    r"\.(css|js|map|woff2?|ttf|eot|png|jpe?g|gif|svg|ico|pdf|webp|mp4|webm)\b",
    re.I,
)
# API 路径特征（用于辅助分类）
API_PATH_RE = re.compile(r"/api/|/v\d+/|/graphql|/rest/|/openapi|/swagger", re.I)
STATIC_PATH_PREFIXES = ("/static/", "/assets/", "/favicon.ico")
UNCAPTURED_SECTION = "无上层索引/未捕捉到"


def _accounts_from_roles(project: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """提取录制所需账号；密码只用于当前登录会话，不写入录制结果。"""
    accounts: dict[str, dict[str, Any]] = {}
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


def _request_key(method: str, path: str) -> str:
    """归一化请求标识：GET /api/users/:id -> GET /api/users/:id（保留路径结构）。"""
    try:
        parsed = urlparse(path)
        route = (parsed.path or "/").rstrip("/") or "/"
        # 动态 ID 段归一: /api/users/123 -> /api/users/:id
        parts = []
        for segment in route.split("/"):
            if not segment:
                continue
            if segment.isdigit() or (
                len(segment) >= 8 and re.fullmatch(r"[0-9a-f-]+", segment, re.I)
            ):
                parts.append(":id")
            else:
                parts.append(segment)
        return f"{method.upper()} /{'/'.join(parts)}"
    except Exception:
        return f"{method.upper()} {path}"


def _module_from_path(path: str) -> str:
    """从路径第一段推断接口模块名。"""
    parsed = urlparse(path)
    parts = [p for p in (parsed.path or "/").split("/") if p]
    if not parts:
        return ""
    if parts[0].lower() in {"api", "openapi", "gateway", "v1", "v2", "v3", "v4", "v5"}:
        return parts[1] if len(parts) > 1 else parts[0]
    return parts[0]


def _page_route(url: str) -> str:
    """保留 hash 路由，便于把录到的接口关联回实际业务页面。"""
    parsed = urlparse(url)
    route = (parsed.path or "/").rstrip("/") or "/"
    return f"{route}#{parsed.fragment}" if parsed.fragment else route


def _clean_section_title(value: Any) -> str:
    """清理菜单章节名并排除“保存/删除/刷新”等操作按钮文本。"""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text or text.lower() in {"x", "ok", "cancel", "submit", "save", "delete", "logout"}:
        return ""
    if text in {"×", "关闭", "取消", "确定", "保存", "删除", "退出", "刷新", "重置", "查询"}:
        return ""
    return text[:80]


def _source_section_from_page(page: Any) -> str:
    """从当前激活导航项、路由缓存和最近点击元素推断接口所属业务菜单。"""
    try:
        title = page.evaluate(
            """() => {
              const clean = value => String(value || '').replace(/\\s+/g, ' ').trim().slice(0, 80);
              const ignored = /^(x|ok|cancel|submit|save|delete|logout|刷新|重置|查询|关闭|取消|确定|保存|删除|退出)$/i;
              const navSelector = 'nav,aside,[role="navigation"],[role="menu"],.ant-menu,.el-menu,[class*="sidebar"],[class*="sider"],[class*="side-menu"],[class*="menu"]';
              const itemSelector = 'a[href],[role="menuitem"],[role="tab"],.ant-menu-item,.el-menu-item,.menu-item,.nav-item,.sidebar-item,.sider-item';
              const currentRoute = () => {
                const path = (location.pathname || '/').replace(/\\/$/, '') || '/';
                return location.hash ? `${path}${location.hash}` : path;
              };
              const valid = value => {
                const text = clean(value);
                if (!text || text.length > 80) return '';
                if (ignored.test(text)) return '';
                return text;
              };
              const inNav = node => !!(node && node.closest && node.closest(navSelector));
              const isMenuNode = node => {
                if (!node || node.nodeType !== 1) return false;
                const role = node.getAttribute('role') || '';
                const classes = String(node.className || '').toLowerCase();
                return role === 'menuitem' || role === 'tab'
                  || node.matches(itemSelector)
                  || (inNav(node) && /(^|[-_\\s])(item|link|entry|option)([-_\\s]|$)/.test(classes));
              };
              const bestMenuNode = node => {
                if (!node || node.nodeType !== 1) return null;
                if (isMenuNode(node)) return node;
                return node.closest && node.closest(itemSelector);
              };
              const navRank = node => {
                if (!node || !node.getBoundingClientRect) return 0;
                const rect = node.getBoundingClientRect();
                if (inNav(node)) return 3;
                if (rect.left <= 360 && rect.width <= 420 && rect.height >= 20 && rect.height <= 96) return 2;
                return 1;
              };
              const labelFor = node => {
                if (!node) return '';
                const own = node.getAttribute('aria-label') || node.getAttribute('data-menu-title') || node.getAttribute('title');
                const direct = valid(own);
                if (direct) return direct;
                const leaves = [...node.querySelectorAll('span,div,p,a')]
                  .filter(child => child !== node && child.children.length === 0)
                  .map(child => valid(child.getAttribute('aria-label') || child.getAttribute('title') || child.textContent))
                  .filter(Boolean);
                return leaves[0] || valid(node.innerText);
              };
              const selectors = [
                '[aria-current="page"]',
                '[aria-selected="true"]',
                '.ant-menu-item-selected',
                '.el-menu-item.is-active',
                '.router-link-active',
                '.active',
                '.selected',
                '.is-active'
              ];
              for (const selector of selectors) {
                const node = [...document.querySelectorAll(selector)]
                  .map(bestMenuNode)
                  .filter(item => item && (isMenuNode(item) || inNav(item)))
                  .sort((a, b) => navRank(b) - navRank(a))[0];
                const text = labelFor(node);
                if (text) return text;
              }
              try {
                const routes = JSON.parse(sessionStorage.getItem('__idorRouteSections') || '{}');
                const text = valid(routes[currentRoute()]);
                if (text) return text;
              } catch (_) {}
              const last = window.__idorLastSection || {};
              if (last && Date.now() - Number(last.time || 0) < 3000) {
                const text = valid(last.title);
                if (text) return text;
              }
              try {
                const saved = JSON.parse(sessionStorage.getItem('__idorLastSection') || '{}');
                if (saved && Date.now() - Number(saved.time || 0) < 3000) {
                  const text = valid(saved.title);
                  if (text) return text;
                }
              } catch (_) {}
              return '';
            }"""
        )
        return _clean_section_title(title)
    except Exception:
        return ""


def _is_safe_navigation_target(element: Any) -> bool:
    """只遍历导航项，避免误触菜单中的写操作。"""
    try:
        text = (element.inner_text(timeout=300) or "").strip().lower()
    except Exception:
        return False
    blocked_words = ("删除", "新增", "新建", "保存", "提交", "退出", "logout", "delete", "create", "save", "submit")
    return not any(word in text for word in blocked_words)


def _skip_recorded_request(url: str) -> bool:
    """过滤脚本、图片、字体等静态资源，避免污染业务接口池。"""
    parsed = urlparse(url)
    path = parsed.path or ""
    return path.startswith(STATIC_PATH_PREFIXES) or bool(STATIC_EXT.search(url))


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


def _try_form_login(page: Any, account: dict[str, Any]) -> bool:
    """token 注入不可用时尝试常见登录表单；失败返回 False 交由上层记录。"""
    username = str(account.get("username") or "")
    password = str(account.get("password") or "")
    if not username or not password:
        return False
    try:
        user_locator = page.locator(
            'input[name="username"], input[name="userName"], input[name="account"], '
            'input[type="text"], input[placeholder*="账号"], input[placeholder*="用户名"], '
            'input[placeholder*="用户"], input[placeholder*="手机"], input[placeholder*="邮箱"]'
        ).first
        password_locator = page.locator(
            'input[type="password"], input[name="password"], input[placeholder*="密码"]'
        ).first
        if not user_locator.count() or not password_locator.count():
            return False
        user_locator.fill(username, timeout=1200)
        password_locator.fill(password, timeout=1200)
        login_button = page.locator(
            'button:has-text("登录"), button:has-text("登 录"), button:has-text("Login"), '
            'input[type="submit"], [role="button"]:has-text("登录")'
        ).first
        if login_button.count():
            login_button.click(timeout=1500)
        else:
            password_locator.press("Enter", timeout=800)
        page.wait_for_load_state("networkidle", timeout=8000)
        return True
    except Exception:
        return False


def _dedupe_captured_requests(captured: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按归一化 method+path 去重，并把查询串拆成可编辑 params。"""
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for req in captured:
        key = _request_key(req["method"], req["path"])
        if key in seen:
            continue
        seen.add(key)
        parsed = urlparse(req.get("full_url") or req["path"])
        deduped.append({
            "method": req["method"],
            # 日期、分页和组织 ID 属于参数；留在 path 会制造重复接口和过期计划。
            "path": parsed.path or req["path"],
            "params": dict(parse_qsl(parsed.query, keep_blank_values=True)),
            "full_url": req.get("full_url", ""),
            "module": _module_from_path(req["path"]),
            "source_role": req.get("source_role", ""),
            "source_username": req.get("source_username", ""),
            "source_page": req.get("source_page", ""),
            "source_page_title": req.get("source_page_title", ""),
            "source_section": req.get("source_section", ""),
            "resource_type": req.get("resource_type", ""),
            "headers": req.get("headers") or {},
            "body": req.get("body") or "",
            "sample_response": req.get("sample_response") or {},
        })
    return deduped


def _merge_recorded_endpoints(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """合并重复接口，同时保留所有来源角色、页面和菜单章节作为证据集合。"""
    merged_seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    source_map: dict[str, set[str]] = defaultdict(set)
    username_map: dict[str, set[str]] = defaultdict(set)
    page_map: dict[str, set[str]] = defaultdict(set)
    title_map: dict[str, set[str]] = defaultdict(set)
    section_map: dict[str, set[str]] = defaultdict(set)
    for ep in raw:
        key = _request_key(ep["method"], ep["path"])
        source_map[key].add(ep.get("source_role", ""))
        if ep.get("source_username"):
            username_map[key].add(ep["source_username"])
        if ep.get("source_page"):
            page_map[key].add(ep["source_page"])
        if ep.get("source_page_title"):
            title_map[key].add(ep["source_page_title"])
        if ep.get("source_section"):
            section_map[key].add(ep["source_section"])
        if key in merged_seen:
            continue
        merged_seen.add(key)
        merged.append({**ep, "source_roles": sorted(source_map[key]), "source_usernames": sorted(username_map[key])})
    for ep in merged:
        key = _request_key(ep["method"], ep["path"])
        ep["source_roles"] = sorted(source_map[key])
        ep["source_usernames"] = sorted(username_map[key])
        ep["source_pages"] = sorted(page_map[key])
        ep["source_page_titles"] = sorted(title_map[key])
        ep["source_sections"] = sorted(section_map[key]) or [UNCAPTURED_SECTION]
        ep["source_section"] = ep["source_sections"][0]
    return merged


class ManualRecordingSession:
    """由人工操作的可见 Playwright 会话。

    会话在线程中运行，以 Event 表达 starting/ready/stopping/done，供 HTTP 状态接口
    查询；``_result`` 无论成功或异常都会产出结构一致的归档，便于前端恢复。
    """

    def __init__(self, project: dict[str, Any], project_dir: Path, role_name: str):
        self.project = apply_goal_scope_to_project(project)
        self.project_dir = project_dir
        self.role_name = role_name
        self.session_id = f"{datetime.now().strftime('%Y%m%d%H%M%S')}-{os.getpid()}-{time.time_ns()}"
        self.started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.stop_event = threading.Event()
        self.ready_event = threading.Event()
        self.done_event = threading.Event()
        self.error = ""
        self.result: dict[str, Any] | None = None
        self.captured: list[dict[str, Any]] = []
        self.pages: set[str] = set()
        self.thread = threading.Thread(target=self._run, name=f"manual-recorder-{self.session_id}", daemon=True)

    def start(self) -> None:
        """非阻塞启动录制线程；是否 ready 由状态接口继续查询。"""
        self.thread.start()

    def stop(self, timeout: float = 30.0) -> dict[str, Any]:
        """通知浏览器安全收尾并等待结果；超时只返回 stopping，不强杀线程。"""
        self.stop_event.set()
        self.thread.join(timeout)
        if self.thread.is_alive():
            return {
                "session_id": self.session_id,
                "status": "stopping",
                "error": "浏览器录制会话仍在关闭中，请稍后再次点击停止。",
            }
        return self.result or self._result()

    def status(self) -> dict[str, Any]:
        """返回不含账号密码的轻量状态快照。"""
        state = "done" if self.done_event.is_set() else "ready" if self.ready_event.is_set() else "starting"
        if self.error and not self.done_event.is_set():
            state = "error"
        return {
            "session_id": self.session_id,
            "status": state,
            "role": self.role_name,
            "started_at": self.started_at,
            "captured_count": len(self.captured),
            "page_count": len(self.pages),
            "error": self.error,
        }

    def _result(self) -> dict[str, Any]:
        """构造 UI 去重清单与完整来源证据，并写入会话 JSON/HAR 索引。"""
        deduped = _dedupe_captured_requests(self.captured)
        # UI 表格需要去重，但证据合并必须看到全部观察，才能保留一个接口的多个菜单来源。
        merged = _merge_recorded_endpoints(self.captured)
        save_dir = self.project_dir / "interface_records"
        save_dir.mkdir(parents=True, exist_ok=True)
        result = {
            "session_id": self.session_id,
            "started_at": self.started_at,
            "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "mode": "manual-playwright",
            "enabled": True,
            "base_url": str(self.project.get("base_url") or "").rstrip("/"),
            "role": self.role_name,
            "roles": {
                self.role_name: {
                    "endpoints": deduped,
                    "count": len(deduped),
                    "pages": sorted(self.pages),
                    "error": self.error,
                }
            },
            "total_endpoints": len(merged),
            "merged_endpoints": merged,
            "all_pages": sorted(self.pages),
            "har_path": "",
            "error": self.error,
        }
        session_dir = save_dir / "manual_sessions"
        session_dir.mkdir(parents=True, exist_ok=True)
        har_path = session_dir / f"{self.session_id}.har"
        if har_path.exists():
            result["har_path"] = har_path.as_posix()
        (session_dir / f"{self.session_id}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result

    def _run(self) -> None:
        """线程主循环：登录、启动浏览器、监听请求，直到用户点击停止后归档。"""
        base_url = str(self.project.get("base_url") or "").rstrip("/")
        if not base_url:
            self.error = "base_url 未配置，无法启动人工录制。"
            self.result = self._result()
            self.ready_event.set()
            self.done_event.set()
            return

        from idor_workbench.domains.idor.frontend_probe import FrontendAuthClient

        accounts = _accounts_from_roles(self.project)
        account = accounts.get(self.role_name)
        if not account:
            self.error = f"未找到角色账号：{self.role_name}"
            self.result = self._result()
            self.ready_event.set()
            self.done_event.set()
            return

        auth_client = FrontendAuthClient(self.project)
        try:
            token = auth_client.get_token(account.get("username", ""), account.get("password", ""), account.get("login_type", "WEB"))
        finally:
            auth_client.close()

        session_dir = self.project_dir / "interface_records" / "manual_sessions"
        session_dir.mkdir(parents=True, exist_ok=True)
        har_path = session_dir / f"{self.session_id}.har"

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.error = "缺少 playwright 依赖。"
            self.result = self._result()
            self.ready_event.set()
            self.done_event.set()
            return

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(**_browser_launch_options(False))
                headers = {"Authorization": f"Bearer {token}"} if token else {}
                context = browser.new_context(
                    extra_http_headers=headers,
                    ignore_https_errors=True,
                    record_har_path=str(har_path),
                )
                if token:
                    try:
                        parsed_base = urlparse(base_url)
                        domain = parsed_base.hostname or ""
                        if domain:
                            context.add_cookies([{
                                "name": "Authorization",
                                "value": f"Bearer {token}",
                                "domain": domain,
                                "path": "/",
                                "httpOnly": False,
                                "secure": parsed_base.scheme == "https",
                                "sameSite": "Lax",
                            }, {
                                "name": "token",
                                "value": token,
                                "domain": domain,
                                "path": "/",
                                "httpOnly": False,
                                "secure": parsed_base.scheme == "https",
                                "sameSite": "Lax",
                            }])
                    except Exception:
                        pass
                context.add_init_script(
                    f"""(() => {{
                      const token = {json.dumps(token)};
                      if (token) {{
                        ['Authorization', 'authorization'].forEach(key => {{
                          localStorage.setItem(key, 'Bearer ' + token);
                          sessionStorage.setItem(key, 'Bearer ' + token);
                        }});
                        ['token', 'accessToken', 'access_token', 'Admin-Token', 'X-Token'].forEach(key => {{
                          localStorage.setItem(key, token);
                          sessionStorage.setItem(key, token);
                        }});
                      }}
                      const clean = value => String(value || '').replace(/\\s+/g, ' ').trim().slice(0, 80);
                      const ignored = /^(x|ok|cancel|submit|save|delete|logout|刷新|重置|查询|关闭|取消|确定|保存|删除|退出)$/i;
                      const navSelector = 'nav,aside,[role="navigation"],[role="menu"],.ant-menu,.el-menu,[class*="sidebar"],[class*="sider"],[class*="side-menu"],[class*="menu"]';
                      const itemSelector = 'a[href],[role="menuitem"],[role="tab"],.ant-menu-item,.el-menu-item,.menu-item,.nav-item,.sidebar-item,.sider-item';
                      const inNav = node => !!(node && node.closest && node.closest(navSelector));
                      const currentRoute = () => {{
                        const path = (location.pathname || '/').replace(/\\/$/, '') || '/';
                        return location.hash ? `${{path}}${{location.hash}}` : path;
                      }};
                      const saveSection = (title, source) => {{
                        const payload = {{title, time: Date.now(), source, route: currentRoute()}};
                        window.__idorLastSection = payload;
                        try {{
                          sessionStorage.setItem('__idorLastSection', JSON.stringify(payload));
                          const routes = JSON.parse(sessionStorage.getItem('__idorRouteSections') || '{{}}');
                          routes[currentRoute()] = title;
                          sessionStorage.setItem('__idorRouteSections', JSON.stringify(routes));
                        }} catch (_) {{}}
                      }};
                      const menuNode = event => {{
                        const path = event && event.composedPath ? event.composedPath() : [];
                        for (const item of path) {{
                          if (!item || item.nodeType !== 1) continue;
                          const role = item.getAttribute('role') || '';
                          const classes = String(item.className || '').toLowerCase();
                          const isMenu = role === 'menuitem' || role === 'tab'
                            || item.matches(itemSelector)
                            || (inNav(item) && /(^|[-_\\s])(item|link|entry|option)([-_\\s]|$)/.test(classes));
                          if (isMenu) return item;
                          if (inNav(item)) {{
                            const candidate = item.closest(itemSelector);
                            if (candidate) return candidate;
                          }}
                        }}
                        return null;
                      }};
                      const captureSection = event => {{
                        const node = menuNode(event);
                        const labelledChild = node && [...node.querySelectorAll('span,div,p,a')].find(child => child.children.length === 0 && clean(child.textContent));
                        const title = clean(node && (node.getAttribute('aria-label') || node.getAttribute('data-menu-title') || node.getAttribute('title') || (labelledChild && labelledChild.textContent) || node.innerText || node.textContent));
                        if (!title || title.length > 48 || ignored.test(title)) return;
                        saveSection(title, 'nav-click');
                        setTimeout(() => saveSection(title, 'nav-click-after-route'), 300);
                        setTimeout(() => saveSection(title, 'nav-click-after-route'), 900);
                      }};
                      document.addEventListener('pointerdown', captureSection, true);
                      document.addEventListener('click', captureSection, true);
                    }})();"""
                )

                def capture(request: Any) -> None:
                    url = request.url
                    if _skip_recorded_request(url):
                        return
                    if not API_PATH_RE.search(url) and "application/json" not in (request.headers.get("accept") or ""):
                        if request.resource_type in {"document", "stylesheet", "image", "media", "font", "script"}:
                            return
                    try:
                        parsed = urlparse(url)
                        source_page = ""
                        try:
                            req_page = request.frame.page
                            source_page = _page_route(req_page.url)
                            source_title = req_page.title()
                            source_section = _source_section_from_page(req_page)
                        except Exception:
                            pages = context.pages
                            source_page = _page_route(pages[-1].url) if pages else ""
                            source_title = pages[-1].title() if pages else ""
                            source_section = _source_section_from_page(pages[-1]) if pages else ""
                        if source_page:
                            self.pages.add(source_page)
                        self.captured.append({
                            "request_id": id(request),
                            "method": request.method,
                            "path": parsed.path + ("?" + parsed.query if parsed.query else ""),
                            "full_url": url,
                            "headers": dict(request.headers),
                            "body": request.post_data or "",
                            "resource_type": request.resource_type,
                            "source_role": self.role_name,
                            "source_page": source_page,
                            "source_page_title": source_title[:160],
                            "source_section": source_section or UNCAPTURED_SECTION,
                        })
                    except Exception:
                        pass

                def capture_finished(request: Any) -> None:
                    try:
                        response = request.response()
                        if not response:
                            return
                        for item in reversed(self.captured):
                            if item.get("request_id") == id(request):
                                content_type = response.headers.get("content-type", "")
                                body = response.text()[:10000] if ("json" in content_type or "text" in content_type) else ""
                                item["sample_response"] = {"status_code": response.status, "headers": dict(response.headers), "body": body}
                                break
                    except Exception:
                        pass

                def attach_page(page: Any) -> None:
                    page.on("request", capture)
                    page.on("requestfinished", capture_finished)
                    page.on("framenavigated", lambda frame: self.pages.add(_page_route(page.url)) if frame == page.main_frame else None)

                context.on("page", attach_page)
                page = context.new_page()
                attach_page(page)
                page.goto(base_url, wait_until="domcontentloaded", timeout=15000)
                if token:
                    page.wait_for_timeout(800)
                if "login" in page.url.lower():
                    _try_form_login(page, account)
                self.pages.add(_page_route(page.url))
                # The landing page may already be a business module (for
                # example, a security administrator landing on "成员管理").
                # Seed the menu context without requiring an extra click.
                for _ in range(12):
                    initial_section = _source_section_from_page(page)
                    if initial_section:
                        try:
                            page.evaluate(
                                """title => {
                                  const route = (() => {
                                    const path = (location.pathname || '/').replace(/\\/$/, '') || '/';
                                    return location.hash ? `${path}${location.hash}` : path;
                                  })();
                                  const payload = {title, time: Date.now(), source: 'initial-active-menu', route};
                                  window.__idorLastSection = payload;
                                  sessionStorage.setItem('__idorLastSection', JSON.stringify(payload));
                                  const routes = JSON.parse(sessionStorage.getItem('__idorRouteSections') || '{}');
                                  routes[route] = title;
                                  sessionStorage.setItem('__idorRouteSections', JSON.stringify(routes));
                                }""",
                                initial_section,
                            )
                        except Exception:
                            pass
                        break
                    time.sleep(0.25)
                self.ready_event.set()

                while not self.stop_event.is_set():
                    if not context.pages:
                        break
                    time.sleep(0.5)

                context.close()
                browser.close()
        except Exception as exc:
            self.error = str(exc)
        finally:
            self.result = self._result()
            self.ready_event.set()
            self.done_event.set()


def record_endpoints(project: dict[str, Any], project_dir: Path) -> dict[str, Any]:
    """按角色录制接口请求，返回按角色分组的去重接口清单。

    流程：
    1. 用现有的 auth_client 为每个角色获取 token
    2. Playwright 打开浏览器，注入 token
    3. page.on("request") 拦截所有 XHR/fetch 请求
    4. BFS 点击菜单项触发各页面的 API 调用
    5. 去重、过滤静态资源、按角色分组输出

    这是自动遍历兼容入口；工作台主交互优先使用 ``ManualRecordingSession``，因为
    人工操作能覆盖复杂菜单、验证码和业务前置条件，且风险更可控。

    返回格式：
    {
      "roles": {
        "系统管理员": {
          "endpoints": [{"method":"GET","path":"/api/admin/users","module":"admin","source_role":"系统管理员"}, ...],
          "count": 15
        },
        ...
      },
      "total_endpoints": 42,
      "merged_endpoints": [...],  # 全角色合并去重
    }
    """
    project = apply_goal_scope_to_project(project)
    base_url = str(project.get("base_url") or "").rstrip("/")
    if not base_url:
        return _error_result("base_url 未配置，无法录制接口。")

    from idor_workbench.domains.idor.frontend_probe import FrontendAuthClient

    auth_client = FrontendAuthClient(project)
    accounts = _accounts_from_roles(project)
    tokens: dict[str, str] = {}
    login_errors: list[str] = []

    for role_name, account in accounts.items():
        token = auth_client.get_token(
            account.get("username", ""),
            account.get("password", ""),
            account.get("login_type", "WEB"),
        )
        if token:
            tokens[role_name] = token
        else:
            login_errors.append(f"{role_name}: 登录失败，跳过接口录制。")
    auth_client.close()

    if not tokens:
        return _error_result("所有角色登录失败: " + "; ".join(login_errors), login_errors)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return _error_result("缺少 playwright 依赖。")

    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    role_results: dict[str, Any] = {}
    all_raw: list[dict[str, Any]] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(**_browser_launch_options(True))
        try:
            for role_name, token in tokens.items():
                captured: list[dict[str, Any]] = []
                error = ""
                account = accounts.get(role_name, {})
                try:
                    context = browser.new_context(
                        extra_http_headers={"Authorization": f"Bearer {token}"},
                        ignore_https_errors=True,
                    )
                    context.add_init_script(
                        f"""(() => {{
                          localStorage.setItem('Authorization', 'Bearer ' + {json.dumps(token)});
                          sessionStorage.setItem('Authorization', 'Bearer ' + {json.dumps(token)});
                        }})();"""
                    )
                    page = context.new_page()
                    page.on("dialog", lambda dialog: dialog.dismiss())

                    # 拦截网络请求
                    def _capture(
                        request: Any,
                        captured_rows: list[dict[str, Any]] = captured,
                        source_role: str = role_name,
                        source_username: str = str(account.get("username", "")),
                        active_page: Any = page,
                    ) -> None:
                        url = request.url
                        if _skip_recorded_request(url):
                            return
                        if not API_PATH_RE.search(url) and "application/json" not in (request.headers.get("accept") or ""):
                            # 非 API 请求，可能是页面导航，跳过
                            if request.resource_type in {"document", "stylesheet", "image", "media", "font", "script"}:
                                return
                        try:
                            parsed = urlparse(url)
                            captured_rows.append({
                                "method": request.method,
                                "path": parsed.path + ("?" + parsed.query if parsed.query else ""),
                                "full_url": url,
                                "headers": dict(request.headers),
                                "resource_type": request.resource_type,
                                "source_role": source_role,
                                "source_username": source_username,
                                "source_page": _page_route(active_page.url),
                            })
                        except Exception:
                            pass

                    page.on("request", _capture)

                    # BFS 爬菜单
                    page.goto(base_url, wait_until="domcontentloaded", timeout=15000)
                    page.wait_for_timeout(2000)

                    visited: set[str] = {_page_route(page.url)}
                    clickables: list[Any] = []
                    for sel in [
                        "[role='menuitem']",
                        ".ant-menu-item",
                        ".el-menu-item",
                        ".menu-item",
                        ".nav-item",
                        "[class*='menu']:not([class*='submenu'])",
                        "nav a",
                    ]:
                        try:
                            for el in page.locator(sel).all():
                                if el.is_visible() and _is_safe_navigation_target(el):
                                    clickables.append(el)
                        except Exception:
                            continue

                    ptr = 0
                    while ptr < len(clickables) and ptr < 80:
                        el = clickables[ptr]
                        ptr += 1
                        try:
                            el.click()
                            page.wait_for_timeout(800)
                            key = _page_route(page.url)
                            if key not in visited:
                                visited.add(key)
                                for sel in [
                                    "[role='menuitem']",
                                    ".ant-menu-item",
                                    ".el-menu-item",
                                    ".nav-item",
                                    "[class*='menu']",
                                    "nav a",
                                ]:
                                    try:
                                        for sub_el in page.locator(sel).all():
                                            if sub_el.is_visible() and _is_safe_navigation_target(sub_el):
                                                clickables.append(sub_el)
                                    except Exception:
                                        continue
                        except Exception:
                            continue

                    page.wait_for_timeout(1500)
                    context.close()
                except Exception as exc:
                    error = str(exc)

                deduped = _dedupe_captured_requests(captured)
                all_raw.extend(deduped)
                role_results[role_name] = {"endpoints": deduped, "count": len(deduped), "error": error}
        finally:
            browser.close()

    merged = _merge_recorded_endpoints(all_raw)

    # 保存到项目目录
    save_dir = project_dir / "interface_records"
    save_dir.mkdir(parents=True, exist_ok=True)
    result = {
        "started_at": started_at,
        "enabled": True,
        "base_url": base_url,
        "roles": role_results,
        "total_endpoints": len(merged),
        "merged_endpoints": merged,
        "login_errors": login_errors,
    }
    (save_dir / "recorded_endpoints.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return result


def _error_result(message: str, login_errors: list[str] | None = None) -> dict[str, Any]:
    return {
        "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "enabled": False,
        "base_url": "",
        "roles": {},
        "total_endpoints": 0,
        "merged_endpoints": [],
        "login_errors": login_errors or [],
        "error": message,
    }
