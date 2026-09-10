"""从已完成的 IDOR 执行结果生成 Word 报告。

报告层只消费已落盘的结构化证据，不重新请求被测系统，也不重新判断 PASS/FAIL。
这样重复生成报告不会改变测试结论，且报告可追溯到同一份 run_results.json。
"""
from __future__ import annotations

import argparse
import json  # L2 IDOR report generator.
from datetime import datetime
from pathlib import Path
from typing import Any

from docx import Document
from docx.document import Document as DocumentObject
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor


def safe_text(value: Any, limit: int = 500) -> str:
    """移除 Word 不接受的控制字符，并限制单元格内容长度。"""
    text = "" if value is None else str(value)
    cleaned = []
    for ch in text:
        code = ord(ch)
        cleaned.append(ch if ch in "\n\r\t" or code >= 32 else " ")
    return "".join(cleaned)[:limit]


def safe_json(value: Any, limit: int = 1200) -> str:
    """将请求参数/响应证据格式化为可读 JSON，并在不可序列化时安全降级。"""
    if value in (None, "", {}):
        return "（无）"
    try:
        text = json.dumps(value, ensure_ascii=False, indent=2)
    except TypeError:
        text = str(value)
    return safe_text(text, limit)


def project_from_data(data: dict[str, Any]) -> dict[str, Any]:
    """从运行结果中安全取得项目快照，兼容旧产物缺少 project 的情况。"""
    project = data.get("project")
    return dict(project) if isinstance(project, dict) else {}


def endpoints_from_data(data: dict[str, Any]) -> list[dict[str, Any]]:
    """优先读取项目接口；旧结果缺少项目快照时从执行行反推最小清单。"""
    project = project_from_data(data)
    endpoints = project.get("endpoints") if isinstance(project.get("endpoints"), list) else []
    if endpoints:
        return endpoints
    seen = {}
    for row in data.get("results") or []:
        key = f"{row.get('method', 'GET')}:{row.get('path', '')}"
        if row.get("path") and key not in seen:
            seen[key] = {"method": row.get("method", "GET"), "path": row.get("path", "")}
    return list(seen.values())


def set_cell(cell, text: Any, bold: bool = False, color: RGBColor | None = None, size: float = 8, font_name: str = "微软雅黑") -> None:
    """统一表格单元格字体、字号、对齐和颜色，避免各章节样式漂移。"""
    cell.text = ""
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
    paragraph = cell.paragraphs[0]
    run = paragraph.add_run(str(text))
    run.font.size = Pt(size)
    run.font.name = font_name
    run._element.rPr.rFonts.set(qn("w:eastAsia"), font_name)
    run.bold = bold
    if color:
        run.font.color.rgb = color


def add_heading(doc: DocumentObject, text: str, level: int = 1) -> None:
    """添加使用统一中文字体的章节标题。"""
    heading = doc.add_heading(text, level=level)
    for run in heading.runs:
        run.font.name = "微软雅黑"
        run._element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")


def add_summary(doc: DocumentObject, data: dict[str, Any]) -> None:
    """写报告标题和环境、角色、总数等执行概况。"""
    project = project_from_data(data)
    endpoints = endpoints_from_data(data)

    title = doc.add_heading("越权测试报告", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    subtitle = doc.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle.add_run(f"项目：{project.get('project_name') or data.get('project_name') or '未命名项目'}").font.size = Pt(11)
    time_line = doc.add_paragraph()
    time_line.alignment = WD_ALIGN_PARAGRAPH.CENTER
    time_line.add_run(f"测试时间：{data.get('time') or datetime.now().strftime('%Y-%m-%d %H:%M:%S')}").font.size = Pt(11)

    add_heading(doc, "一、测试概况")
    rows = [
        ("测试环境", data.get("env") or project.get("base_url", "")),
        ("测试时间", data.get("time", "")),
        ("测试角色", "、".join(data.get("roles") or [role.get("name", "") for role in project.get("roles") or [] if role.get("name")])),
        ("接口数量", str(len(endpoints))),
        ("执行总数", data.get("total", 0)),
        ("通过", data.get("passed", 0)),
        ("失败", data.get("failed", 0)),
        ("跳过/错误", data.get("skipped", 0)),
    ]
    table = doc.add_table(rows=len(rows), cols=2, style="Table Grid")
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for index, (label, value) in enumerate(rows):
        set_cell(table.cell(index, 0), label, bold=True, size=9)
        set_cell(table.cell(index, 1), value, size=9)


def add_result_overview(doc: DocumentObject, results: list[dict[str, Any]]) -> None:
    """写入所有接口结果的紧凑总览，便于先定位角色、状态和路径。"""
    add_heading(doc, "二、接口执行结果总览")
    if not results:
        doc.add_paragraph("未产生接口执行结果。")
        return

    headers = ["序号", "角色", "分组", "状态", "方法", "接口", "HTTP", "结论"]
    table = doc.add_table(rows=1 + len(results), cols=len(headers), style="Table Grid")
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for col, header in enumerate(headers):
        set_cell(table.cell(0, col), header, bold=True, size=8)

    for row_index, row in enumerate(results, start=1):
        assertion = row.get("assertion") or {}
        values = [
            row_index,
            row.get("role", ""),
            row.get("section", ""),
            row.get("status", ""),
            row.get("method", ""),
            row.get("path", ""),
            row.get("status_code", ""),
            assertion.get("verdict") or "（未记录）",
        ]
        color = RGBColor(0, 100, 0) if row.get("status") == "PASS" else RGBColor(200, 0, 0)
        for col, value in enumerate(values):
            set_cell(table.cell(row_index, col), safe_text(value, 260), size=7.5, color=color if col == 3 else None)


def add_failed_details(doc: DocumentObject, results: list[dict[str, Any]]) -> None:
    """仅展开非 PASS 结果，并附脱敏请求、断言依据和失败原因。"""
    add_heading(doc, "三、失败与复核详情")
    failed_rows = [row for row in results if row.get("status") != "PASS"]
    if not failed_rows:
        doc.add_paragraph("未发现失败或错误接口。")
        return

    for index, row in enumerate(failed_rows, start=1):
        assertion = row.get("assertion") or {}
        request = row.get("request") or {}
        doc.add_paragraph(f"{index}. [{row.get('status')}] {row.get('role', '')} {row.get('method', '')} {row.get('path', '')}")
        details = [
            ("分组", row.get("section", "")),
            ("HTTP", row.get("status_code", "")),
            ("请求 URL", request.get("url") or row.get("path", "")),
            ("请求参数", safe_json(row.get("req_params") or request.get("params") or {}, 1000)),
            ("请求体", safe_json(row.get("req_body") if "req_body" in row else request.get("body"), 1600)),
            ("断言预期", row.get("expected", "")),
            ("断言结论", assertion.get("verdict") or "（未记录）"),
            ("证据", "；".join(assertion.get("evidence") or [])),
            ("失败原因", "\n".join(row.get("failures") or []) or row.get("body_preview") or "（无）"),
        ]
        table = doc.add_table(rows=len(details), cols=2, style="Table Grid")
        for detail_index, (label, value) in enumerate(details):
            set_cell(table.cell(detail_index, 0), label, bold=True, size=8)
            set_cell(table.cell(detail_index, 1), safe_text(value, 1800), size=7.5, font_name="Consolas" if label in {"请求参数", "请求体"} else "微软雅黑")
        doc.add_paragraph("")


def add_frontend_results(doc: DocumentObject, frontend: dict[str, Any]) -> None:
    """写入页面拦截结果；没有执行证据时展示诊断信息而不是空白章节。"""
    if not frontend:
        return
    add_heading(doc, "四、前端拦截检测")
    summary = frontend.get("summary") or {}
    doc.add_paragraph(f"总数 {summary.get('total', 0)}，通过 {summary.get('passed', 0)}，失败 {summary.get('failed', 0)}，错误 {summary.get('error', 0)}。")
    rows = frontend.get("results") or []
    if not rows:
        doc.add_paragraph(safe_text(frontend.get("setup_error") or frontend.get("note") or "未产生前端检测详情。", 800))
        return
    headers = ["角色", "页面", "状态", "结论", "证据"]
    table = doc.add_table(rows=1 + len(rows), cols=len(headers), style="Table Grid")
    for col, header in enumerate(headers):
        set_cell(table.cell(0, col), header, bold=True, size=8)
    for row_index, row in enumerate(rows, start=1):
        values = [row.get("role", ""), row.get("page_url") or row.get("full_url") or "", row.get("status", ""), row.get("conclusion", ""), "；".join(row.get("signals") or [])]
        for col, value in enumerate(values):
            set_cell(table.cell(row_index, col), safe_text(value, 700), size=7.5)


def add_ai_analysis(doc: DocumentObject, data: dict[str, Any]) -> None:
    """追加 AI 二次审计和复测建议；没有 AI 结果时整节省略。"""
    ai = data.get("ai_analysis") or data.get("ai_run_analysis") or {}
    if not ai:
        return

    add_heading(doc, "五、AI 二次审计")
    summary = ai.get("summary") or {}
    risk_text = summary.get("risk_text") if isinstance(summary, dict) else ""
    report_brief = ai.get("report_brief") or ""
    if risk_text or report_brief:
        doc.add_paragraph(safe_text(risk_text or report_brief, 1000))

    risk_findings = ai.get("risk_findings") or []
    if risk_findings:
        doc.add_paragraph("AI 风险判断")
        headers = ["优先级", "角色", "方法", "接口", "状态", "原因", "证据"]
        table = doc.add_table(rows=1 + len(risk_findings), cols=len(headers), style="Table Grid")
        for col, header in enumerate(headers):
            set_cell(table.cell(0, col), header, bold=True, size=8)
        for row_index, row in enumerate(risk_findings, start=1):
            values = [row.get("priority", ""), row.get("role", ""), row.get("method", ""), row.get("path", ""), row.get("status", ""), row.get("reason", ""), row.get("evidence", "")]
            for col, value in enumerate(values):
                set_cell(table.cell(row_index, col), safe_text(value, 700), size=7.5)

    retests = ai.get("manual_retest_priorities") or []
    if retests:
        doc.add_paragraph("人工复测优先级")
        headers = ["优先级", "角色", "方法", "接口", "复测说明"]
        table = doc.add_table(rows=1 + len(retests), cols=len(headers), style="Table Grid")
        for col, header in enumerate(headers):
            set_cell(table.cell(0, col), header, bold=True, size=8)
        for row_index, row in enumerate(retests, start=1):
            values = [row.get("priority", ""), row.get("role", ""), row.get("method", ""), row.get("path") or row.get("url", ""), row.get("manual_retest") or row.get("reason", "")]
            for col, value in enumerate(values):
                set_cell(table.cell(row_index, col), safe_text(value, 700), size=7.5)

    actions = ai.get("ai_actions") or []
    if actions:
        doc.add_paragraph("AI 已执行动作")
        for action in actions[:8]:
            doc.add_paragraph(safe_text(action, 500), style=None)


def add_conclusion(doc: DocumentObject, data: dict[str, Any]) -> None:
    """根据已经确定的统计值生成报告结论，不在报告层重新执行断言。"""
    add_heading(doc, "六、测试结论")
    failed = int(data.get("failed") or 0)
    total = int(data.get("total") or 0)
    if failed:
        text = f"本次共执行 {total} 条接口用例，发现 {failed} 条失败/异常结果，需要结合复现请求和业务预期继续人工确认。"
        color = RGBColor(200, 0, 0)
    else:
        text = f"本次共执行 {total} 条接口用例，自动断言未发现失败结果。"
        color = RGBColor(0, 100, 0)
    paragraph = doc.add_paragraph()
    run = paragraph.add_run(text)
    run.bold = True
    run.font.size = Pt(11)
    run.font.color.rgb = color


def generate_report(data: dict[str, Any], output_path: str | Path) -> Path:
    """按固定章节生成报告并返回输出路径；输入必须是已收集的执行数据。"""
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    doc = Document()
    section = doc.sections[0]
    section.orientation = WD_ORIENT.LANDSCAPE
    section.page_width, section.page_height = section.page_height, section.page_width
    section.top_margin = Cm(1.2)
    section.bottom_margin = Cm(1.2)
    section.left_margin = Cm(1.2)
    section.right_margin = Cm(1.2)

    style = doc.styles["Normal"]
    style.font.name = "微软雅黑"
    style.font.size = Pt(10)
    style.element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")

    results = data.get("results") or []
    add_summary(doc, data)
    add_result_overview(doc, results)
    add_failed_details(doc, results)
    add_frontend_results(doc, data.get("frontend_results") or {})
    add_ai_analysis(doc, data)
    add_conclusion(doc, data)

    doc.save(str(output))
    return output


def main() -> int:
    """命令行入口：读取 run_results.json 并在指定位置生成报告。"""
    parser = argparse.ArgumentParser(description="Generate an IDOR workbench Word report from run_results.json.")
    parser.add_argument("run_results", nargs="?", default="run_results.json")
    parser.add_argument("-o", "--output")
    args = parser.parse_args()

    run_results_path = Path(args.run_results)
    data = json.loads(run_results_path.read_text(encoding="utf-8"))
    output = generate_report(data, args.output or run_results_path.with_name("report.docx"))
    print(f"报告已生成: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
