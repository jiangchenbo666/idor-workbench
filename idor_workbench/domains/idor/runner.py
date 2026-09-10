"""IDOR Workbench 执行协调器。

视图层把已经授权并加载好的项目字典交给 runner；runner 不读取或修改全局 config.py。
它只负责编排“场景准备 -> API 扫描 -> AI 结果复核 -> 产物落盘 -> 可选报告”，
HTTP 细节、断言规则、场景规则和报告格式分别留在对应领域模块。
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.parse import urljoin

import httpx

from idor_workbench.foundation.paths import PROJECTS_DIR
from idor_workbench.domains.idor.findings import create_findings_from_execution_results


DATA_DIR = PROJECTS_DIR


def _run_ai_config() -> dict[str, object]:
    """CLI/后台执行未显式传配置时，从环境变量构造 AI 运行配置。"""
    return {
        "provider": os.environ.get("IDOR_AI_PROVIDER", "ollama"),
        "base_url": os.environ.get("IDOR_AI_BASE_URL", "http://127.0.0.1:11434"),
        "model": os.environ.get("IDOR_AI_MODEL", "qwen3:8b"),
        "api_key": os.environ.get("IDOR_AI_API_KEY", ""),
        "timeout": os.environ.get("IDOR_RUN_AI_TIMEOUT", os.environ.get("IDOR_AI_TIMEOUT", "30")),
        "temperature": os.environ.get("IDOR_AI_TEMPERATURE", "0.2"),
        "sync_model": os.environ.get("IDOR_RUN_AI_SYNC", "").strip().lower() in {"1", "true", "yes", "on"},
    }


def load_project(project_id: str) -> dict:
    """CLI 入口按项目 ID 加载已持久化快照；Web 路由不会通过此函数绕过鉴权。"""
    path = DATA_DIR / project_id / "project.json"
    if not path.exists():
        raise SystemExit(f"Project not found: {project_id}")
    return json.loads(path.read_text(encoding="utf-8"))


class ScenarioHttpEngine:
    """仅供场景准备步骤使用的同步 HTTP 适配器。

    场景通常串行依赖上一步提取的资源 ID，所以这里使用同步客户端；正式接口矩阵
    扫描仍由 ``execution.py`` 的异步引擎完成。
    """

    def __init__(self, project: dict):
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
        self.client.close()

    def get_token(self, username: str, password: str, login_type: str = "WEB") -> str | None:
        """按项目鉴权方式登录，为场景步骤取得短期 token。"""
        url = urljoin(f"{self.context.base_url}/", str(self.context.token_url).lstrip("/"))
        try:
            headers = {"XloginType": login_type}
            auth_method = (self.context.auth_method or "json_post").lower()
            if auth_method == "form_post":
                resp = self.client.post(
                    url,
                    data={self.context.username_field: username, self.context.password_field: password, "grant_type": "password", "loginType": login_type},
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
                    json={self.context.username_field: username, self.context.password_field: password},
                    headers={**headers, "Content-Type": "application/json"},
                )
            return self.execution_engine._extract_token_from_response(resp.json(), self.context.token_path)
        except Exception:
            return None

    def send_request(self, token: str, endpoint: dict) -> dict:
        """执行一个场景请求并返回统一响应快照；落盘前会脱敏请求凭据。"""
        request_snapshot = self.execution_engine._prepare_request(self.context, token, endpoint)
        try:
            resp = self.client.request(
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
                "request": self.execution_engine._safe_request_snapshot(request_snapshot),
                "response_headers": dict(resp.headers),
            }
        except Exception as exc:
            return {
                "status_code": 0,
                "body": "",
                "error": str(exc),
                "request": self.execution_engine._safe_request_snapshot(request_snapshot),
                "response_headers": {},
            }


def prepare_business_scenarios(project: dict, project_dir: Path) -> dict:
    """在权限矩阵扫描前执行造数/查 ID 场景，并保证同步客户端最终关闭。"""
    from idor_workbench.domains.idor.scenarios import execute_scenarios

    engine = ScenarioHttpEngine(project)
    try:
        return execute_scenarios(project, project_dir, engine)
    finally:
        engine.close()


def attach_ai_run_analysis(data: dict, project_dir: Path, ai_config: dict | None = None) -> dict:
    """在规则结果之上附加 AI 审查；模型异常时保留原结果并写入降级说明。"""
    if not data:
        return data
    try:
        from idor_workbench.domains.idor.ai import analyze_run
        from idor_workbench.domains.idor.assertions import apply_ai_tuning

        assertion_profile = data.get("assertion_profile") or {}
        ai_insight = analyze_run(data, assertion_profile, ai_config or _run_ai_config())
        data["ai_analysis"] = ai_insight

        tuning = ai_insight.get("assertion_tuning") if isinstance(ai_insight, dict) else None
        if tuning:
            tuned_profile = apply_ai_tuning(assertion_profile, tuning)
            data["assertion_profile"] = tuned_profile
            (project_dir / "assertion_ai_tuning.json").write_text(
                json.dumps(ai_insight, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        (project_dir / "ai_run_analysis.json").write_text(
            json.dumps(ai_insight, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        data["ai_analysis"] = {
            "summary": {
                "risk_text": "AI 结果审计失败，已保留规则引擎执行结果。",
                "p0": 0,
                "p1": 0,
                "p2": 0,
            },
            "risk_findings": [],
            "manual_retest_priorities": [],
            "assertion_tuning": {},
            "report_brief": str(exc),
            "ai_actions": [f"AI 执行结果审计异常：{exc}"],
        }
        (project_dir / "ai_run_analysis.json").write_text(
            json.dumps(data["ai_analysis"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return data


def write_execution_artifacts(project_dir: Path, data: dict) -> None:
    """一次性写出运行结果与断言学习档案，供历史、报告和下次调优复用。"""
    (project_dir / "run_results.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (project_dir / "assertion_profile.json").write_text(
        json.dumps(data.get("assertion_profile", {}), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    create_findings_from_execution_results(
            project_dir / "findings.json",
            data.get("results") or [],
        )


def execute_api_scan(project: dict, project_dir: Path, ai_config: dict | None = None) -> dict | None:
    """组合场景变量、异步接口扫描和 AI 复核，但暂不负责生成 Word 报告。"""
    from idor_workbench.domains.idor import execution as execution_engine

    scenario_data = prepare_business_scenarios(project, project_dir)
    data = execution_engine.collect_all_results(project, scenario_data.get("variables") or {})
    if not data:
        return None
    data["project"] = project
    data["scenario_results"] = scenario_data
    return attach_ai_run_analysis(data, project_dir, ai_config)


def run_api_project(project: dict, project_dir: Path, report: bool = False, ai_config: dict | None = None) -> int:
    """API 执行总入口；返回 0 成功、2 无结果，并可基于已有结果单独生成报告。"""
    report_path = project_dir / "report.docx"
    run_results_path = project_dir / "run_results.json"

    if report and run_results_path.exists():
        data = json.loads(run_results_path.read_text(encoding="utf-8"))
        data["project"] = project
        if not data.get("ai_analysis"):
            data = attach_ai_run_analysis(data, project_dir, ai_config)
            write_execution_artifacts(project_dir, data)
    else:
        data = execute_api_scan(project, project_dir, ai_config)
        if not data:
            return 2
        write_execution_artifacts(project_dir, data)

    if report:
        from idor_workbench.domains.idor.reporting import generate_report

        generate_report(data, report_path)
    return 0


def run_frontend_project(project: dict, project_dir: Path) -> int:
    """前端拦截检测入口；结果独立保存，避免和 API 断言结果混在一起。"""
    from idor_workbench.domains.idor.frontend_probe import run_frontend_checks

    frontend_data = run_frontend_checks(project, project_dir)
    (project_dir / "frontend_results.json").write_text(
        json.dumps(frontend_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if frontend_data.get("setup_error"):
        print(frontend_data["setup_error"])
        return 3
    print(json.dumps(frontend_data.get("summary", {}), ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--frontend", action="store_true")
    args = parser.parse_args()

    project = load_project(args.project_id)
    project_dir = DATA_DIR / args.project_id

    if args.frontend:
        return run_frontend_project(project, project_dir)
    return run_api_project(project, project_dir, report=args.report)


if __name__ == "__main__":
    raise SystemExit(main())
