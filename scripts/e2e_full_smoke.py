from __future__ import annotations

import argparse
import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "e2e"


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


class Recorder:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def step(self, name: str, fn: Callable[[], Any]) -> Any:
        started = time.perf_counter()
        try:
            value = fn()
        except Exception as exc:
            self.rows.append({"name": name, "ok": False, "ms": round((time.perf_counter() - started) * 1000), "error": str(exc)})
            return None
        self.rows.append({"name": name, "ok": True, "ms": round((time.perf_counter() - started) * 1000), "value": value})
        return value


def _wait_for_task(page: Page, task: dict[str, Any], timeout_s: int = 25) -> dict[str, Any]:
    task_id = task.get("task_id")
    if not task_id:
        return task
    deadline = time.monotonic() + timeout_s
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = page.request.get(f"/api/tasks/{task_id}", timeout=4000)
        last = response.json()
        if last.get("status") == "done":
            return last.get("result") or last
        if last.get("status") == "failed":
            raise RuntimeError(last.get("error") or last.get("message") or "task failed")
        time.sleep(0.8)
    raise TimeoutError(f"task {task_id} did not finish within {timeout_s}s; last={last}")


def _project_id_by_name(page: Page, project_name: str) -> str:
    projects = page.request.get("/api/projects", timeout=5000).json().get("projects") or []
    for project in projects:
        if project.get("project_name") == project_name:
            return project["project_id"]
    raise RuntimeError(f"project was not found after save: {project_name}")


def _download_statuses(page: Page, project_id: str) -> list[dict[str, Any]]:
    names = [
        "project.json",
        "test_plan.json",
        "generated_config.py",
        "run_results.json",
        "assertion_profile.json",
        "manual_review.json",
        "frontend_results.json",
        "last_run.log",
        "last_frontend_run.log",
        "report.docx",
    ]
    statuses = []
    for name in names:
        response = page.request.get(f"/api/projects/{project_id}/download/{name}", timeout=5000)
        statuses.append({"name": name, "status": response.status})
    return statuses


def run(base_url: str, keep_project: bool = False) -> dict[str, Any]:
    recorder = Recorder()
    console: list[str] = []
    bad_responses: list[dict[str, Any]] = []
    stamp = time.strftime("%Y%m%d%H%M%S")
    project_name = f"Full-E2E-{stamp}"
    project_id = ""

    launch_options: dict[str, Any] = {"headless": True}
    executable = _edge_or_chrome()
    if executable:
        launch_options["executable_path"] = executable

    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_options)
        context = browser.new_context(base_url=base_url, ignore_https_errors=True)
        page = context.new_page()
        page.set_default_timeout(5000)
        page.on("console", lambda msg: console.append(f"{msg.type}: {msg.text}") if msg.type in {"error", "warning"} else None)
        page.on("response", lambda resp: bad_responses.append({"status": resp.status, "url": resp.url}) if resp.status >= 400 and "/download/report.docx" not in resp.url else None)

        try:
            recorder.step("open homepage", lambda: (page.goto("/", wait_until="domcontentloaded", timeout=8000), page.title())[1])
            def register_project_owner() -> str:
                page.click("#openAuthModal")
                page.fill("#authUsername", project_name)
                page.fill("#authPassword", "test-pass")
                page.fill("#authConfirmPassword", "test-pass")
                page.click("#registerBtn")
                page.wait_for_function("(name) => document.querySelector('#userStatus')?.innerText.includes(name)", arg=project_name, timeout=10000)
                return page.locator("#userStatus").inner_text()

            recorder.step("register project owner", register_project_owner)

            def empty_save_validation() -> str:
                page.click("#newProject")
                page.click("#saveBtn")
                page.wait_for_function("() => document.body.innerText.includes('请先填写项目名称、被测环境地址')", timeout=4000)
                return page.locator("#chat").inner_text()[-160:]

            recorder.step("empty save validation", empty_save_validation)

            def fill_base_config() -> dict[str, Any]:
                page.fill("#projectName", project_name)
                page.fill("#baseUrl", base_url)
                page.fill("#timeout", "4")
                page.fill("#goal", "Full browser smoke for project, imports, plans, runs, reviews, history, downloads.")
                page.fill("#tokenUrl", "/healthz")
                page.select_option("#authMethod", label="GET (免密)")
                page.fill("#tokenPath", "status")
                page.set_input_files("#testcaseFile", str(FIXTURES / "sample_testcases.csv"))
                page.wait_for_function("() => document.querySelector('#testcaseDigest')?.value.includes('TC-001')", timeout=6000)
                page.set_input_files("#prdFile", str(FIXTURES / "sample_prd.md"))
                page.wait_for_function("() => document.querySelector('#prdText')?.value.includes('Admin may access')", timeout=6000)
                page.click("#addRole")
                page.click("#addRole")
                role_rows = page.locator(".roleRow")
                count = role_rows.count()
                role_rows.nth(count - 2).locator(".roleName").fill("admin")
                role_rows.nth(count - 2).locator(".roleUser").fill("admin")
                role_rows.nth(count - 2).locator(".rolePass").fill("unused")
                role_rows.nth(count - 1).locator(".roleName").fill("guest")
                role_rows.nth(count - 1).locator(".roleUser").fill("guest")
                role_rows.nth(count - 1).locator(".rolePass").fill("unused")
                return {"roles": role_rows.count()}

            recorder.step("fill base config, files and roles", fill_base_config)

            def save_project() -> dict[str, str]:
                nonlocal project_id
                page.click("#saveBtn")
                page.wait_for_function("(name) => document.querySelector('#currentProject')?.innerText.includes(name)", arg=project_name, timeout=10000)
                project_id = _project_id_by_name(page, project_name)
                return {"project_id": project_id}

            recorder.step("save project", save_project)

            def import_everything() -> dict[str, Any]:
                page.click(".step[data-step='1']")
                page.wait_for_selector("#endpointRows", state="attached", timeout=5000)
                page.click("#addEndpoint")
                last = page.locator("#endpointRows tr:not(.epDetailRow)").last
                last.locator("[data-field='name']").fill("Health manual")
                last.locator("[data-field='method']").fill("GET")
                last.locator("[data-field='path']").fill("/healthz")
                last.locator("[data-field='module']").fill("system")
                last.locator("[data-field='allowed_roles']").fill("admin guest")
                last.locator("[data-field='page_url']").fill("/")
                aria = page.eval_on_selector_all("#endpointRows input", "(inputs) => inputs.map((input) => input.getAttribute('aria-label')).filter(Boolean).length")

                page.fill("#curlText", "curl -X GET 'http://127.0.0.1:8001/api/bootstrap?from=curl' -H 'Accept: application/json'")
                page.click("#importCurl")
                page.wait_for_function("() => Array.from(document.querySelectorAll('#endpointRows input[data-field=\"path\"]')).some((i) => i.value === '/api/bootstrap')", timeout=8000)

                page.set_input_files("#harFile", str(FIXTURES / "sample.har"))
                page.click("#importHar")
                page.wait_for_function("() => Array.from(document.querySelectorAll('#endpointRows input[data-field=\"path\"]')).some((i) => i.value === '/api/bootstrap')", timeout=8000)

                page.set_input_files("#collectionFile", str(FIXTURES / "sample_collection.json"))
                page.click("#importCollection")
                page.wait_for_function("() => Array.from(document.querySelectorAll('#endpointRows input[data-field=\"path\"]')).some((i) => i.value === '/api/projects')", timeout=8000)

                page.locator(".openRoleImport").first.click()
                page.wait_for_selector("#roleImportModal:not([hidden])", timeout=5000)
                page.fill("#roleCurlText", "curl 'http://127.0.0.1:8001/api/projects?from=rolecurl'")
                page.click("#roleImportCurl")
                page.wait_for_function("() => Array.from(document.querySelectorAll('#roleImportRows tr')).some((tr) => tr.innerText.includes('/api/projects'))", timeout=8000)
                page.click("#closeRoleImport")

                page.fill("#endpointSearch", "bootstrap")
                filtered = page.locator("#endpointRows tr").count()
                page.fill("#endpointSearch", "")
                page.select_option("#endpointPageSize", "10")
                imported_rows = page.locator("#endpointRows tr").count()
                page.evaluate(
                    """() => {
                      for (const row of Array.from(document.querySelectorAll('#endpointRows tr'))) {
                        const path = row.querySelector('input[data-field="path"]')?.value || '';
                        if (path !== '/healthz') row.querySelector('button')?.click();
                      }
                    }"""
                )
                page.wait_for_timeout(300)
                return {
                    "imported_rows": imported_rows,
                    "rows_after_trim_for_execution": page.locator("#endpointRows tr").count(),
                    "filtered": filtered,
                    "aria_labels": aria,
                }

            recorder.step("manual, curl, har, collection, role import, search", import_everything)
            recorder.step("save after imports", lambda: (page.click("#saveBtn"), page.wait_for_timeout(800), True)[2])

            def plan_flow() -> dict[str, str]:
                page.click(".step[data-step='2']")
                page.wait_for_selector("#planSections", timeout=5000)
                page.click("#makePlan")
                page.wait_for_function("() => (document.querySelector('#planSections')?.innerText || '').includes('/healthz')", timeout=10000)
                page.fill("#planSearch", "health")
                return {
                    "summary": page.locator("#planSummary").inner_text(timeout=3000),
                    "missing": page.locator("#missing").inner_text(timeout=3000),
                }

            recorder.step("plan generation and search", plan_flow)

            def api_run_flow() -> dict[str, Any]:
                page.click(".step[data-step='3']")
                page.wait_for_selector("#runApi", timeout=5000)
                page.click("#runApi")
                page.wait_for_function("() => (document.querySelector('#runNotice')?.innerText || '').includes('执行完成')", timeout=60000)
                page.fill("#apiResultSearch", "health")
                page.eval_on_selector("#apiStatusFilter", "(select) => { select.value = 'PASS'; select.dispatchEvent(new Event('change', {bubbles: true})); }")
                rows = page.locator("#runRows tr").count()
                detail = page.locator(".resultDetail").first
                if detail.count():
                    detail.click()
                    page.wait_for_selector("#resultDetailModal:not([hidden])", timeout=5000)
                    page.click("#closeResultDetail")
                page.fill("#apiResultSearch", "")
                page.eval_on_selector("#apiStatusFilter", "(select) => { select.value = ''; select.dispatchEvent(new Event('change', {bubbles: true})); }")
                return {"notice": page.locator("#runNotice").inner_text(), "rows": rows}

            recorder.step("api run, result filters, detail modal", api_run_flow)

            def manual_review_flow() -> dict[str, Any]:
                checkbox = page.locator("#runRows input[type='checkbox']").first
                if checkbox.count():
                    checkbox.check()
                page.click("#saveManualReview")
                page.wait_for_timeout(1000)
                return {"count": page.locator("#manualReviewCount").inner_text()}

            recorder.step("manual review save", manual_review_flow)

            def frontend_run_flow() -> dict[str, Any]:
                task = page.request.post(f"/api/projects/{project_id}/run-frontend", timeout=5000).json()
                result = _wait_for_task(page, task, timeout_s=90)
                return {
                    "returncode": result.get("returncode"),
                    "summary": ((result.get("run_summary") or {}).get("frontend") or {}).get("summary") or {},
                }

            recorder.step("frontend interception run", frontend_run_flow)

            def report_flow() -> dict[str, Any]:
                task = page.request.post(f"/api/projects/{project_id}/report", data={"manual_review": {"items": [], "notes": "full smoke"}}, timeout=5000).json()
                result = _wait_for_task(page, task, timeout_s=60)
                return {"returncode": result.get("returncode"), "has_report": result.get("has_report")}

            recorder.step("report task", report_flow)

            def history_flow() -> dict[str, Any]:
                page.locator(".resultCard[data-view='history']").click()
                page.click("#refreshRunHistory")
                page.wait_for_function("() => document.querySelectorAll('#runHistoryRows tr').length > 0", timeout=8000)
                page.fill("#runHistorySearch", base_url)
                rows = page.locator("#runHistoryRows tr").count()
                # This deliberately uses the same click path as a real user.
                # It catches a mismatch between the UI's PATCH request and the
                # backend route before an operator loses a retest annotation.
                page.fill(".runNoteInput", "full smoke note")
                page.click(".saveRunNote")
                page.wait_for_timeout(300)
                run_id = page.locator(".runNoteInput").first.get_attribute("data-id") or ""
                saved_runs = page.request.get(f"/api/projects/{project_id}/runs", timeout=5000).json().get("runs") or []
                note_saved = any(run.get("run_id") == run_id and run.get("note") == "full smoke note" for run in saved_runs)
                if not note_saved:
                    raise AssertionError("history note was not persisted through the UI PATCH action")
                page.click("#toggleCompareDiff")
                return {
                    "rows": rows,
                    "note_saved": note_saved,
                    "compare": page.locator("#compareLeft").inner_text(timeout=3000)[:120],
                }

            recorder.step("run history, search and compare toggle", history_flow)
            recorder.step("download artifacts", lambda: _download_statuses(page, project_id))

            def ai_settings_flow() -> str:
                page.click(".step[data-step='0']")
                page.click("#testAiSettings")
                page.wait_for_function("() => (document.querySelector('#aiConfigStatus')?.innerText || '').length > 0", timeout=12000)
                return page.locator("#aiConfigStatus").inner_text()

            recorder.step("AI settings probe button", ai_settings_flow)

            def delete_flow() -> bool:
                page.click(".step[data-step='history']")
                page.wait_for_selector("#projectRows", timeout=5000)
                page.evaluate("() => { window.confirm = () => true; }")
                page.evaluate(
                    "(name) => { const row = Array.from(document.querySelectorAll('#projectRows tr')).find((tr) => tr.innerText.includes(name)); row?.querySelector('.deleteProject')?.click(); }",
                    project_name,
                )
                page.wait_for_function(
                    "(name) => !Array.from(document.querySelectorAll('#projectRows tr')).some((tr) => tr.innerText.includes(name))",
                    arg=project_name,
                    timeout=10000,
                )
                return True

            recorder.step("delete generated project", delete_flow)
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
        "results": recorder.rows,
        "console_warnings_or_errors": console,
        "bad_responses": bad_responses,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--keep-project", action="store_true")
    args = parser.parse_args()
    result = run(args.base_url.rstrip("/"), keep_project=args.keep_project)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if all(row["ok"] for row in result["results"]) and not result["console_warnings_or_errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
