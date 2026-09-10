"""把自然语言测试目标转换成可解释的角色/接口范围。

只有目标明确出现“仅/只/排除/交叉”等限制信号时才收窄范围；模糊目标保持全量，
避免 NLP 猜测静默漏测。计划、API 执行和前端探针都调用同一入口保证范围一致。
"""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

LIMIT_KEYWORDS = ("只", "仅", "限定", "限于", "只进行", "只测试", "只覆盖", "只生成")
CROSS_KEYWORDS = ("交叉", "相互", "互相", "之间")
EXCLUDE_KEYWORDS = ("不测", "不测试", "不进行", "不要", "无需", "排除", "除外", "剔除", "不覆盖", "不生成")


def _safe_text(value: Any, limit: int = 4000) -> str:
    text = "" if value is None else str(value)
    return "".join(ch if ch in "\n\r\t" or ord(ch) >= 32 else " " for ch in text)[:limit]


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", _safe_text(value)).lower()


def role_names_from_project(project: dict[str, Any]) -> list[str]:
    """按项目配置顺序提取非空角色名。"""
    return [str(role.get("name")) for role in project.get("roles") or [] if role.get("name")]


def _role_is_excluded(goal_text: str, role_name: str) -> bool:
    role = _compact(role_name)
    if not role:
        return False
    for match in re.finditer(re.escape(role), goal_text):
        window = goal_text[max(0, match.start() - 8): min(len(goal_text), match.end() + 6)]
        if any(keyword in window for keyword in EXCLUDE_KEYWORDS):
            return True
    return False


def role_scope_from_goal(project: dict[str, Any]) -> dict[str, Any]:
    """提取测试目标明确要求的角色范围。

    项目可以保留全部角色；只有目标明确要求子集时才收窄，例如“只进行系统管理员
    和安全管理员的交叉越权测试”。返回 reason 供 UI 展示，不做不可见过滤。
    """
    roles = role_names_from_project(project)
    goal = _compact(project.get("goal"))
    if not roles or not goal:
        return {"scoped": False, "roles": roles, "excluded_roles": [], "reason": ""}

    mentioned = [role for role in roles if _compact(role) and _compact(role) in goal]
    excluded = [role for role in mentioned if _role_is_excluded(goal, role)]
    included = [role for role in mentioned if role not in excluded]

    if excluded:
        active = [role for role in roles if role not in excluded]
        return {
            "scoped": len(active) != len(roles),
            "roles": active,
            "excluded_roles": excluded,
            "reason": f"测试目标排除了：{'、'.join(excluded)}",
        }

    has_limit = any(keyword in goal for keyword in LIMIT_KEYWORDS)
    has_cross_subset = any(keyword in goal for keyword in CROSS_KEYWORDS) and 1 < len(included) < len(roles)
    if included and (has_limit or has_cross_subset):
        return {
            "scoped": len(included) != len(roles),
            "roles": included,
            "excluded_roles": [role for role in roles if role not in included],
            "reason": f"测试目标限定角色：{'、'.join(included)}",
        }

    return {"scoped": False, "roles": roles, "excluded_roles": [], "reason": ""}


def endpoint_in_scope(endpoint: dict[str, Any], scoped_roles: list[str]) -> bool:
    """接口与任一范围角色存在允许/观察关系即保留；无角色证据时保守保留。"""
    if not scoped_roles:
        return True
    scoped = set(scoped_roles)
    allowed = set(endpoint.get("allowed_roles") or [])
    if allowed:
        return bool(allowed & scoped)
    observed = set(endpoint.get("observed_roles") or endpoint.get("discovered_by") or [])
    if observed:
        return bool(observed & scoped)
    return True


def apply_goal_scope_to_project(project: dict[str, Any]) -> dict[str, Any]:
    """深拷贝项目并应用角色/接口范围，确保不会修改用户保存的原始配置。"""
    scoped_project = deepcopy(project)
    scope = role_scope_from_goal(scoped_project)
    scoped_project["plan_scope"] = scope
    if not scope.get("scoped"):
        return scoped_project

    scoped_roles = set(scope.get("roles") or [])
    scoped_project["roles"] = [role for role in scoped_project.get("roles") or [] if role.get("name") in scoped_roles]
    scoped_project["endpoints"] = [
        endpoint
        for endpoint in scoped_project.get("endpoints") or []
        if endpoint_in_scope(endpoint, list(scoped_roles))
    ]
    return scoped_project
