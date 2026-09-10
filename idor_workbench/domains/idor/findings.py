from dataclasses import dataclass, fields, field
from enum import StrEnum
import json
from pathlib import Path



class FindingStatus(StrEnum):
    """Finding 的状态枚举：规定一张风险卡片只能处在这些固定状态之一。"""

    NEEDS_HUMAN_REVIEW = "needs_human_review"
    CONFIRMED = "confirmed"
    FALSE_POSITIVE = "false_positive"
    WAITING_FIX = "waiting_fix"
    RETEST_PASSED = "retest_passed"
    CLOSED = "closed"


REVIEW_ALLOWED_STATUSES = {FindingStatus.NEEDS_HUMAN_REVIEW}
WAITING_FIX_ALLOWED_STATUSES={FindingStatus.CONFIRMED,
                              FindingStatus.WAITING_FIX}
RETEST_PASSED_ALLOWED_STATUSES={FindingStatus.WAITING_FIX}
CLOSED_ALLOWED_STATUSES={FindingStatus.RETEST_PASSED, FindingStatus.FALSE_POSITIVE} 



@dataclass
class Finding:
    """一张越权风险卡片，记录疑似漏洞证据、人工复核、修复版本和复测历史。"""

    finding_id: str
    status: FindingStatus
    actor_role: str
    endpoint: str
    expected_result: str
    actual_status_code: int
    actual_response_summary: str
    assertion_reason: str
    human_label: str | None = None
    human_note: str | None = None
    fix_version: str | None = None
    retest_run_id: str | None = None
    retest_note: str | None = None
    close_note: str | None = None
    retest_history: list[dict] = field(default_factory=list)




    def _ensure_status(self, allowed_statuses : set[FindingStatus], message: str) -> None:
        """校验当前状态是否在允许状态集合里；不允许就抛 ValueError。"""

        if self.status not in allowed_statuses:
            raise ValueError(message)



        
    def mark_false_positive(self, note: str) -> None:
        """人工复核为误报：只能从 needs_human_review 进入 false_positive。"""

        self._ensure_waiting_for_review()
        self.status = FindingStatus.FALSE_POSITIVE
        self.human_label = "false_positive"
        self.human_note = note


    def mark_confirmed(self, note: str) -> None:
        """人工复核为真漏洞：只能从 needs_human_review 进入 confirmed。"""

        self._ensure_waiting_for_review()
        self.status = FindingStatus.CONFIRMED
        self.human_label = "confirmed"
        self.human_note = note


    def _ensure_waiting_for_review(self) -> None:
        """校验当前 Finding 是否还处在等待人工复核状态。"""

        self._ensure_status(REVIEW_ALLOWED_STATUSES, "only findings awaiting human review can be reviewed")


    def mark_waiting_fix(self, fix_version: str) -> None:
        """标记为等待修复，并记录开发承诺/提交的修复版本。"""

        self._ensure_status(WAITING_FIX_ALLOWED_STATUSES, "only confirmed findings can be marked as waiting fix")
        self.status = FindingStatus.WAITING_FIX
        self.fix_version = fix_version
            
   
    def mark_retest_passed(self, retest_run_id: str, note: str) -> None:
        """复测通过：从 waiting_fix 进入 retest_passed，并追加一条复测历史。"""

        self._ensure_status(RETEST_PASSED_ALLOWED_STATUSES, "only findings awaiting fix can be marked as retest passed")
        self.status = FindingStatus.RETEST_PASSED
        self.retest_run_id = retest_run_id
        self.retest_note = note
        self.retest_history.append({
            "fix_version": self.fix_version,
            "retest_run_id": retest_run_id,
            "passed": True,
            "note": note})

    def mark_closed(self, note: str) -> None:
        """关闭风险：只有复测通过或确认误报之后，才允许关闭。"""

        self._ensure_status(CLOSED_ALLOWED_STATUSES, "only findings awaiting retest passed or false positive can be marked as closed")
        self.status = FindingStatus.CLOSED
        self.close_note = note


    def mark_retest_failed(self, retest_run_id: str, note: str) -> None:
        """复测失败：状态保持 waiting_fix，但记录本次失败的复测历史。"""

        self._ensure_status(RETEST_PASSED_ALLOWED_STATUSES, "only findings awaiting fix can be marked as retest failed")
        self.retest_run_id = retest_run_id
        self.retest_note = note
        self.status = FindingStatus.WAITING_FIX
        self.retest_history.append({
            "fix_version": self.fix_version,
            "retest_run_id": retest_run_id,
            "passed": False,
             "note": note})

    def to_dict(self) -> dict:
        """把 Finding 对象转换成可写入 JSON 的 dict；status 枚举会转成字符串。"""

        data={}
        for field in fields(self):
            field_name = field.name
            value = getattr(self,field_name)

            if field_name == "status":
                data[field_name] = value.value
            else:
                data[field_name] = value

        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Finding":
        """把从 JSON 读出来的 dict 恢复成 Finding 对象；status 字符串会转回枚举。"""

        copied_data = dict(data)
        copied_data.setdefault("finding_id", "")
        status = FindingStatus(copied_data["status"])
        copied_data["status"] = status

        return cls(**copied_data)



def findings_to_dicts(findings: list[Finding]) -> list[dict]:
    """把多张 Finding 卡片批量转换成 dict 列表，方便保存成 JSON。"""

    dicts = []
    for finding in findings:
        dicts.append(finding.to_dict())
    return dicts


def findings_from_dicts( items: list[dict]) -> list[Finding]:
    """把 dict 列表批量恢复成 Finding 对象列表。"""

    findings = []
    for item in items:
        findings.append(Finding.from_dict(item))
    return findings


def save_findings(path: str, findings: list[Finding]) -> None:
    """把 Finding 列表保存到指定 JSON 文件。"""

    dicts = findings_to_dicts(findings)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(dicts, f, ensure_ascii=False)


def load_findings(path: str) -> list[Finding]:
    """从指定 JSON 文件读取 Finding 列表；文件不存在时返回空列表。

    历史 findings.json 可能缺少 finding_id，这里统一补编号（幂等）并写回，
    避免读取时因 finding_id 必填缺号导致的崩溃。
    """
    if not Path(path).exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        dicts = json.load(f)
    findings = findings_from_dicts(dicts)
    if assign_missing_finding_ids(findings):
        save_findings(path, findings)
    return findings


def assign_missing_finding_ids(findings: list[Finding]) -> bool:
    """给 finding_id 缺失的 Finding 补上 F-001 起的编号；返回是否有改动。

    只对空/非法编号的卡片补号，已有合法编号的保持不变，可重复调用（幂等）。
    """
    max_id = 0
    for finding in findings:
        finding_id = str(finding.finding_id or "")
        if finding_id.startswith("F-"):
            try:
                max_id = max(max_id, int(finding_id[2:]))
            except ValueError:
                continue
    changed = False
    for finding in findings:
        finding_id = str(finding.finding_id or "")
        if not finding_id.startswith("F-"):
            max_id += 1
            finding.finding_id = f"F-{max_id:03d}"
            changed = True
    return changed


def next_finding_id(findings: list[Finding]) -> str:
    """根据已有 Finding 编号生成下一个编号；对空/非法编号的脏数据自动跳过。"""

    if not findings:
        return "F-001"
    max_id = 0
    for finding in findings:
        finding_id = str(finding.finding_id or "")
        if finding_id.startswith("F-"):
            try:
                max_id = max(max_id, int(finding_id[2:]))
            except ValueError:
                continue
    return f"F-{max_id + 1:03d}"
    

def create_finding_from_candidate(candidate: dict, next_id: str) -> Finding:
    """把规则引擎产生的候选风险 dict，包装成一张新的 Finding 卡片。"""

    copy_candidate = dict(candidate)
    copy_candidate["status"] = FindingStatus.NEEDS_HUMAN_REVIEW
    copy_candidate["finding_id"] = next_id
    finding =  Finding.from_dict(copy_candidate)
    return finding


def append_candidate(path: str, candidate: dict) -> Finding:
    """向 JSON 文件追加一条候选风险：自动编号、创建 Finding、保存并返回。"""

    findings = load_findings(path)
    next_id = next_finding_id(findings)
    finding = create_finding_from_candidate(candidate, next_id)
    findings.append(finding)
    save_findings(path, findings)
    return finding

def find_finding_by_id(findings: list[Finding], finding_id: str) -> Finding | None:
    """在 Finding 列表中按 finding_id 查找卡片；找不到返回 None。"""

    if len(findings) == 0:
        return None
    for finding in findings:
        if finding.finding_id == finding_id:
            return finding
    return None

def review_finding_by_id(path: str, finding_id: str, decision: str, note: str) -> Finding:
    """文件级人工复核入口：按 ID 找到卡片，并标记为 confirmed 或 false_positive。"""

    findings = load_findings(path)
    finding = find_finding_by_id(findings, finding_id)
    if finding is None:
        raise ValueError(f"finding with id {finding_id} not found")
    if decision not in {"confirmed", "false_positive"}:
        raise ValueError(f"invalid decision {decision}")
    if decision == "confirmed":
        finding.mark_confirmed(note)
    elif decision == "false_positive":
        finding.mark_false_positive(note)
    save_findings(path, findings)
    return finding


def mark_finding_waiting_fix_by_id(path: str, finding_id: str, fix_version: str) -> Finding:
    """文件级待修复入口：按 ID 找到卡片，标记/更新为 waiting_fix 并保存。"""

    findings = load_findings(path)
    finding = find_finding_by_id(findings, finding_id)
    if finding is None:
        raise ValueError(f"finding with id {finding_id} not found")
    finding.mark_waiting_fix(fix_version)
    save_findings(path, findings)
    return finding


def mark_finding_retested_by_id(path: str, finding_id: str, retest_run_id: str, passed: bool, note: str) -> Finding:

    findings = load_findings(path)
    finding = find_finding_by_id(findings, finding_id)
    if finding is None:
        raise ValueError(f"finding with id {finding_id} not found")
    if passed:
        finding.mark_retest_passed(retest_run_id,note)
    else:
        finding.mark_retest_failed(retest_run_id,note)
    save_findings(path, findings)
    return finding


def close_finding_by_id(path: str, finding_id: str, note: str) -> Finding:

    findings = load_findings(path)
    finding = find_finding_by_id(findings, finding_id)
    if finding is None:
        raise ValueError(f"finding with id {finding_id} not found")
    finding.mark_closed(note)
    save_findings(path, findings)
    return finding


def create_finding_from_execution_result(path: str, dict_result: dict) -> Finding:
    candidate = {}
    candidate["actor_role"] = dict_result["role"]
    candidate["endpoint"] = f"{dict_result['method']} {dict_result['path']}"
    candidate["expected_result"] = dict_result["expected"]
    candidate["actual_status_code"] = dict_result["status_code"]
    candidate["actual_response_summary"] = dict_result["body_preview"]
    failures = dict_result.get("failures") or []
    if failures:
        candidate["assertion_reason"] = failures[0]
    else:
        verdict = dict_result["assertion"].get("verdict", "")
        confidence = dict_result["assertion"].get("confidence", "")
        evidence = dict_result["assertion"].get("evidence") or []
        candidate["assertion_reason"] = f"{verdict}（置信度: {confidence}，依据: {'; '.join(evidence)}）"
    return append_candidate(path, candidate)


def create_findings_from_execution_results(path: str, results: list[dict]) -> list[Finding]:
    created_findings=[]
    for result in results:
        if result["status"]=="FAIL" and result["section"]=="越权":
            created_findings.append(create_finding_from_execution_result(path, result))

    return created_findings

