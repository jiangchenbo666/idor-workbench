from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def _edge_or_chrome() -> str | None:
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
            return candidate
    return None


def _project_id_by_name(page: Page, name: str) -> str:
    projects = page.request.get("/api/projects", timeout=5000).json().get("projects") or []
    for project in projects:
        if project.get("project_name") == name:
            return str(project["project_id"])
    raise RuntimeError(f"project not found: {name}")


def _wait_for_text(page: Page, selector: str, text: str, timeout: int = 120000) -> str:
    page.wait_for_function(
        """([selector, text]) => (document.querySelector(selector)?.innerText || '').includes(text)""",
        arg=[selector, text],
        timeout=timeout,
    )
    return page.locator(selector).inner_text(timeout=5000)


def _wait_for_any_text(page: Page, selector: str, texts: list[str], timeout: int = 120000) -> str:
    page.wait_for_function(
        """([selector, texts]) => {
          const value = document.querySelector(selector)?.innerText || '';
          return texts.some((text) => value.includes(text));
        }""",
        arg=[selector, texts],
        timeout=timeout,
    )
    return page.locator(selector).inner_text(timeout=5000)


def _wait_for_task(page: Page, task: dict[str, Any], timeout_s: int = 120) -> dict[str, Any]:
    task_id = task.get("task_id")
    if not task_id:
        return task
    deadline = time.monotonic() + timeout_s
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = page.request.get(f"/api/tasks/{task_id}", timeout=5000).json()
        if last.get("status") == "done":
            return last.get("result") or {}
        if last.get("status") == "failed":
            raise RuntimeError(last.get("error") or last.get("message") or "task failed")
        time.sleep(1)
    raise TimeoutError(f"task {task_id} timeout; last={last}")


def run(base_url: str, keep_project: bool = False) -> dict[str, Any]:
    project_name = f"PW-Biz-{time.strftime('%Y%m%d%H%M%S')}"
    project_id = ""
    steps: list[dict[str, Any]] = []
    console: list[str] = []
    bad_responses: list[dict[str, Any]] = []

    def step(name: str, fn) -> Any:
        started = time.perf_counter()
        try:
            value = fn()
            steps.append({"name": name, "ok": True, "ms": round((time.perf_counter() - started) * 1000), "value": value})
            return value
        except Exception as exc:
            steps.append({"name": name, "ok": False, "ms": round((time.perf_counter() - started) * 1000), "error": str(exc)})
            raise

    launch_options: dict[str, Any] = {"headless": True}
    executable = _edge_or_chrome()
    if executable:
        launch_options["executable_path"] = executable

    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_options)
        context = browser.new_context(base_url=base_url, ignore_https_errors=True)
        page = context.new_page()
        page.set_default_timeout(8000)
        page.on("console", lambda msg: console.append(f"{msg.type}: {msg.text}") if msg.type in {"error", "warning"} else None)
        page.on("response", lambda resp: bad_responses.append({"status": resp.status, "url": resp.url}) if resp.status >= 500 else None)

        try:
            step("open workbench", lambda: (page.goto("/", wait_until="domcontentloaded"), page.title())[1])
            def register_project_owner() -> str:
                page.click("#openAuthModal")
                page.fill("#authUsername", project_name)
                page.fill("#authPassword", "test-pass")
                page.fill("#authConfirmPassword", "test-pass")
                page.click("#registerBtn")
                page.wait_for_function("(name) => document.querySelector('#userStatus')?.innerText.includes(name)", arg=project_name, timeout=10000)
                return page.locator("#userStatus").inner_text()

            step("register project owner", register_project_owner)
            page.evaluate("() => { state.aiSettings = {provider: 'ollama', sync_model: false, timeout: '1'}; }")

            def reject_result_dom_injection() -> dict[str, Any]:
                """Exercise the real result renderer with hostile API-controlled text."""
                marker = '<img src=x onerror="window.__idorResultXss += 1">'
                page.evaluate(
                    """(marker) => {
                      window.__idorResultXss = 0;
                      state.runResults = [{
                        role: marker,
                        section: marker,
                        status: 'FAIL',
                        method: 'GET',
                        path: marker,
                        name: marker,
                        http: 403,
                        verdict: marker,
                        confidence: marker,
                        failures: [marker],
                        replay: {url: '/hostile'},
                      }];
                      state.runSummary = {total: 1, failed: 1};
                      renderRunPage();
                    }""",
                    marker,
                )
                page.wait_for_timeout(100)
                injected = page.evaluate("() => window.__idorResultXss")
                image_count = page.locator("#runRows img").count()
                rendered_text = page.locator("#runRows").inner_text()
                page.evaluate(
                    """() => {
                      state.runResults = [];
                      state.runSummary = {};
                      renderRunPage();
                    }"""
                )
                if injected or image_count:
                    raise AssertionError(f"result DOM injection executed={injected}, image_count={image_count}")
                if marker not in rendered_text:
                    raise AssertionError("escaped hostile value was not preserved as visible text")
                return {"executed": injected, "created_images": image_count}

            step("result renderer rejects DOM injection", reject_result_dom_injection)

            def configure_project() -> dict[str, Any]:
                page.click("#newProject")
                page.fill("#projectName", project_name)
                page.fill("#baseUrl", base_url)
                page.fill("#timeout", "4")
                page.fill("#goal", "只进行系统管理员和安全管理员的交叉越权测试，并检查 Playwright 人工录制的项目配置链路。")
                page.fill("#tokenUrl", "/healthz")
                page.select_option("#authMethod", "get_no_password")
                page.fill("#tokenPath", "status")
                page.click("#addRole")
                page.click("#addRole")
                rows = page.locator(".roleRow")
                rows.nth(rows.count() - 2).locator(".roleName").fill("系统管理员")
                rows.nth(rows.count() - 2).locator(".roleUser").fill("sys")
                rows.nth(rows.count() - 2).locator(".rolePass").fill("unused")
                rows.nth(rows.count() - 1).locator(".roleName").fill("安全管理员")
                rows.nth(rows.count() - 1).locator(".roleUser").fill("sec")
                rows.nth(rows.count() - 1).locator(".rolePass").fill("unused")
                return {"roles": rows.count()}

            def save_project() -> dict[str, str]:
                nonlocal project_id
                page.click("#saveBtn")
                page.wait_for_function("(name) => document.querySelector('#currentProject')?.innerText.includes(name)", arg=project_name, timeout=15000)
                project_id = _project_id_by_name(page, project_name)
                return {"project_id": project_id}

            step("configure project and roles", configure_project)
            step("save project", save_project)

            def add_manual_endpoint() -> dict[str, Any]:
                page.click(".step[data-step='1']")
                page.wait_for_selector("#addEndpoint", state="visible")
                before = page.locator("#endpointRows tr:not(.epDetailRow)").count()
                page.click("#addEndpoint")
                page.wait_for_function(
                    "(before) => document.querySelectorAll('#endpointRows tr:not(.epDetailRow)').length > before",
                    arg=before,
                )
                last = page.locator("#endpointRows tr:not(.epDetailRow)").last
                last.locator("[data-field='name']").fill("Bootstrap")
                last.locator("[data-field='method']").fill("GET")
                last.locator("[data-field='path']").fill("/api/bootstrap")
                last.locator("[data-field='module']").fill("workbench")
                last.locator("[data-field='allowed_roles']").fill("系统管理员")
                last.locator("[data-field='page_url']").fill("/")
                page.click("#saveBtn")
                page.wait_for_timeout(800)
                return {"endpoint_rows": page.locator("#endpointRows tr:not(.epDetailRow)").count()}

            step("add manual endpoint through UI", add_manual_endpoint)

            def manual_recording_only() -> dict[str, Any]:
                return {
                    "manual_recording_visible": page.locator("#startManualRecording").is_visible(),
                    "removed_browser_controls": page.locator("#recordBrowserEndpoints, #discoverBrowserPages").count(),
                }

            step("manual recording is the only browser collection entry", manual_recording_only)

            def plan_and_ai_review() -> dict[str, Any]:
                page.click(".step[data-step='2']")
                page.click("#makePlan")
                _wait_for_text(page, "#planActionStatus", "计划生成完成", timeout=60000)
                page.click("[data-ai-toggle='plan']")
                page.wait_for_selector("#aiPlanInsights .aiInsightBody:not([hidden])", timeout=5000)
                return {
                    "plan_status": page.locator("#planActionStatus").inner_text(),
                    "ai_box": page.locator("#aiPlanInsights").inner_text(timeout=5000)[:800],
                }

            step("plan generation with AI fallback review", plan_and_ai_review)

            def api_task_direct() -> dict[str, Any]:
                task = page.request.post(f"/api/projects/{project_id}/run", data={"report": False, "manual_review": {"items": [], "notes": ""}}, timeout=5000).json()
                result = _wait_for_task(page, task, timeout_s=120)
                return {
                    "returncode": result.get("returncode"),
                    "total": (result.get("run_summary") or {}).get("total"),
                    "has_run_results": result.get("has_run_results"),
                }

            step("backend API run task", api_task_direct)
        finally:
            if project_id and not keep_project:
                try:
                    page.request.delete(f"/api/projects/{project_id}", timeout=5000)
                except Exception:
                    pass
            context.close()
            browser.close()

    return {
        "project_name": project_name,
        "project_id": project_id,
        "steps": steps,
        "console_warnings_or_errors": console,
        "bad_responses": bad_responses,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--keep-project", action="store_true")
    args = parser.parse_args()
    result = run(args.base_url.rstrip("/"), args.keep_project)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if all(item["ok"] for item in result["steps"]) and not result["bad_responses"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
