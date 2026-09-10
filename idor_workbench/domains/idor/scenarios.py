"""本地 IDOR Workbench 的业务场景准备引擎。

场景用于在正式跨角色接口扫描前准备隔离测试数据，例如创建资源、查询资源 ID、清理数据。
写操作默认受保护：每个场景必须显式允许写操作，DELETE 还需要每个步骤单独确认。
"""
from __future__ import annotations

import copy
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}  # L2 IDOR scenario policy.


def _get_json_path(payload: Any, path: str) -> Any:
    """支持一个很小的 JSONPath 子集，例如 $.data.id、$.data.records[0].id。"""
    if not path:
        return None
    text = path.strip()
    if text.startswith("$."):
        text = text[2:]
    elif text == "$":
        return payload
    current = payload
    for field, index in re.findall(r"([^.\[\]]+)|\[(\d+)\]", text):
        token = index or field
        if index:
            if not isinstance(current, list) or int(token) >= len(current):
                return None
            current = current[int(token)]
        elif isinstance(current, dict):
            current = current.get(token)
        else:
            return None
    return current


def _resolve(value: Any, variables: dict[str, Any]) -> Any:
    """把场景步骤里的 {{变量名}} 替换成前面步骤提取到的真实值。"""
    if isinstance(value, str):
        return re.sub(
            r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}",
            lambda match: str(variables.get(match.group(1), match.group(0))),
            value,
        )
    if isinstance(value, list):
        return [_resolve(item, variables) for item in value]
    if isinstance(value, dict):
        return {key: _resolve(item, variables) for key, item in value.items()}
    return value


def _role_tokens(project: dict[str, Any], engine: Any) -> tuple[dict[str, str], list[str]]:
    """根据项目角色配置登录，返回可用 token 和登录失败原因。"""
    tokens: dict[str, str] = {}
    errors: list[str] = []
    for role in project.get("roles") or []:
        name = role.get("name", "")
        if not name:
            continue
        username = role.get("username", "")
        password = role.get("password", "")
        if not username or not password:
            errors.append(f"{name}: missing account")
            continue
        token = engine.get_token(username, password, role.get("login_type") or "WEB")
        if token:
            tokens[name] = token
        else:
            errors.append(f"{name}: login failed")
    return tokens, errors


def execute_scenarios(project: dict[str, Any], project_dir: Path, engine: Any) -> dict[str, Any]:
    """执行已启用的场景步骤，并落盘变量和审计结果。

    返回的 variables 会由调用方应用到普通接口测试中。
    场景结果故意和权限断言结果分开保存，避免“造数据失败”和“越权失败”混在一起。
    """
    # 阶段 1：筛选启用场景并先创建稳定的空报告；即使没有场景也会落盘，方便下载和诊断。
    scenarios = [item for item in (project.get("scenarios") or []) if item.get("enabled", True)]
    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    report: dict[str, Any] = {
        "started_at": started_at,
        "finished_at": "",
        "variables": {},
        "login_errors": [],
        "scenarios": [],
        "summary": {"total": 0, "passed": 0, "skipped": 0, "failed": 0},
    }
    if not scenarios:
        report["finished_at"] = started_at
        (project_dir / "scenario_results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report

    # 阶段 2：场景步骤按角色执行，提前登录可让缺失账号统一表现为 SKIPPED 而不是随机异常。
    tokens, login_errors = _role_tokens(project, engine)
    report["login_errors"] = login_errors
    variables: dict[str, Any] = {"TIMESTAMP": datetime.now().strftime("%Y%m%d%H%M%S")}

    for scenario in scenarios:
        scenario_result = {
            "id": scenario.get("id", ""),
            "name": scenario.get("name") or "Unnamed scenario",
            "status": "PASS",
            "steps": [],
        }
        allow_write = bool(scenario.get("allow_write", False))
        for raw_step in scenario.get("steps") or []:
            step = _resolve(copy.deepcopy(raw_step), variables)
            method = str(step.get("method") or "GET").upper()
            role = step.get("role") or scenario.get("actor_role") or ""
            step_result = {
                "name": step.get("name") or step.get("path") or "Unnamed step",
                "purpose": step.get("purpose") or "prepare",
                "role": role,
                "method": method,
                "path": step.get("path", ""),
                "status": "PASS",
                "reason": "",
                "extracts": {},
            }
            report["summary"]["total"] += 1
            # 阶段 3：先做写操作审批门禁，再发送网络请求。DELETE 需要场景和步骤两级确认。
            if method in WRITE_METHODS and not allow_write:
                step_result.update(status="SKIPPED", reason="Write operation blocked: enable test-environment write access for this scenario")
            elif method == "DELETE" and not step.get("confirm_delete", False):
                step_result.update(status="SKIPPED", reason="DELETE requires per-step confirmation")
            elif not role or role not in tokens:
                step_result.update(status="SKIPPED", reason=f"No usable token for role: {role or 'not selected'}")
            else:
                response = engine.send_request(tokens[role], step)
                step_result["http"] = response.get("status_code", 0)
                step_result["request"] = response.get("request", {})
                step_result["response_preview"] = (response.get("body") or "")[:1000]
                if response.get("error"):
                    step_result.update(status="FAIL", reason=response["error"])
                else:
                    try:
                        body = json.loads(response.get("body") or "null")
                    except ValueError:
                        body = None
                    # 阶段 4：只从成功响应提取变量，供后续步骤和正式 API 扫描替换 {{name}}。
                    for extract in step.get("extract") or []:
                        key = str(extract.get("name") or "").strip()
                        value = _get_json_path(body, str(extract.get("path") or ""))
                        if key and value is not None:
                            variables[key] = value
                            step_result["extracts"][key] = value
            if step_result["status"] == "PASS":
                report["summary"]["passed"] += 1
            elif step_result["status"] == "SKIPPED":
                report["summary"]["skipped"] += 1
            else:
                report["summary"]["failed"] += 1
                scenario_result["status"] = "FAIL"
            scenario_result["steps"].append(step_result)
        report["scenarios"].append(scenario_result)

    # 阶段 5：场景证据和变量独立落盘，避免把“造数失败”误报成“越权失败”。
    report["variables"] = variables
    report["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    (project_dir / "scenario_results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
