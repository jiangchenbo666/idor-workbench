"""越权测试用的自学习断言引擎。

接口返回不一定总是标准 401/403，有些系统会返回 200 + 业务 code/message。
本模块会观察“有权访问”和“应被拦截”的响应差异，学习哪些字段代表成功、哪些字段代表拒绝，
再把原始响应判定为 PASS/FAIL。
"""
from __future__ import annotations

import ast
import json  # L2 IDOR assertion rules.
import re
from copy import deepcopy
from typing import Any

SUCCESS_FIELD_CANDIDATES = ("success", "succeed", "ok", "isSuccess")
CODE_FIELD_CANDIDATES = ("code", "status", "statusCode", "errCode", "errorCode")
MESSAGE_FIELD_CANDIDATES = ("message", "msg", "error", "errorMessage", "detail")
DATA_FIELD_CANDIDATES = ("data", "result", "payload", "body")
LIST_FIELD_CANDIDATES = ("records", "list", "items", "rows", "data")


def new_profile() -> dict[str, Any]:
    """创建一份新的断言学习档案，每次测试运行都会从这里开始。"""
    blocked_keywords = [
        "无权限", "未授权", "禁止访问", "拒绝访问", "未登录", "登录失效",
        "token", "Token", "Unauthorized", "Forbidden", "forbidden", "denied",
    ]
    return {
        "version": 1,
        "mode": "auto",
        "learned_at": "",
        "success_field": None,
        "code_field": None,
        "message_field": None,
        "data_field": None,
        "list_fields": [],
        "success_values": [],
        "blocked_values": [],
        "success_codes": [],
        "blocked_codes": [],
        "blocked_keywords": blocked_keywords,
        "observations": {
            "allowed": {"count": 0, "statuses": {}, "fields": {}},
            "blocked": {"count": 0, "statuses": {}, "fields": {}},
        },
    }


def parse_json_body(body: str | None) -> Any:
    """尝试把响应体解析成 JSON；不是 JSON 时返回 None。"""
    if not body:
        return None
    try:
        return json.loads(body)
    except (TypeError, ValueError):
        return None


def get_path_value(data: Any, path: str | None) -> Any:
    """按 a.b.c 这种简单路径从 JSON 字典中取值。"""
    if not path:
        return None
    cur = data
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _observe_field(bucket: dict[str, Any], field: str, value: Any) -> None:
    """累计某类样本中字段值的出现次数，为后续提取主流成功/拒绝值。"""
    fields = bucket.setdefault("fields", {})
    item = fields.setdefault(field, {})
    key = repr(value)
    item[key] = item.get(key, 0) + 1


def _inc(mapping: dict[str, Any], key: Any) -> None:
    text = str(key)
    mapping[text] = mapping.get(text, 0) + 1


def _first_present(data: Any, candidates: tuple[str, ...]) -> str | None:
    if not isinstance(data, dict):
        return None
    for field in candidates:
        if field in data:
            return field
    return None


def _data_nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, list | tuple | set | dict | str):
        return len(value) > 0
    return True


def _records_nonempty(data: Any) -> bool:
    """识别常见 records/list/items/rows 容器中是否真的返回业务记录。"""
    if not isinstance(data, dict):
        return False
    containers = [data]
    for field in DATA_FIELD_CANDIDATES:
        inner = data.get(field)
        if isinstance(inner, dict):
            containers.append(inner)
    for container in containers:
        for field in LIST_FIELD_CANDIDATES:
            value = container.get(field)
            if isinstance(value, list) and value:
                return True
    return False


def _message_text(data: Any, message_field: str | None = None) -> str:
    if not isinstance(data, dict):
        return ""
    fields = [message_field] if message_field else []
    fields += list(MESSAGE_FIELD_CANDIDATES)
    for field in fields:
        if field and field in data and data[field] is not None:
            return str(data[field])
    return ""


def update_profile(profile: dict[str, Any] | None, result: dict[str, Any], should_block: bool) -> dict[str, Any]:
    """根据一条响应更新断言学习档案。

    should_block=True 表示这条请求按计划应该被拒绝；
    should_block=False 表示这条请求按计划应该允许访问。
    两类样本分开累计，后续 classify_response() 才能比较它们的差异。
    """
    profile = deepcopy(profile) if profile else new_profile()
    bucket_name = "blocked" if should_block else "allowed"
    bucket = profile["observations"][bucket_name]
    bucket["count"] += 1
    _inc(bucket["statuses"], result.get("status_code"))

    data = parse_json_body(result.get("body"))
    if isinstance(data, dict):
        for field in SUCCESS_FIELD_CANDIDATES + CODE_FIELD_CANDIDATES + MESSAGE_FIELD_CANDIDATES + DATA_FIELD_CANDIDATES:
            if field in data:
                value = data[field]
                if isinstance(value, dict | list):
                    value = f"<{type(value).__name__}:{len(value)}>"
                _observe_field(bucket, field, value)

        for key, candidates in (
            ("success_field", SUCCESS_FIELD_CANDIDATES),
            ("code_field", CODE_FIELD_CANDIDATES),
            ("message_field", MESSAGE_FIELD_CANDIDATES),
            ("data_field", DATA_FIELD_CANDIDATES),
        ):
            if not profile.get(key):
                found = _first_present(data, candidates)
                if found:
                    profile[key] = found

        for field in LIST_FIELD_CANDIDATES:
            if field in data and field not in profile["list_fields"]:
                profile["list_fields"].append(field)
        inner = get_path_value(data, profile.get("data_field"))
        if isinstance(inner, dict):
            for field in LIST_FIELD_CANDIDATES:
                if field in inner and field not in profile["list_fields"]:
                    profile["list_fields"].append(f"{profile.get('data_field')}.{field}")

    _refresh_profile_values(profile)
    return profile


def _top_values(bucket: dict[str, Any], field: str | None) -> list[Any]:
    if not field:
        return []
    values = bucket.get("fields", {}).get(field, {})
    ordered = sorted(values.items(), key=lambda item: item[1], reverse=True)
    parsed = []
    for raw, _count in ordered[:5]:
        try:
            parsed.append(ast.literal_eval(raw))
        except Exception:
            parsed.append(raw.strip("'\""))
    return parsed


def _refresh_profile_values(profile: dict[str, Any]) -> None:
    """从观察桶刷新成功/拒绝特征，并选择 http-first 或 body-first 模式。"""
    allowed = profile["observations"]["allowed"]
    blocked = profile["observations"]["blocked"]
    success_field = profile.get("success_field")
    code_field = profile.get("code_field")
    if success_field:
        profile["success_values"] = _top_values(allowed, success_field)
        raw_blocked_values = _top_values(blocked, success_field)
        # 排除与成功值重叠的信号，避免污染
        profile["blocked_values"] = [v for v in raw_blocked_values if v not in set(profile["success_values"])]
    if code_field:
        profile["success_codes"] = _top_values(allowed, code_field)
        raw_blocked_codes = _top_values(blocked, code_field)
        profile["blocked_codes"] = [v for v in raw_blocked_codes if v not in set(profile["success_codes"])]

    blocked_statuses = set(blocked.get("statuses", {}))
    if blocked_statuses and blocked_statuses - {"200", "0"}:
        profile["mode"] = "http-first"
    elif profile.get("success_field") or profile.get("code_field"):
        profile["mode"] = "body-first"
    else:
        profile["mode"] = "auto"


def classify_response(result: dict[str, Any], should_block: bool, profile: dict[str, Any] | None) -> dict[str, Any]:
    """用学习档案判断当前响应是否符合权限预期。

    关键修正：当 should_block=True 但响应返回了正常业务数据时，
    不能因为 body_blocked 被污染就误判为 PASS。
    """
    profile = profile or new_profile()
    status = result.get("status_code")
    data = parse_json_body(result.get("body"))
    message = _message_text(data, profile.get("message_field"))

    status_blocked = status in (401, 403) or (300 <= int(status or 0) < 400)
    success_value = get_path_value(data, profile.get("success_field")) if isinstance(data, dict) else None
    code_value = get_path_value(data, profile.get("code_field")) if isinstance(data, dict) else None
    data_value = get_path_value(data, profile.get("data_field")) if isinstance(data, dict) else None
    body_success = success_value in profile.get("success_values", []) or code_value in profile.get("success_codes", [])
    body_blocked = success_value in profile.get("blocked_values", []) or code_value in profile.get("blocked_codes", [])
    keyword_blocked = any(keyword in message for keyword in profile.get("blocked_keywords", []))
    business_data = _data_nonempty(data_value) or _records_nonempty(data)

    evidence = []
    if status is not None:
        evidence.append(f"HTTP={status}")
    if profile.get("success_field"):
        evidence.append(f"{profile['success_field']}={success_value!r}")
    if profile.get("code_field"):
        evidence.append(f"{profile['code_field']}={code_value!r}")
    if message:
        evidence.append(f"message={message[:80]}")
    evidence.append(f"business_data={'yes' if business_data else 'no'}")

    if should_block:
        # ── 第一步：明确拦截信号（优先判定 PASS）──
        explicit_blocked = status_blocked or keyword_blocked
        if explicit_blocked:
            return {"passed": True, "verdict": "拦截成功", "confidence": "高", "evidence": evidence}

        # ── 第二步：返回了成功信号且有业务数据 → 越权（FAIL）──
        # body_blocked 可能被污染（blocked 响应也返回了 200），
        # 此时 body_success=True+business_data 才是决定性信号。
        if status == 200 and body_success and business_data:
            return {"passed": False, "verdict": "越权疑似成功（返回了正常业务数据）", "confidence": "高", "evidence": evidence}

        # ── 第三步：仅有 body_blocked 且无成功信号 → 判定为拦截 ──
        if status == 200 and body_blocked and not body_success:
            return {"passed": True, "verdict": "拦截成功（code字段匹配拦截特征）", "confidence": "中", "evidence": evidence}

        if status == 200 and body_success and not business_data:
            return {"passed": False, "verdict": "疑似越权或空数据", "confidence": "中", "evidence": evidence}

        return {"passed": False, "verdict": "无法确认是否拦截", "confidence": "低", "evidence": evidence}

    if status == 200 and (body_success or business_data or not isinstance(data, dict)):
        return {"passed": True, "verdict": "有权访问成功", "confidence": "高" if body_success or business_data else "中", "evidence": evidence}
    if status_blocked or body_blocked or keyword_blocked:
        return {"passed": False, "verdict": "有权访问被拦截", "confidence": "高", "evidence": evidence}
    return {"passed": status == 200, "verdict": "有权访问结果不明确", "confidence": "低", "evidence": evidence}


def expected_text(should_block: bool, profile: dict[str, Any] | None) -> str:
    """把当前断言档案转成报告里可读的“期望结果”文字。"""
    profile = profile or new_profile()
    if should_block:
        parts = ["期望被拦截"]
        if profile.get("mode") == "http-first":
            parts.append("HTTP 401/403/302")
        if profile.get("success_field") and profile.get("blocked_values"):
            parts.append(f"{profile['success_field']} in {profile['blocked_values']}")
        if profile.get("code_field") and profile.get("blocked_codes"):
            parts.append(f"{profile['code_field']} in {profile['blocked_codes']}")
        parts.append("不能返回业务数据")
        return "；".join(parts)
    parts = ["期望允许访问", "HTTP 200"]
    if profile.get("success_field") and profile.get("success_values"):
        parts.append(f"{profile['success_field']} in {profile['success_values']}")
    if profile.get("code_field") and profile.get("success_codes"):
        parts.append(f"{profile['code_field']} in {profile['success_codes']}")
    return "；".join(parts)



def apply_ai_tuning(profile: dict[str, Any] | None, tuning: dict[str, Any] | None) -> dict[str, Any]:
    """把 AI 给出的断言调优建议合并进断言档案。

    安全策略：只接受明确字段名和值列表；字段名必须是简单 JSON 字段路径；原始建议
    保存在 ai_tuning 供审计。AI 不能提交表达式、代码或任意对象覆盖断言档案。
    """
    profile = deepcopy(profile) if profile else new_profile()
    tuning = tuning or {}
    field_map = {
        "preferred_success_field": "success_field",
        "preferred_code_field": "code_field",
        "preferred_message_field": "message_field",
        "preferred_data_field": "data_field",
    }
    for source_key, target_key in field_map.items():
        value = tuning.get(source_key)
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", value):
            profile[target_key] = value

    for key in ("blocked_keywords", "success_values", "blocked_values", "success_codes", "blocked_codes"):
        values = tuning.get(key)
        if isinstance(values, list):
            existing = profile.get(key) or []
            merged = []
            for item in [*existing, *values]:
                if item not in merged and len(str(item)) <= 120:
                    merged.append(item)
            profile[key] = merged[:50]

    from datetime import datetime
    profile["ai_tuning"] = {
        "applied_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "raw": tuning,
    }
    return profile
