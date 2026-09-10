"""本地 AI 辅助分析模块。

职责边界：
1. 统一调用本地 Ollama 或 OpenAI-compatible 服务。
2. 为基础配置、接口导入、执行计划、执行结果和断言档案提供结构化分析。
3. 所有分析先生成确定性的本地规则结果；模型不可用时业务仍可继续。
4. AI 不直接发送被测请求，也不任意改项目；计划修改必须再经过 API 层白名单校验。
"""
from __future__ import annotations

import json  # L2 IDOR AI analysis adapter.
import os
import re
from datetime import datetime
from typing import Any

import httpx


def safe_text(value: Any, limit: int = 2000) -> str:
    """清理控制字符并限制上下文长度，避免脏数据破坏 prompt 或日志。"""
    text = "" if value is None else str(value)
    cleaned = []
    for ch in text:
        code = ord(ch)
        cleaned.append(ch if ch in "\n\r\t" or code >= 32 else " ")
    return "".join(cleaned)[:limit]


def ai_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """标准化一次 AI 调用配置；显式请求值优先于进程环境变量。"""
    config = config or {}
    provider = str(config.get("provider") or os.environ.get("IDOR_AI_PROVIDER", "disabled")).strip().lower()
    base_url = str(config.get("base_url") or os.environ.get("IDOR_AI_BASE_URL", "")).strip().rstrip("/")
    model = str(config.get("model") or os.environ.get("IDOR_AI_MODEL", "")).strip()
    api_key = str(config.get("api_key") or os.environ.get("IDOR_AI_API_KEY", "")).strip()
    try:
        timeout = int(str(config.get("timeout") or os.environ.get("IDOR_AI_TIMEOUT", "600")))
    except (TypeError, ValueError):
        timeout = 600
    try:
        temperature = float(str(config.get("temperature") if config.get("temperature") is not None else os.environ.get("IDOR_AI_TEMPERATURE", "0.2")))
    except (TypeError, ValueError):
        temperature = 0.2
    return {
        "provider": provider,
        "base_url": base_url,
        "model": model,
        "api_key": api_key,
        "timeout": timeout,
        "temperature": temperature,
    }


def provider_info(config: dict[str, Any], configured: bool, note: str = "", error: str = "") -> dict[str, Any]:
    """生成前端可展示的模型使用说明，不返回 API Key。"""
    cfg = ai_config(config)
    return {
        "mode": cfg["provider"],
        "configured": configured,
        "model": cfg["model"],
        "note": note,
        "error": error,
    }


def _chat_url(cfg: dict[str, Any]) -> str:
    if cfg["provider"] == "ollama":
        return cfg["base_url"] if cfg["base_url"].endswith("/api/chat") else f"{cfg['base_url']}/api/chat"
    return cfg["base_url"] if cfg["base_url"].endswith("/chat/completions") else f"{cfg['base_url']}/chat/completions"


def _extract_content(response: dict[str, Any]) -> str:
    # Ollama 格式
    if "message" in response:
        return str((response.get("message") or {}).get("content") or response.get("response") or "")
    # OpenAI-compatible 格式
    choices = response.get("choices") or []
    if not choices:
        return ""
    msg = choices[0].get("message") or {}
    content = str(msg.get("content") or "")
    return content


def parse_json(text: str) -> dict[str, Any]:
    """容错解析模型 JSON：依次尝试原文、外层对象和平衡大括号提取。"""
    import logging
    logger = logging.getLogger("idor.ai")
    cleaned = safe_text(text, 200000).strip()
    # 去掉 markdown code fence
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    if not cleaned:
        raise ValueError("AI 未返回可解析内容")
    # 策略 1: 直接解析
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    # 策略 2: 提取最外层 {}
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    # 策略 3: 用平衡大括号提取完整 JSON
    extracted = _extract_balanced_json(cleaned)
    if extracted:
        try:
            return json.loads(extracted)
        except json.JSONDecodeError:
            pass
    # 全部失败
    logger.error(f"parse_json failed, raw content (first 1000 chars): {cleaned[:1000]}")
    raise ValueError(f"AI 未返回可解析内容。原始响应前200字符: {cleaned[:200]}")


def _extract_balanced_json(text: str) -> str | None:
    """用栈匹配 {} 提取最外层的完整 JSON 对象。"""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for i, ch in enumerate(text[start:], start):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


# 统一模型调用入口：屏蔽 Ollama 与 OpenAI-compatible 请求/响应格式差异。
def call_ai(messages: list[dict[str, str]], config: dict[str, Any] | None = None, max_tokens: int = 50000) -> dict[str, Any]:
    """调用配置的模型并返回原始响应对象。

    这里处理不同供应商的 URL、鉴权、JSON 模式和少量兼容重试。调用者不应直接
    使用模型文本，而要继续经过 ``parse_json`` 和对应业务 schema/白名单校验。
    """
    import logging
    logger = logging.getLogger("idor.ai")
    cfg = ai_config(config)
    if not cfg["base_url"] or not cfg["model"]:
        raise ValueError("AI base_url/model 未配置")
    if cfg["provider"] in {"disabled", "server"}:
        raise ValueError("AI 未配置，已使用本地规则")
    if cfg["provider"] != "ollama" and not cfg["api_key"]:
        raise ValueError("OpenAI-compatible provider 需要 api_key")

    chat_url = _chat_url(cfg)
    body_size = len(json.dumps(messages, ensure_ascii=False))
    logger.info(f"AI call: provider={cfg['provider']} model={cfg['model']} url={chat_url} timeout={cfg['timeout']}s body={body_size}chars")

    if cfg["provider"] == "ollama":
        payload = {
            "model": cfg["model"],
            "messages": messages,
            "stream": False,
            "format": "json",
            "think": False,
            "options": {"temperature": cfg["temperature"], "num_predict": max_tokens},
        }
        headers = {}
    else:
        payload = {
            "model": cfg["model"],
            "messages": messages,
            "temperature": cfg["temperature"],
            "max_tokens": max_tokens,
            "thinking": {"type": "disabled"},  # 禁用 DeepSeek 思考模式，避免 thinking + content 总 token 超限导致 content 为空
        }
        # DeepSeek / openai-compatible providers: some do NOT support response_format
        if cfg["provider"] == "openai":
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {cfg['api_key']}", "Content-Type": "application/json"}

    import time
    t0 = time.time()
    resp = httpx.post(chat_url, json=payload, headers=headers, timeout=cfg["timeout"], verify=False)
    elapsed = time.time() - t0
    logger.info(f"AI response: status={resp.status_code} elapsed={elapsed:.1f}s")

    if resp.status_code >= 400:
        logger.warning(f"AI error body: {resp.text[:800]}")
        body_lower = resp.text.lower()
        # DeepSeek 模型名自动修正
        if resp.status_code == 400 and "model" in cfg.get("model", "").lower():
            if "supported" in body_lower and "model" in body_lower:
                cfg["model"] = "deepseek-v4-flash"
                payload["model"] = "deepseek-v4-flash"
                logger.info(f"AI auto-fix model -> {cfg['model']}")
                resp = httpx.post(chat_url, json=payload, headers=headers, timeout=cfg["timeout"], verify=False)
        # thinking 参数不兼容 → 去掉重试
        if resp.status_code == 400 and "thinking" in body_lower:
            payload.pop("thinking", None)
            logger.info("AI retry without thinking param")
            resp = httpx.post(chat_url, json=payload, headers=headers, timeout=cfg["timeout"], verify=False)
        if "response_format" in payload and resp.status_code >= 400:
            payload.pop("response_format", None)
            logger.info("AI retry without response_format")
            resp = httpx.post(chat_url, json=payload, headers=headers, timeout=cfg["timeout"], verify=False)
    resp.raise_for_status()
    result = resp.json()

    # DeepSeek v4 思考模式导致 content 为空 → 加大 max_tokens 重试
    if _extract_content(result) == "":
        logger.warning("AI returned empty content (thinking consumed all tokens), retrying with 2x max_tokens + thinking disabled")
        payload["max_tokens"] = max(payload.get("max_tokens", 50000) * 2, 100000)
        payload["thinking"] = {"type": "disabled"}
        resp = httpx.post(chat_url, json=payload, headers=headers, timeout=cfg["timeout"], verify=False)
        resp.raise_for_status()
        result = resp.json()
        if _extract_content(result) == "":
            raise ValueError("AI 连续两次返回空 content，请增大超时或换用更大上下文的模型")

    return result


# 结构化 Prompt 入口：统一系统约束、输入序列化和 provider 审计信息。
def ask_json(task: str, payload: dict[str, Any], schema_hint: str, config: dict[str, Any] | None = None, max_tokens: int = 50000) -> dict[str, Any]:
    """要求模型按字段提示返回 JSON，并为结果附加模型和生成时间。"""
    messages = [
        {
            "role": "system",
            "content": (
                "你是 IDOR/越权测试平台的本地 AI 分析引擎。"
                "只输出 JSON，不输出 Markdown。"
                "不要编造已执行事实；无法确定时要写入 uncertainty。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"任务：{task}\n"
                f"输出 JSON 字段要求：{schema_hint}\n"
                f"输入数据：\n{json.dumps(payload, ensure_ascii=False)}"
            ),
        },
    ]
    raw = call_ai(messages, config=config, max_tokens=max_tokens)
    parsed = parse_json(_extract_content(raw))
    parsed.setdefault("generated_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    parsed["provider"] = provider_info(config or {}, True, "已使用本地 AI 模型生成分析。")
    return parsed


def fallback(kind: str, summary: str, actions: list[str], config: dict[str, Any] | None = None, error: str = "") -> dict[str, Any]:
    """构造统一的本地规则降级响应，让前端无需按异常类型分叉。"""
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "provider": provider_info(config or {}, False, "AI 不可用，已使用本地规则兜底。", safe_text(error, 500)),
        "summary": summary,
        "ai_actions": actions,
        "kind": kind,
    }


def _roles(project: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "name": role.get("name", ""),
            "username": role.get("username", ""),
            "login_type": role.get("login_type", ""),
        }
        for role in project.get("roles") or []
    ]


def _provider_used(config: dict[str, Any] | None, note: str = "已使用本地规则审查生成基础建议。", error: str = "") -> dict[str, Any]:
    return provider_info(config or {}, not bool(error), note, safe_text(error, 500))


def _ensure_actions(data: dict[str, Any], actions: list[str]) -> dict[str, Any]:
    existing = [item for item in data.get("ai_actions") or [] if item]
    data["ai_actions"] = list(dict.fromkeys([*existing, *actions]))[:10]
    return data


def _merge_analysis(local: dict[str, Any], llm: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
    """以本地规则为底座合并模型增强；保留审计字段并合并列表证据。"""
    merged = dict(local)
    for key, value in llm.items():
        if key in {"provider", "generated_at", "kind"}:
            continue
        if isinstance(value, list):
            merged[key] = [*(merged.get(key) or []), *value]
        elif isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = {**merged[key], **value}
        elif value not in (None, "", [], {}):
            merged[key] = value
    merged["provider"] = provider_info(config or {}, True, "已先做本地规则审查，并合并模型语义分析。")
    merged["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return merged


def _fallback_from_local(local: dict[str, Any], config: dict[str, Any] | None, error: Exception) -> dict[str, Any]:
    """模型超时、鉴权或连接失败时保留本地结论，并返回可操作诊断。"""
    data = dict(local)
    err_text = str(error)
    # 区分超时和其他错误
    if "timeout" in err_text.lower() or "timed out" in err_text.lower():
        note = f"AI 调用超时：{err_text[:120]}，已使用本地规则审查。"
    elif "401" in err_text or "unauthorized" in err_text.lower():
        note = "AI 鉴权失败(401)：请检查 API Key 是否正确。已使用本地规则审查。"
    elif "connection" in err_text.lower() or "connect" in err_text.lower() or "refused" in err_text.lower():
        note = f"AI 连接失败：{err_text[:120]}，请检查 Base URL 和网络。已使用本地规则审查。"
    else:
        note = f"AI 调用失败：{err_text[:200]}，已使用本地规则审查。"
    data["provider"] = provider_info(config or {}, False, note, err_text)
    data["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return data


def _should_wait_for_model(config: dict[str, Any] | None) -> bool:
    return bool((config or {}).get("sync_model"))


def _local_only(local: dict[str, Any], config: dict[str, Any] | None) -> dict[str, Any]:
    data = dict(local)
    data["provider"] = provider_info(config or {}, False, "已完成本地规则 AI 审查；模型语义增强未阻塞当前操作。")
    data["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return data


def _config_rule_analysis(project: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
    roles = _roles(project)
    auth = project.get("auth") or {}
    findings: list[dict[str, Any]] = []
    if len(roles) < 2:
        findings.append({"priority": "P0", "field": "roles", "issue": "角色数量不足，无法形成越权对比", "suggestion": "至少配置两个权限不同的角色，并写清每个角色的业务边界。"})
    if not project.get("base_url"):
        findings.append({"priority": "P0", "field": "base_url", "issue": "缺少被测环境地址", "suggestion": "填写完整 http/https 地址，避免执行阶段请求落空。"})
    if not auth.get("token_url"):
        findings.append({"priority": "P0", "field": "auth.token_url", "issue": "缺少 getToken URL", "suggestion": "补充登录接口；如果是免密 GET，Auth Method 需要选择 GET(免密)。"})
    if not auth.get("token_path"):
        findings.append({"priority": "P1", "field": "auth.token_path", "issue": "Token 提取路径为空", "suggestion": "按真实响应配置，例如 tokenInfo.accessToken、data.access_token 或 access_token。"})
    if not safe_text(project.get("goal"), 1000).strip():
        findings.append({"priority": "P1", "field": "goal", "issue": "测试目标为空", "suggestion": "写清要验证的角色边界、越权类型和预期拦截方式。"})
    boundaries = [
        {
            "role": role.get("name") or "未命名角色",
            "allowed_scope": "根据接口 allowed_roles 与用例描述判定",
            "denied_scope": "不在 allowed_roles 中的接口应被后端拒绝，前端也应隐藏或拦截入口",
            "notes": f"登录类型：{role.get('login_type') or auth.get('login_type') or 'WEB'}；账号：{role.get('username') or '未填'}",
        }
        for role in roles
    ]
    summary = f"已读取 {len(roles)} 个角色、{len(project.get('endpoints') or [])} 个接口；发现 {len(findings)} 个配置风险点。"
    return _ensure_actions({
        "kind": "config",
        "summary": summary,
        "role_boundaries": boundaries,
        "config_findings": findings or [{"priority": "P2", "field": "scope", "issue": "基础字段基本完整", "suggestion": "建议继续补充每个角色的允许/禁止业务范围，提升 AI 和规则审查准确度。"}],
        "provider": _provider_used(config),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }, ["检查角色数量和账号完整性", "检查登录/token 配置", "提取角色边界审查要点"])


def _import_rule_analysis(project: dict[str, Any], endpoints: list[dict[str, Any]], config: dict[str, Any] | None = None) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    suggestions: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    roles = [str(role["name"]) for role in _roles(project) if role.get("name")]

    def import_pool_issue(method: str, path: str) -> str:
        text = f"{method} {path}".lower()
        if re.search(r"/(login|logout|captcha|token|refresh|session)(/|$)", text):
            return "鉴权/会话接口容易污染越权接口池"
        if re.search(r"/(health|ping|heartbeat|metrics|actuator)(/|$)", text):
            return "健康检查/监控接口通常不属于业务越权场景"
        if re.search(r"/(swagger|openapi|api-docs|v3/api-docs)(/|$)", text):
            return "接口文档地址通常应作为来源材料，而不是越权执行接口"
        if re.search(r"/(dict|dictionary|enum|options|config|profile)(/|$)", text) and method == "GET":
            return "公共字典/运行时配置接口需要确认是否真的绑定业务权限"
        return ""

    for ep in endpoints[:200]:
        method = str(ep.get("method") or "GET").upper()
        path = str(ep.get("path") or "")
        key = (method, path)
        if key in seen:
            findings.append({"priority": "P2", "method": method, "path": path, "issue": "重复接口", "suggestion": "合并重复接口，保留 observed_roles/source 作为证据。"})
        seen.add(key)
        noise_issue = import_pool_issue(method, path)
        if noise_issue:
            findings.append({"priority": "P2", "method": method, "path": path, "issue": f"可能污染接口池：{noise_issue}", "suggestion": "默认保留但标记待确认；若它只是登录、心跳、公共配置或文档接口，建议从越权测试计划中排除。"})
        if method in {"POST", "PUT", "PATCH", "DELETE"}:
            findings.append({"priority": "P1", "method": method, "path": path, "issue": "写操作接口需要重点越权验证", "suggestion": "确认 allowed_roles 是否最小化，并增加后端拒绝断言。"})
        if not ep.get("allowed_roles"):
            findings.append({"priority": "P0", "method": method, "path": path, "issue": "未配置有权角色", "suggestion": "根据 HAR 来源角色、PRD 或用例补齐 allowed_roles。"})
        if not (ep.get("observed_roles") or ep.get("discovered_by")):
            findings.append({"priority": "P2", "method": method, "path": path, "issue": "来源角色不明确", "suggestion": "建议按角色重新导入 HAR/浏览器录制，避免把未确认接口直接当成某角色有权接口。"})
        if ep.get("needs_review"):
            suggestions.append({"role": "待确认", "path": path, "suggestion": "该接口来自角色观察证据，建议人工确认 allowed_roles 是否等于 observed_roles。"})
    summary = f"已审查 {len(endpoints)} 个接口，识别 {len(findings)} 个接口配置/风险提示。"
    return _ensure_actions({
        "kind": "import",
        "summary": summary,
        "endpoint_findings": findings[:30] or [{"priority": "P2", "method": "-", "path": "-", "issue": "接口清单暂无明显结构问题", "suggestion": "建议继续补充 sample_response 和 page_url，方便断言与前端拦截检测。"}],
        "role_permission_suggestions": suggestions[:20] or [{"role": "全部角色", "path": "接口清单", "suggestion": f"当前角色集合：{'、'.join(roles) or '未配置'}；建议按业务中心分组核对权限边界。"}],
        "provider": _provider_used(config),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }, ["检查写操作接口", "检查 allowed_roles 缺口", "识别重复接口和待确认来源"])


def _plan_rule_analysis(project: dict[str, Any], plan: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
    cases = plan.get("cases") or []
    items: list[dict[str, Any]] = []
    for case in cases[:160]:
        category = case.get("category")
        method = str(case.get("method") or "")
        if category in {"role_isolation", "anonymous"} or method in {"POST", "PUT", "PATCH", "DELETE"}:
            items.append({
                "priority": "P1" if method not in {"DELETE"} else "P0",
                "method": method,
                "path": case.get("path"),
                "actor": case.get("actor"),
                "reason": case.get("type") or category,
                "suggestion": "优先执行并核对后端是否拒绝返回业务数据；写操作需确认没有产生副作用。",
            })
    for missing in plan.get("missing") or []:
        items.insert(0, {"priority": "P0", "method": "-", "path": "-", "actor": "-", "reason": missing, "suggestion": "先补齐该配置，否则执行结果可能为 0 或不可判定。"})
    summary = f"计划包含 {len(cases)} 条候选项；优先审查项 {len(items)} 条。"
    return _ensure_actions({
        "kind": "plan",
        "summary": summary,
        "plan_review_items": items[:40] or [{"priority": "P2", "method": "-", "path": "-", "actor": "-", "reason": "计划覆盖基本完整", "suggestion": "建议抽查每个角色至少 1 条自身权限、1 条越权、1 条未登录访问。"}],
        "suggested_scenarios": [{"priority": "P1", "name": "登录后跨角色访问核心资源", "steps": ["使用低权限角色登录", "访问高权限接口", "确认后端拒绝且前端无入口"]}],
        "provider": _provider_used(config),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }, ["检查计划覆盖范围", "标记越权/未登录/写操作优先项", "生成建议业务场景"])


def _row_path(row: dict[str, Any]) -> str:
    replay = row.get("replay") or {}
    return str(row.get("path") or row.get("url") or row.get("endpoint") or replay.get("path") or replay.get("url") or "")


def _row_failures(row: dict[str, Any]) -> list[str]:
    raw = row.get("failures") or row.get("failure_reasons") or []
    if isinstance(raw, str):
        failures = [raw]
    else:
        failures = [str(item) for item in raw if item]
    for key in ("failure_reason", "error"):
        value = safe_text(row.get(key), 500).strip()
        if value and value not in failures:
            failures.append(value)
    risky_words = ("失败", "异常", "越权", "未授权", "无权限", "应阻断", "拒绝", "错误", "FAIL", "ERROR")
    status_text = str(row.get("status") or "").upper()
    for key in ("assertion_conclusion", "verdict"):
        value = safe_text(row.get(key), 500).strip()
        if value and (status_text != "PASS" or any(word in value for word in risky_words)) and value not in failures:
            failures.append(value)
    return failures


def _row_evidence(row: dict[str, Any]) -> str:
    for key in ("body_preview", "response_body", "response_preview", "actual_response", "evidence"):
        value = safe_text(row.get(key), 500).strip()
        if value:
            return value
    return safe_text(row.get("assertion_conclusion") or row.get("failure_reason") or row.get("verdict"), 500)


def _run_rule_analysis(summary: dict[str, Any], assertion_profile: dict[str, Any] | None = None, config: dict[str, Any] | None = None) -> dict[str, Any]:
    rows = summary.get("results") or []
    findings: list[dict[str, Any]] = []
    retests: list[dict[str, Any]] = []
    for row in rows:
        status = row.get("status")
        section = str(row.get("section") or "")
        path = _row_path(row)
        failures = _row_failures(row)
        confidence = str(row.get("confidence") or "")
        status_text = str(status or "").upper()
        risk_text = " ".join([section, path, "；".join(failures), safe_text(row.get("expected"), 300)])
        is_idor_related = any(word in risk_text for word in ("越权", "未授权", "权限", "IDOR", "idor", "forbidden", "unauthorized"))
        if status_text != "PASS" or failures or confidence in {"低", "low", "medium"} or is_idor_related:
            priority = "P0" if status_text == "FAIL" and is_idor_related else "P1" if status_text != "PASS" else "P2"
            findings.append({
                "priority": priority,
                "role": row.get("role"),
                "method": row.get("method"),
                "path": path,
                "status": status,
                "reason": "；".join(failures) or row.get("verdict") or "越权相关样本需要复核",
                "evidence": _row_evidence(row),
            })
            retests.append({
                "priority": priority,
                "role": row.get("role"),
                "method": row.get("method"),
                "path": path,
                "url": ((row.get("replay") or {}).get("url") or row.get("url") or path),
                "reason": "失败/越权/低置信度样本优先人工复核",
                "manual_retest": "用同一 token 重放请求，并对比有权角色与越权角色的响应字段、状态码和业务数据。"
            })
    p0 = sum(1 for item in findings if item["priority"] == "P0")
    p1 = sum(1 for item in findings if item["priority"] == "P1")
    p2 = sum(1 for item in findings if item["priority"] == "P2")
    if not findings and rows:
        retests.append({"priority": "P2", "role": "抽样", "method": "-", "path": "全部通过样本", "url": "", "reason": "本次全通过不等于无风险", "manual_retest": "抽查每个角色的核心接口，确认响应体没有越权业务数据。"})
    risk_text = f"实际执行 {summary.get('total', len(rows))} 条，失败 {summary.get('failed', 0)} 条；P0={p0}，P1={p1}，P2={p2}。"
    return _ensure_actions({
        "kind": "run",
        "summary": {"risk_text": risk_text, "p0": p0, "p1": p1, "p2": p2},
        "risk_findings": findings[:40] or [{"priority": "P2", "role": "全部", "method": "-", "path": "-", "status": "PASS", "reason": "未发现失败项", "evidence": "建议仍保留抽样复核，尤其是核心数据读取接口。"}],
        "manual_retest_priorities": retests[:40],
        "assertion_tuning": {
            "preferred_success_field": (assertion_profile or {}).get("success_field"),
            "preferred_code_field": (assertion_profile or {}).get("code_field"),
            "preferred_message_field": (assertion_profile or {}).get("message_field"),
            "preferred_data_field": (assertion_profile or {}).get("data_field"),
            "fields_to_avoid": ["traceId", "timestamp", "requestId"],
            "notes": "优先使用稳定业务字段判断成功/拒绝，避免只看 HTTP 200。",
        },
        "report_brief": risk_text,
        "provider": _provider_used(config),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }, ["筛选失败/越权/低置信度样本", "生成手工复测优先级", "给出断言策略调优建议"])


def analyze_config(project: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
    """审查角色、鉴权、业务目标和场景配置；不运行接口。"""
    local = _config_rule_analysis(project, config)
    if not _should_wait_for_model(config):
        return _local_only(local, config)
    payload = {
        "project_name": project.get("project_name"),
        "model_type": project.get("model_type"),
        "goal": safe_text(project.get("goal"), 1500),
        "prd_text": safe_text(project.get("prd_text"), 2500),
        "testcases": project.get("testcases") or {},
        "roles": _roles(project),
        "scenarios": project.get("scenarios") or [],
    }
    try:
        generated = ask_json(
            "理解基础配置、测试目标、PRD/测试用例备注，抽取角色边界和权限预期；指出配置缺口，但不要执行测试。",
            payload,
            "summary:string, role_boundaries:[{role,allowed_scope,denied_scope,notes}], config_findings:[{priority,field,issue,suggestion}], ai_actions:[string]",
            config,
            50000,
        )
        return _merge_analysis(local, generated, config)
    except Exception as exc:
        return _fallback_from_local(local, config, exc)


# ── 越权深度分析：处理 HTTP 200 + 正常业务数据但 should_block=True 的模糊 case ──

def analyze_idor_suspect(
    row: dict[str, Any],
    allowed_response: dict[str, Any] | None,
    project: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """对单条疑似越权结果进行 AI 深度分析。

    适用场景：should_block=True 的角色访问了某个接口，返回 HTTP 200 + 正常业务数据，
    本地断言无法区分"数据真的越权了"还是"返回了无差别的公共数据"。

    入参：
    - row: execution.py 生成的单行结果（含 role、method、path、response_body、status_code 等）
    - allowed_response: 同一接口由有权角色访问的响应（用于对比）
    - project: 项目配置
    """
    cfg = ai_config(config)
    if not _should_wait_for_model(config):
        return {
            "passed": row.get("status") == "FAIL",
            "verdict": "AI深度复核未同步等待模型，保留本地断言结果",
            "confidence": row.get("assertion", {}).get("confidence", "中"),
            "evidence": row.get("assertion", {}).get("evidence", []),
            "signal": "local_only",
            "ai_analyzed": False,
        }
    if not cfg["base_url"] or not cfg["model"]:
        return {
            "passed": row.get("status") == "FAIL",
            "verdict": "AI不可用（未配置模型），保留本地断言结果",
            "confidence": row.get("assertion", {}).get("confidence", "低"),
            "evidence": row.get("assertion", {}).get("evidence", []),
            "signal": "no_ai",
        }

    blocked_body = safe_text(str(row.get("response_body") or row.get("body_preview")), 3000)
    blocked_headers = row.get("response_headers") or {}
    allowed_body = safe_text(str(allowed_response.get("response_body") or allowed_response.get("body")), 3000) if allowed_response else ""
    assertion = row.get("assertion") or {}

    payload = {
        "endpoint": {
            "method": row.get("method", ""),
            "path": row.get("path", ""),
            "role": row.get("role", ""),
            "should_be_blocked": True,
        },
        "blocked_response": {
            "status": row.get("status_code", 0),
            "headers": {k: v for k, v in blocked_headers.items() if k.lower() in ("content-type", "x-powered-by", "server")},
            "body": blocked_body,
        },
        "allowed_response": {
            "status": allowed_response.get("status_code", 0),
            "body": allowed_body,
        } if allowed_response else None,
        "roles": _roles(project),
        "goal": safe_text(project.get("goal"), 1000),
    }

    prompt = (
        "任务：判断一个 `should_block=True`（无权角色）的请求是否真的发生了越权。\n"
        "\n"
        "输入说明：\n"
        "- blocked_response：无权角色对接口发起请求的实际响应\n"
        "- allowed_response：同一接口由有权角色访问的响应（如果提供，用于对比）\n"
        "\n"
        "判断规则：\n"
        "1. 如果 blocked_response 的 body 为空/null/[]/{}/错误信息 → 未越权（PASS）\n"
        "2. 如果 blocked_response 返回了和 allowed_response 在结构和数据量上完全一样的数据 → 越权（IDOR 成功）\n"
        "3. 如果 blocked_response 返回了与当前角色无关的业务数据（如返回了其他用户的列表、其他角色的配置）→ 越权\n"
        "4. 如果返回的是无差异的公共配置/字典数据（所有角色都有权读取）→ 不算越权，标记为\"公共资源\"\n"
        "5. 不能确定时 → 标记 uncertainty\n"
        "\n"
        "输出 JSON 字段：\n"
        "- verdict: \"idor_confirmed\" | \"no_idor\" | \"public_resource\" | \"uncertain\"\n"
        "- confidence: \"高\" | \"中\" | \"低\"\n"
        "- reasoning: 一句话解释判断依据\n"
        "- data_owner: 如果越权，数据本应属于哪个角色（如不确定填\"未知\"）\n"
        "- data_sensitivity: \"高\" | \"中\" | \"低\"（数据敏感度）\n"
        "- recommendation: 给用户的操作建议"
    )

    try:
        messages: list[dict[str, str]] = [
            {"role": "system", "content": "你是 IDOR 越权测试专家。只输出 JSON，不输出 Markdown。"},
            {"role": "user", "content": f"{prompt}\n\n数据：\n{json.dumps(payload, ensure_ascii=False)}"},
        ]
        raw = call_ai(messages, config=config, max_tokens=50000)
        result = parse_json(_extract_content(raw))
        result["signal"] = "ai"
        # 映射回 passed 字段
        result["passed"] = result.get("verdict", "") in ("no_idor", "public_resource")
        result["ai_analyzed"] = True
        return result
    except Exception:
        return {
            "passed": row.get("status") == "FAIL",
            "verdict": "AI分析失败，保留本地断言",
            "confidence": "中",
            "evidence": assertion.get("evidence", []),
            "signal": "ai_failed",
            "ai_analyzed": False,
        }


def analyze_import(project: dict[str, Any], endpoints: list[dict[str, Any]], config: dict[str, Any] | None = None) -> dict[str, Any]:
    """审查导入接口的来源角色、权限边界和噪声污染，并给出人工确认建议。"""
    local = _import_rule_analysis(project, endpoints, config)
    if not _should_wait_for_model(config):
        return _local_only(local, config)
    if not endpoints:
        return local
    compact_endpoints = [
        {
            "method": ep.get("method"),
            "path": ep.get("path"),
            "name": ep.get("name"),
            "module": ep.get("module"),
            "allowed_roles": ep.get("allowed_roles") or [],
            "observed_roles": ep.get("observed_roles") or ep.get("discovered_by") or [],
            "operation": ep.get("operation"),
            "risk": ep.get("risk"),
            "needs_review": ep.get("needs_review"),
        }
        for ep in endpoints[:160]
    ]
    payload = {
        "goal": safe_text(project.get("goal"), 1200),
        "roles": _roles(project),
        "prd_text": safe_text(project.get("prd_text"), 1800),
        "testcases": project.get("testcases") or {},
        "endpoints": compact_endpoints,
    }
    try:
        generated = ask_json(
            "审查刚导入的接口列表，判断来源角色、allowed_roles、接口风险、缺失参数是否和业务目标一致。重点识别接口池污染：登录/刷新 token、心跳监控、接口文档、公共字典、运行时配置、轮询初始化接口不要轻易进入越权测试计划，除非它们本身涉及敏感数据或权限边界。",
            payload,
            "summary:string, endpoint_findings:[{priority,method,path,issue,suggestion}], role_permission_suggestions:[{role,path,suggestion}], ai_actions:[string]",
            config,
            50000,
        )
        return _merge_analysis(local, generated, config)

    except Exception as exc:
        return _fallback_from_local(local, config, exc)


def _compact_endpoints(endpoints: list[dict[str, Any]], max_groups: int = 80) -> list[dict[str, Any]]:
    """同类接口归并，每组提取 1-2 条代表，防止 token 溢出和重复审查。

    归并规则：
    - 同一 module + method 为一组
    - CRUD 同类合并（/page, /list, /tree 归为查询；/add, /save 归为写入）
    - 每组取前 2 条作为代表
    """
    import re
    if not endpoints or len(endpoints) <= 40:
        return endpoints

    # 1. 归一化 path：把 ID / UUID / 数字参数替换成 {id}
    def _norm_path(path: str) -> str:
        p = str(path)
        p = re.sub(r'/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', '/{uuid}', p, flags=re.I)
        p = re.sub(r'/\d{4,}', '/{id}', p)
        p = re.sub(r'/\d+(?=/|$)', '/{id}', p)
        return p

    # 2. 操作归并
    OP_MAP = {
        "list": "query", "page": "query", "tree": "query", "find": "query", "get": "query", "query": "query",
        "add": "write", "save": "write", "create": "write", "insert": "write",
        "update": "write", "edit": "write", "modify": "write",
        "delete": "write", "remove": "write", "del": "write",
        "export": "export", "download": "download", "import": "import", "upload": "upload",
        "login": "auth", "logout": "auth", "reset": "auth",
    }

    # 3. 分组
    groups: dict[str, list[dict[str, Any]]] = {}
    for ep in endpoints:
        module = str(ep.get("module", "") or "")
        method = str(ep.get("method", "GET") or "GET").upper()
        path = str(ep.get("path") or "")
        norm = _norm_path(path)
        allowed = set(ep.get("allowed_roles") or [])

        # 提取操作关键词
        path_lower = norm.lower()
        operation = ""
        for kw, cat in OP_MAP.items():
            if kw in path_lower:
                operation = cat
                break

        # 分组 key: module + method + normalized_path_family + operation + allowed_roles_hash
        path_family = re.sub(r'/{id}|/{uuid}|/[^/?]*\{[^}]*\}', '/{id}', norm)
        role_hash = ",".join(sorted(allowed)) if allowed else "_none"
        if operation and method == "GET" and operation == "query":
            key = f"{module}|GET|query|{path_family}|{role_hash}"
        else:
            key = f"{module}|{method}|{operation}|{path_family}|{role_hash}"

        if key not in groups:
            groups[key] = []
        groups[key].append(ep)

    # 4. 每组取代表 + 统计
    compact = []
    for eps in groups.values():
        reps = eps[:2]  # 最多 2 条
        for ep in reps:
            ep_copy = dict(ep)
            if len(eps) > len(reps):
                ep_copy["_group_count"] = len(eps)
                ep_copy["_group_note"] = f"本组共 {len(eps)} 个同类接口"
            compact.append(ep_copy)

    # 如果归并后仍然太多，再截断
    if len(compact) > max_groups:
        # 优先保留有 allowed_roles 的 + POST/DELETE 操作
        prioritized = []
        for ep in compact:
            score = 0
            if ep.get("allowed_roles"):
                score += 2
            if ep.get("method", "GET").upper() in ("POST", "PUT", "DELETE", "PATCH"):
                score += 1
            if ep.get("_group_count", 1) > 1:
                score += 1
            prioritized.append((score, ep))
        prioritized.sort(key=lambda x: -x[0])
        compact = [ep for _, ep in prioritized[:max_groups]]

    return compact


def _compact_coverage(coverage_summary: list[dict[str, Any]], max_entries: int = 100) -> list[dict[str, Any]]:
    """合并覆盖度摘要中的同类路径。"""
    if not coverage_summary or len(coverage_summary) <= 50:
        return coverage_summary
    import re

    def _norm_path(p: str) -> str:
        p = re.sub(r'/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', '/{uuid}', p, flags=re.I)
        p = re.sub(r'/\d{4,}', '/{id}', p)
        p = re.sub(r'/\d+(?=/|$)', '/{id}', p)
        return p

    # 按归一化路径合并
    merged: dict[str, dict[str, Any]] = {}
    for entry in coverage_summary:
        norm = _norm_path(entry.get("path", ""))
        if norm not in merged:
            merged[norm] = dict(entry)
            merged[norm]["_similar_count"] = 1
            merged[norm]["_similar_paths"] = [entry.get("path", "")]
        else:
            merged[norm]["_similar_count"] += 1
            merged[norm]["_similar_paths"].append(entry.get("path", ""))

    result = list(merged.values())[:max_entries]
    return result


def _compact_ai_endpoints(endpoints: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把 endpoint 列表压缩成 AI 可读的格式。"""
    compact = _compact_endpoints(endpoints)
    return [
        {
            "method": ep.get("method", ""),
            "path": ep.get("path", ""),
            "allowed_roles": ep.get("allowed_roles", []),
            "module": ep.get("module", ""),
            **({"group_note": ep.get("_group_note", "")} if ep.get("_group_note") else {}),
        }
        for ep in compact
    ]


def analyze_plan(project: dict[str, Any], plan: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Agent 模式执行计划审查。

    1. 先用规则生成基线计划审查
    2. 将完整计划 + 角色边界 + 测试目标发给 AI
    3. AI 返回具体的计划修改建议（增/删/改具体 case）
    4. 返回结构化修改建议；真正应用仍由 API 层 ``_apply_plan_modifications`` 校验
    """
    local = _plan_rule_analysis(project, plan, config)
    if not _should_wait_for_model(config):
        return _local_only(local, config)

    cases = plan.get("cases") or []
    # 构建接口覆盖度摘要：每个接口有哪些角色在测试（而非发送全部 case 列表）
    coverage_map: dict[str, set] = {}  # "method|path" -> {actor, ...}
    for c in cases:
        key = f"{c.get('method', '')}|{c.get('path', '')}"
        if key not in coverage_map:
            coverage_map[key] = set()
        coverage_map[key].add(c.get("actor", ""))
    coverage_summary = [
        {"key": k, "path": k.split("|", 1)[1], "method": k.split("|", 1)[0], "tested_by": sorted(v)}
        for k, v in coverage_map.items()
    ]

    payload = {
        "goal": safe_text(project.get("goal"), 1200),
        "roles": _roles(project),
        "role_boundaries": local.get("role_boundaries", []),
        "plan_summary": plan.get("summary") or {},
        "coverage": _compact_coverage(coverage_summary),
        "endpoints": _compact_ai_endpoints(project.get("endpoints") or []),
        "total_endpoints": len(project.get("endpoints") or []),
    }

    prompt = (
        "你是 IDOR 越权测试计划审查 Agent。你的任务是审阅当前测试计划，找出遗漏、错误或可优化的 case，直接返回修改建议。\n"
        "\n"
        "数据说明：\n"
        "- total_endpoints 是接口总数，endpoints 是去重归并后的代表接口（group_note 标注了同组接口数量）\n"
        "- coverage 是接口覆盖度摘要，tested_by 列出当前计划中哪些角色已测试该接口\n"
        "- endpoints 中每个接口的 allowed_roles 是该接口的有权角色\n"
        "- roles 是所有角色列表\n"
        "- 如果某接口的 tested_by 角色集合 < allowed_roles + 所有角色，说明有角色漏测 → 建议 add\n"
        "\n"
        "你的判断标准：\n"
        "1. 对照 coverage 和 endpoints，找出 tested_by 中缺少的角色（该角色也应测试此接口），建议 add\n"
        "2. 如果某接口不在 coverage 中（整个接口都没测），需要为所有角色 add 测试 case\n"
        "3. 如果某个接口的 allowed_roles 为空且 tested_by 也只有匿名/未登录 → 已足够，不需要改\n"
        "4. 关键写操作/P0 接口应该有\"未登录用户\" case，缺失则建议 add\n"
        "5. 如果发现角色归属明显错误（如把无权角色标记为自身权限），建议 modify\n"
        "6. 不要建议重复添加（coverage 里 tested_by 已包含的角色不需要 add）\n"
        "\n"
        "返回 JSON 字段：\n"
        "- summary: 一句话总结审查结论\n"
        "- plan_modifications: 具体的修改建议列表，每个元素包含：\n"
        "    action: \"add\" | \"remove\" | \"modify\"\n"
        "    reason: 修改理由（一句话）\n"
        "    priority: \"P0\" | \"P1\" | \"P2\"\n"
        "    如果是 add: method, path, actor（角色名）, type, expected, category\n"
        "    如果是 remove: actor, method, path（用于匹配要删除的 case）\n"
        "    如果是 modify: actor, method, path（匹配原 case）, new_type, new_expected（修改后的值）\n"
        "- plan_review_items: 不需要修改但值得关注的审查发现\n"
        "- ai_actions: 你建议用户接下来做的操作步骤"
    )

    try:
        generated = ask_json(prompt, payload, "summary:string, plan_modifications:[{action,reason,priority,method,path,actor,type,expected,category,new_type,new_expected}], plan_review_items:[{priority,method,path,actor,reason,suggestion}], ai_actions:[string]", config, 50000)
        # 把 AI 的修改建议合并到审查结果中
        merged = _merge_analysis(local, generated, config)
        merged["plan_modifications"] = generated.get("plan_modifications") or []
        return merged
    except Exception as exc:
        return _fallback_from_local(local, config, exc)

# 结果流量漏斗：只把失败、低置信和越权相关项送给模型，控制上下文和成本。
def _interesting_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    """从全量结果中选择最需要语义复核的最多 80 条证据。"""
    rows = summary.get("results") or []
    selected = []
    for index, row in enumerate(rows):
        confidence = str(row.get("confidence") or "").strip()
        status = str(row.get("status") or "")
        section = str(row.get("section") or "")
        if status != "PASS" or confidence in {"低", "中", "low", "medium"} or "越权" in section:
            selected.append({
                "index": index,
                "role": row.get("role"),
                "section": section,
                "status": status,
                "method": row.get("method"),
                "path": row.get("path"),
                "http": row.get("http"),
                "expected": safe_text(row.get("expected"), 500),
                "verdict": row.get("verdict"),
                "confidence": confidence,
                "failures": row.get("failures") or [],
                "request": row.get("request") or {},
                "response_preview": safe_text(row.get("response_body") or row.get("body_preview"), 1200),
            })
    return selected[:80]  # 设置上限，避免大项目把模型上下文挤爆。


def analyze_run(summary: dict[str, Any], assertion_profile: dict[str, Any] | None = None, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """复核执行风险并提出断言调优；规则结果始终存在，模型只增强疑难项。"""
    local = _run_rule_analysis(summary, assertion_profile, config)
    if not _should_wait_for_model(config):
        return _local_only(local, config)
    payload = {
        "summary": {
            "env": summary.get("env"),
            "time": summary.get("time"),
            "total": summary.get("total"),
            "passed": summary.get("passed"),
            "failed": summary.get("failed"),
            "skipped": summary.get("skipped"),
            "role_stats": summary.get("role_stats") or {},
        },
        "interesting_results": _interesting_rows(summary),
        "assertion_profile": assertion_profile or summary.get("assertion_profile") or {},
    }
    try:
        generated = ask_json(
            "分析执行结果。重点复核低置信度、FAIL、ERROR、越权相关接口；结合请求体、响应体、断言档案判断是否真实越权或断言规则误判，并给出断言档案优化建议。",
            payload,
            "summary:{risk_text,p0,p1,p2}, risk_findings:[{priority,role,method,path,status,reason,evidence}], manual_retest_priorities:[{priority,role,method,path,url,reason,manual_retest}], assertion_tuning:{preferred_success_field,preferred_code_field,preferred_message_field,preferred_data_field,fields_to_avoid,blocked_keywords,success_values,blocked_values,success_codes,blocked_codes,notes}, report_brief:string, ai_actions:[string]",
            config,
            50000,
        )
        return _merge_analysis(local, generated, config)

    except Exception as exc:
        return _fallback_from_local(local, config, exc)
