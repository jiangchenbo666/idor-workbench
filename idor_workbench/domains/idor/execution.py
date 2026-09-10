"""异步 IDOR/越权接口执行引擎。

这个模块是面向 Web 工作台的主执行引擎，使用 httpx.AsyncClient 并发发送接口请求。
每次运行的可变数据都放进 TestContext，避免多个网页任务同时执行时共享断言规则或运行变量。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import urljoin

import httpx

from idor_workbench.domains.idor.assertions import classify_response, expected_text, new_profile, update_profile
from idor_workbench.domains.idor.scope import apply_goal_scope_to_project

LOGGER = logging.getLogger("idor.engine")


def log_info(trace_id: str, message: str) -> None:
    LOGGER.info(message, extra={"trace_id": trace_id})


def log_warning(trace_id: str, message: str) -> None:
    LOGGER.warning(message, extra={"trace_id": trace_id})

# 单次运行上下文：把项目配置快照和运行期状态装进一个对象。
# 它提供的是“内存状态隔离”，不是操作系统/容器级 sandbox。
# 多个项目/用户并发执行时，各自持有独立 TestContext，避免 token、运行变量和断言学习结果互相串写。
@dataclass
class TestContext:
    """单次测试运行的上下文对象。

    账号、接口、运行时变量、断言学习结果、并发数都放在这里。
    这样同一台服务器上多个用户同时跑测试时，不会因为共享全局可变对象而污染彼此的运行状态。
    注意：这不是安全边界；文件目录、进程、网络访问和用户权限隔离需要由部署层/任务队列/权限系统保证。
    """
    base_url: str
    token_url: str
    token_login_type: str = "WEB"
    auth_method: str = "json_post"
    username_field: str = "username"
    password_field: str = "password"
    token_path: str = "tokenInfo.accessToken"
    request_timeout: int = 10
    accounts: dict[str, dict[str, Any]] = field(default_factory=dict)
    endpoints: list[dict[str, Any]] = field(default_factory=list)
    runtime_variables: dict[str, Any] = field(default_factory=dict)
    assertion_profile: dict[str, Any] = field(default_factory=new_profile)
    concurrency: int = 10

    @classmethod
    def from_project(cls, project: dict[str, Any], runtime_variables: dict[str, Any] | None = None) -> TestContext:
        """把保存的 project.json 转成异步执行引擎需要的精简结构。"""
        project = apply_goal_scope_to_project(project)
        auth = project.get("auth") or {}
        accounts: dict[str, dict[str, Any]] = {}
        for role in project.get("roles") or []:
            name = role.get("name")
            if not name:
                continue
            accounts[name] = {
                "username": role.get("username", ""),
                "password": role.get("password", ""),
                "login_type": role.get("login_type", auth.get("login_type", "WEB")),
            }
        try:
            concurrency = int(project.get("api_concurrency") or 10)
        except (TypeError, ValueError):
            concurrency = 10
        return cls(
            base_url=str(project.get("base_url") or "").rstrip("/"),
            token_url=str(auth.get("token_url") or ""),
            token_login_type=str(auth.get("login_type") or "WEB"),
            auth_method=str(auth.get("auth_method") or "json_post"),
            username_field=str(auth.get("username_field") or "username"),
            password_field=str(auth.get("password_field") or "password"),
            token_path=str(auth.get("token_path") or "tokenInfo.accessToken"),
            request_timeout=int(project.get("request_timeout") or 10),
            accounts=accounts,
            endpoints=list(project.get("endpoints") or []),
            runtime_variables=dict(runtime_variables or {}),
            concurrency=max(1, min(30, concurrency)),
        )


def _resolve_runtime_variables(value: Any, variables: dict[str, Any]) -> Any:
    """用业务场景准备步骤提取出的变量，替换请求里的 {{variable}} 占位符。"""
    if isinstance(value, str):
        return re.sub(
            r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}",
            lambda match: str(variables.get(match.group(1), match.group(0))),
            value,
        )
    if isinstance(value, list):
        return [_resolve_runtime_variables(item, variables) for item in value]
    if isinstance(value, dict):
        return {key: _resolve_runtime_variables(item, variables) for key, item in value.items()}
    return value


def _value_by_path(data: Any, path: str) -> Any:
    current = data
    for part in [item for item in str(path or "").split(".") if item]:
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            current = current[int(part)]
        else:
            return None
    return current


def _extract_token_from_response(data: Any, token_path: str = "") -> str | None:
    """优先按用户配置路径取 token，再兼容常见 accessToken/token 响应结构。"""
    configured = _value_by_path(data, token_path)
    if configured:
        return str(configured)
    if not isinstance(data, dict):
        return data if isinstance(data, str) else None
    if isinstance(data.get("tokenInfo"), dict):
        for key in ("accessToken", "token"):
            if data["tokenInfo"].get(key):
                return str(data["tokenInfo"][key])
    if isinstance(data.get("data"), dict):
        for key in ("accessToken", "token", "access_token"):
            if data["data"].get(key):
                return str(data["data"][key])
    for key in ("accessToken", "token", "access_token"):
        if data.get(key):
            return str(data[key])
    return None


def _should_be_blocked(role_name: str, endpoint: dict[str, Any]) -> bool:
    """判断当前角色是否在接口允许角色集合之外，即本请求是否期望被拒绝。"""
    return role_name not in (endpoint.get("allowed_roles") or [])


def _body_preview(body: str, limit: int = 120) -> str:
    return (body or "")[:limit].replace("\n", " ").replace("\r", "")


_SENSITIVE_REQUEST_KEYS = {"authorization", "cookie", "set_cookie", "password", "passwd", "token", "access_token", "refresh_token", "secret"}


def _redact_request_value(value: Any) -> Any:
    """递归脱敏请求证据中的密码、Cookie、token 和 Authorization。"""
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if str(key).lower().replace("-", "_") in _SENSITIVE_REQUEST_KEYS else _redact_request_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_request_value(item) for item in value]
    return value


def _safe_request_snapshot(request: dict[str, Any]) -> dict[str, Any]:
    """保留可复现请求结构，但不把 Bearer 凭据写入结果和报告。"""
    return {
        **request,
        "headers": _redact_request_value(request.get("headers") or {}),
        "body": _redact_request_value(request.get("body")),
        "authorization": "[REDACTED]" if request.get("authorization") else "",
    }


def _prepare_request(context: TestContext, token: str, endpoint: dict[str, Any]) -> dict[str, Any]:
    """把 base_url、接口配置、角色 token 组合成真正要发送的 HTTP 请求。"""
    path = _resolve_runtime_variables(endpoint.get("path") or "", context.runtime_variables)
    url = urljoin(f"{context.base_url}/", str(path).lstrip("/"))
    method = str(endpoint.get("method") or "GET").upper()
    params = _resolve_runtime_variables(endpoint.get("params", {}) or {}, context.runtime_variables)
    req_body = _resolve_runtime_variables(endpoint.get("body", None), context.runtime_variables)
    raw_body = _resolve_runtime_variables(endpoint.get("raw_body", None), context.runtime_variables)
    if req_body is not None:
        req_body = json.loads(json.dumps(req_body).replace("{TIMESTAMP}", datetime.now().strftime("%H%M%S%f")))
    headers = {"Authorization": f"Bearer {token}"}
    headers.update(_resolve_runtime_variables(endpoint.get("headers", {}) or {}, context.runtime_variables))
    if raw_body is not None:
        headers.setdefault("Content-Type", "application/json")
    return {
        "method": method,
        "url": url,
        "path": path,
        "params": params,
        "body": req_body,
        "raw_body": raw_body,
        "headers": headers,
        "authorization": headers.get("Authorization", ""),
    }


async def get_token(client: httpx.AsyncClient, context: TestContext, role_name: str, account: dict[str, Any]) -> tuple[str, str | None, str]:
    """为一个角色获取 token；按项目配置动态选择鉴权请求方式。"""
    username = account.get("username", "")
    password = account.get("password", "")
    login_type = account.get("login_type", context.token_login_type)
    auth_method = (context.auth_method or "json_post").lower()
    if not username or (auth_method != "get_no_password" and not password):
        return role_name, None, "账号/密码未配置"
    url = urljoin(f"{context.base_url}/", str(context.token_url).lstrip("/"))
    try:
        headers = {"XloginType": login_type}
        if auth_method == "form_post":
            resp = await client.post(
                url,
                data={context.username_field: username, context.password_field: password, "grant_type": "password", "loginType": login_type},
                headers=headers,
            )
        elif auth_method == "get_no_password":
            resp = await client.get(
                url,
                params={"username": username, "loginType": login_type},
                headers=headers,
            )
        else:
            resp = await client.post(
                url,
                json={context.username_field: username, context.password_field: password},
                headers={**headers, "Content-Type": "application/json"},
            )
        try:
            data = resp.json()
        except Exception:
            return role_name, None, f"登录响应不是 JSON: HTTP {resp.status_code} {resp.text[:120]}"
        if resp.status_code >= 400:
            return role_name, None, f"登录失败 HTTP {resp.status_code}: {str(data)[:120]}"
        token = _extract_token_from_response(data, context.token_path)
        return role_name, token, "" if token else "响应中没有找到 token"
    except Exception as exc:
        return role_name, None, str(exc)


async def send_request(client: httpx.AsyncClient, context: TestContext, token: str, endpoint: dict[str, Any]) -> dict[str, Any]:
    """使用 httpx.AsyncClient 为某个角色发送一条接口请求。"""
    request_snapshot = _prepare_request(context, token, endpoint)
    try:
        resp = await client.request(
            request_snapshot["method"],
            request_snapshot["url"],
            params=request_snapshot["params"],
            headers=request_snapshot["headers"],
            content=request_snapshot.get("raw_body") if request_snapshot.get("raw_body") is not None else None,
            json=request_snapshot["body"] if request_snapshot["method"] != "GET" and request_snapshot.get("raw_body") is None else None,
        )
        return {
            "status_code": resp.status_code,
            "body": resp.text,
            "error": None,
            "request": _safe_request_snapshot(request_snapshot),
            "response_headers": dict(resp.headers),
        }
    except Exception as exc:
        return {
            "status_code": 0,
            "body": "",
            "error": str(exc),
            "request": _safe_request_snapshot(request_snapshot),
            "response_headers": {},
        }


async def send_request_with_refresh(
    client: httpx.AsyncClient,
    context: TestContext,
    role_name: str,
    account: dict[str, Any],
    token_state: dict[str, str],
    endpoint: dict[str, Any],
    trace_id: str,
) -> dict[str, Any]:
    """发送请求；如果 token 过期返回 401，则刷新 token 后重试一次。"""
    result = await send_request(client, context, token_state["token"], endpoint)
    if result.get("status_code") != 401:
        return result

    log_warning(trace_id, f"{role_name} token 可能已过期，正在重新登录并重试 {endpoint.get('method', 'GET')} {endpoint.get('path', '')}")
    _, new_token, error = await get_token(client, context, role_name, account)
    if not new_token:
        result["error"] = result.get("error") or f"token refresh failed: {error}"
        return result
    token_state["token"] = new_token
    retried = await send_request(client, context, new_token, endpoint)
    retried["retried_after_401"] = True
    return retried


def _classify_row(context: TestContext, role_name: str, endpoint: dict[str, Any], result: dict[str, Any], section: str) -> dict[str, Any]:
    """把原始 HTTP 响应转换成前端表格和 Word 报告统一使用的结果行。

    ``section=自身权限`` 时期望业务成功，``section=越权`` 时期望业务拒绝。
    断言学习档案先观察两类样本，再由 ``classify_response`` 综合 HTTP 状态、
    业务码、成功标志和返回数据判断；不能只因为 HTTP 200 就认定测试通过。
    """
    method = endpoint.get("method", "GET")
    request_info = result.get("request", {})
    if result.get("error"):
        return {
            "role": role_name,
            "section": section,
            "status": "ERROR",
            "name": endpoint.get("name", ""),
            "path": endpoint.get("path", ""),
            "method": request_info.get("method", method),
            "req_body": request_info.get("body", endpoint.get("body")),
            "request": request_info,
            "req_params": request_info.get("params", {}),
            "req_headers": request_info.get("headers", {}),
            "authorization": request_info.get("authorization", ""),
            "expected": "请求失败，未执行断言",
            "assertion": {},
            "status_code": 0,
            "body_preview": result.get("error", ""),
            "response_body": result.get("body", ""),
            "response_headers": result.get("response_headers", {}),
            "failures": [],
        }

    should_block = _should_be_blocked(role_name, endpoint)
    context.assertion_profile = update_profile(context.assertion_profile, result, should_block)
    classification = classify_response(result, should_block, context.assertion_profile)
    assertion_info = {
        "expected": expected_text(should_block, context.assertion_profile),
        "verdict": classification["verdict"],
        "confidence": classification["confidence"],
        "evidence": classification["evidence"],
        "profile": {
            "mode": context.assertion_profile.get("mode"),
            "success_field": context.assertion_profile.get("success_field"),
            "code_field": context.assertion_profile.get("code_field"),
            "message_field": context.assertion_profile.get("message_field"),
            "data_field": context.assertion_profile.get("data_field"),
            "success_values": context.assertion_profile.get("success_values"),
            "blocked_values": context.assertion_profile.get("blocked_values"),
            "success_codes": context.assertion_profile.get("success_codes"),
            "blocked_codes": context.assertion_profile.get("blocked_codes"),
        },
    }
    failures = []
    if not classification["passed"]:
        failures.append(
            f"{classification['verdict']}（置信度: {classification['confidence']}，依据: {'; '.join(classification['evidence'])}）"
        )
    result["assertion"] = assertion_info
    return {
        "role": role_name,
        "section": section,
        "status": "FAIL" if failures else "PASS",
        "name": endpoint.get("name", ""),
        "path": endpoint.get("path", ""),
        "method": request_info.get("method", method),
        "req_body": request_info.get("body", endpoint.get("body")),
        "request": request_info,
        "req_params": request_info.get("params", {}),
        "req_headers": request_info.get("headers", {}),
        "authorization": request_info.get("authorization", ""),
        "expected": assertion_info.get("expected", ""),
        "assertion": assertion_info,
        "status_code": result.get("status_code", 0),
        "body_preview": _body_preview(result.get("body", "")),
        "response_body": result.get("body", ""),
        "response_headers": result.get("response_headers", {}),
        "failures": failures,
    }


async def _run_endpoint_batch(
    client: httpx.AsyncClient,
    context: TestContext,
    semaphore: asyncio.Semaphore,
    role_name: str,
    account: dict[str, Any],
    token_state: dict[str, str],
    endpoints: list[dict[str, Any]],
    section: str,
    trace_id: str,
) -> list[dict[str, Any]]:
    """为一个角色并发执行多条接口请求，并用 semaphore 控制最大并发数。"""
    async def one(endpoint: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        async with semaphore:
            return endpoint, await send_request_with_refresh(client, context, role_name, account, token_state, endpoint, trace_id)

    pairs = await asyncio.gather(*(one(deepcopy(ep)) for ep in endpoints))
    return [_classify_row(context, role_name, endpoint, result, section) for endpoint, result in pairs]


async def collect_all_results_async(project: dict[str, Any], runtime_variables: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """异步执行主入口。

    runner 会把保存后的 project 字典传进来。执行顺序为：目标范围收窄 -> 并发登录
    -> 按角色拆分自身权限/越权接口 -> 限流请求 -> 规则断言 -> 低置信结果 AI 复核
    -> 汇总。返回值就是后续 ``run_results.json`` 的主体。

    被测环境常使用私有 CA，因此这里暂时允许自签名证书；这与 AI 服务连接的 TLS
    策略相互独立。生产环境应将目标 CA 注入运行容器，而不是长期关闭校验。
    """
    context = TestContext.from_project(project, runtime_variables)
    trace_id = uuid.uuid4().hex[:12]
    log_info(trace_id, f"IDOR 异步越权测试启动 env={context.base_url} concurrency={context.concurrency}")

    timeout = httpx.Timeout(context.request_timeout)
    limits = httpx.Limits(max_connections=context.concurrency + 5, max_keepalive_connections=context.concurrency + 5)
    async with httpx.AsyncClient(verify=False, follow_redirects=False, timeout=timeout, limits=limits, trust_env=False) as client:
        login_results = await asyncio.gather(*(
            get_token(client, context, role_name, account)
            for role_name, account in context.accounts.items()
        ))
        tokens: dict[str, str] = {}
        login_errors: list[str] = []
        for role_name, token, error in login_results:
            if token:
                tokens[role_name] = token
                log_info(trace_id, f"{role_name} 登录成功")
            else:
                login_errors.append(f"{role_name}: {error or '登录失败'}")
                log_warning(trace_id, f"{role_name} 登录失败: {error}")
        if not tokens:
            log_warning(trace_id, "没有成功登录的角色，终止执行")
            return {
                "env": context.base_url,
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "results": [],
                "total": 0,
                "passed": 0,
                "failed": 0,
                "skipped": 0,
                "roles": list(context.accounts),
                "login_errors": login_errors,
                "run_error": "没有成功获取 token 的角色，API 测试未执行。请检查账号、密码、getToken URL、Auth Method 和 Token 提取路径。",
                "assertion_profile": context.assertion_profile,
                "engine": {
                    "name": "async-httpx",
                    "concurrency": context.concurrency,
                    "trace_id": trace_id,
                    "phase": "auth",
                },
            }

        semaphore = asyncio.Semaphore(context.concurrency)
        rows: list[dict[str, Any]] = []
        for role_name, token in tokens.items():
            account = context.accounts.get(role_name, {})
            token_state = {"token": token}
            allowed_eps = [ep for ep in context.endpoints if not _should_be_blocked(role_name, ep)]
            blocked_eps = [ep for ep in context.endpoints if _should_be_blocked(role_name, ep)]
            log_info(trace_id, f"开始角色测试 role={role_name} allowed={len(allowed_eps)} blocked={len(blocked_eps)}")
            allowed_rows = await _run_endpoint_batch(client, context, semaphore, role_name, account, token_state, allowed_eps, "自身权限", trace_id)
            blocked_rows = await _run_endpoint_batch(client, context, semaphore, role_name, account, token_state, blocked_eps, "越权", trace_id)
            role_rows = allowed_rows + blocked_rows
            for row in role_rows:
                marker = "[PASS]" if row["status"] == "PASS" else "[FAIL]" if row["status"] == "FAIL" else "[ERROR]"
                log_info(trace_id, f"{marker} role={role_name} {row.get('method')} {row.get('path')} HTTP {row.get('status_code')} {row.get('body_preview')}")
            rows.extend(role_rows)

    # ── AI 深度分析通道：对 should_block=True 但 HTTP 200 返回正常数据的 case ──
    try:
        from idor_workbench.domains.idor.ai import analyze_idor_suspect

        # 构建 allowed 响应索引：用 (path, method) 找对应自身权限的结果
        allowed_index: dict[tuple[str, str], dict[str, Any]] = {}
        for r in rows:
            if r.get("section") == "自身权限":
                allowed_index[(r.get("path", ""), r.get("method", ""))] = r

        for row in rows:
            if row.get("section") != "越权":
                continue
            assertion = row.get("assertion") or {}
            verdict = assertion.get("verdict", "")
            confidence = assertion.get("confidence", "")
            status_code = row.get("status_code", 0)
            # 仅分析：本地断言认为是 FAIL 且属于"疑似越权"类型
            if "越权疑似" not in verdict and confidence not in ("低", "中"):
                continue
            if status_code != 200:
                continue

            allowed = allowed_index.get((row.get("path", ""), row.get("method", "")))
            ai_result = analyze_idor_suspect(row, allowed, project)
            if not ai_result.get("ai_analyzed"):
                continue

            # 用 AI 结果覆盖本地断言
            ai_verdict = ai_result.get("verdict", verdict)
            ai_passed = ai_result.get("passed", row["status"] == "FAIL")
            ai_confidence = ai_result.get("confidence", confidence)
            ai_reasoning = ai_result.get("reasoning", "")
            ai_sensitivity = ai_result.get("data_sensitivity", "")

            row["status"] = "PASS" if ai_passed else "FAIL"
            row["assertion"] = {
                **assertion,
                "verdict": f"AI复核: {ai_verdict}",
                "confidence": ai_confidence,
                "evidence": [*assertion.get("evidence", []), f"reasoning={ai_reasoning}"],
                "ai_reasoning": ai_reasoning,
                "data_sensitivity": ai_sensitivity,
                "data_owner": ai_result.get("data_owner", ""),
                "recommendation": ai_result.get("recommendation", ""),
                "signal": ai_result.get("signal", ""),
            }
            if not ai_passed:
                row["failures"] = [f"AI复核确认越权: {ai_reasoning}（置信度: {ai_confidence}）"]
            else:
                row["failures"] = []
            log_info(trace_id, f"[AI复核] role={row['role']} {row.get('method')} {row.get('path')} → {ai_verdict} 置信度={ai_confidence}")
    except Exception as ai_exc:
        log_warning(trace_id, f"AI深度分析通道异常: {ai_exc}")

    total = len(rows)
    passed = sum(1 for row in rows if row["status"] == "PASS")
    failed = sum(1 for row in rows if row["status"] == "FAIL")
    skipped = sum(1 for row in rows if row["status"] == "ERROR")
    log_info(trace_id, f"执行完成 total={total} passed={passed} failed={failed} skipped={skipped}")
    return {
        "env": context.base_url,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "results": rows,
        "total": total,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "roles": list(tokens.keys()),
        "login_errors": login_errors,
        "assertion_profile": context.assertion_profile,
        "engine": {
            "name": "async-httpx",
            "concurrency": context.concurrency,
            "trace_id": trace_id,
        },
    }


def collect_all_results(project: dict[str, Any], runtime_variables: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """同步包装函数，供子进程和报告生成链路调用；真正执行逻辑在 collect_all_results_async()。"""
    return asyncio.run(collect_all_results_async(project, runtime_variables))
