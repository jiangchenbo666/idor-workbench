from idor_workbench.domains.idor.findings import (
    Finding,
    FindingStatus,
    findings_to_dicts,
    findings_from_dicts,
    save_findings,
    load_findings,
    next_finding_id,
    append_candidate,
    create_finding_from_candidate,
    find_finding_by_id,
    review_finding_by_id,
    mark_finding_waiting_fix_by_id,
    mark_finding_retested_by_id,
    close_finding_by_id,
    create_finding_from_execution_result,
    create_findings_from_execution_results,
)
import json
import pytest



def make_finding():
    """测试用工厂函数：快速创建一张默认的、等待人工复核的 Finding。"""

    finding = Finding(
        finding_id="F-001",
        status=FindingStatus.NEEDS_HUMAN_REVIEW,
        actor_role="normal_user",
        endpoint="GET /api/admin/users",
        expected_result="普通用户不应该访问管理员用户列表",
        actual_status_code=200,
        actual_response_summary="返回了用户列表数据",
        assertion_reason="期望拒绝访问，但实际请求成功",
    )
    return finding





def test_create_finding():
    """验证 Finding 可以被正确创建，并且核心字段初始化正确。"""

    finding = make_finding()
    assert finding.finding_id == "F-001"
    assert finding.status == FindingStatus.NEEDS_HUMAN_REVIEW
    assert finding.actual_status_code == 200
    assert FindingStatus.RETEST_PASSED == "retest_passed"




def test_default_human_review_fields_are_empty():
    """验证新建 Finding 默认还没有人工复核结论和人工备注。"""

    finding = make_finding()
    assert finding.human_label is None
    assert finding.human_note is None


def test_finding_status_has_retest_passed():
    """验证状态枚举里存在 retest_passed，且它的字符串值正确。"""

    assert FindingStatus.RETEST_PASSED == "retest_passed"



def test_mark_finding_as_false_positive():
    """验证等待复核的 Finding 可以被人工标记为误报。"""

    finding = make_finding()
    finding.mark_false_positive("该接口允许普通用户查看公开用户列表")

    assert finding.status == FindingStatus.FALSE_POSITIVE
    assert finding.human_label =="false_positive"
    assert finding.human_note == "该接口允许普通用户查看公开用户列表"



def test_mark_finding_as_confirmed():
    """验证等待复核的 Finding 可以被人工标记为真漏洞。"""

    finding = make_finding()
    finding.mark_confirmed("普通用户成功访问了管理员用户列表")

    assert finding.status == FindingStatus.CONFIRMED
    assert finding.human_label =="confirmed"
    assert finding.human_note == "普通用户成功访问了管理员用户列表"



def test_cannot_confirm_false_positive_finding():
    """验证已经标记为误报的 Finding，不能再次改成真漏洞。"""

    finding = make_finding()

    finding.mark_false_positive("该接口允许普通用户查看公开用户列表")

    try:
        finding.mark_confirmed("后来又想改成真漏洞")
    except ValueError as error:
        assert str(error) == "only findings awaiting human review can be reviewed"
    else:
        assert False


def test_mark_finding_as_waiting_fix():
    """验证真漏洞 confirmed 可以进入 waiting_fix，并记录修复版本。"""

    finding = make_finding()
    finding.mark_confirmed("普通用户成功访问了管理员用户列表")
    finding.mark_waiting_fix("1.0.0")

    assert finding.status == FindingStatus.WAITING_FIX
    assert finding.fix_version == "1.0.0"

def test_cannot_mark_unconfirmed_finding_as_waiting_fix():
    """验证未经人工确认的 Finding，不能直接进入 waiting_fix。"""

    finding = make_finding()

    try:
        finding.mark_waiting_fix("1.0.0")
    except ValueError as error:
        assert str(error) == "only confirmed findings can be marked as waiting fix"
    else:
        assert False


def test_mark_finding_as_retest_passed():
    """验证 waiting_fix 状态的 Finding 复测通过后，可以进入 retest_passed。"""

    finding = make_finding()
    finding.mark_confirmed("普通用户成功访问了管理员用户列表")
    finding.mark_waiting_fix("1.0.0")
    finding.mark_retest_passed("R-001", "2026/9/2复测通过")

    assert finding.status == FindingStatus.RETEST_PASSED
    assert finding.retest_run_id == "R-001"
    assert finding.retest_note == "2026/9/2复测通过"

def test_cannot_mark_unwaiting_fix_finding_as_retest_passed():
    """验证没有进入 waiting_fix 的 Finding，不能直接标记复测通过。"""

    finding = make_finding()

    try:
        finding.mark_retest_passed("R-001", "2026/9/2复测通过")
    except ValueError as error:
        assert str(error) == "only findings awaiting fix can be marked as retest passed"
    else:
        assert False


def test_close_retest_passed_finding():
    """验证复测通过后的 Finding 可以被关闭。"""

    finding = make_finding()
    finding.mark_confirmed("普通用户成功访问了管理员用户列表")
    finding.mark_waiting_fix("1.0.0")
    finding.mark_retest_passed("R-001", "2026/9/2复测通过")
    finding.mark_closed("2026/9/2风险关闭")

    assert finding.status == FindingStatus.CLOSED
    assert finding.close_note == "2026/9/2风险关闭"

def test_close_false_positive_finding():
    """验证人工确认误报后的 Finding 可以被关闭。"""

    finding = make_finding()
    finding.mark_false_positive("该接口允许普通用户查看公开用户列表")
    finding.mark_closed("2026/9/2风险关闭")
    assert finding.status == FindingStatus.CLOSED
    assert finding.close_note == "2026/9/2风险关闭"
    


def test_cannot_close_finding_before_review():
    """验证未复核、未复测通过的 Finding 不能直接关闭。"""

    finding = make_finding()

    try:
        finding.mark_closed("2026/9/2风险关闭")
    except ValueError as error:
        assert str(error) == "only findings awaiting retest passed or false positive can be marked as closed"
    else:
        assert False


def test_finding_to_dict():
    """验证 Finding 对象可以转成 dict，并且 status 会变成字符串。"""

    finding = make_finding()
    dict = finding.to_dict()
    assert dict["status"] == "needs_human_review"   
    assert dict["finding_id"] == "F-001"
    assert dict["actor_role"] == "normal_user"


def test_finding_from_dict():
    """验证 dict 可以恢复成 Finding 对象，并且 status 会变回 FindingStatus。"""

    dict = make_finding().to_dict()
    finding = Finding.from_dict(dict)
    assert finding.status == FindingStatus.NEEDS_HUMAN_REVIEW
    assert finding.finding_id == "F-001"
    assert finding.actor_role == "normal_user"


def test_findings_to_dicts():
    """验证多张 Finding 可以批量转换成 dict 列表。"""

    findinga = make_finding()
    findingb = make_finding()
    findinga.finding_id="F-001"
    findingb.finding_id="F-002"
    dicts = findings_to_dicts([findinga,findingb])
    assert len(dicts) == 2
    assert dicts[0]["finding_id"] == "F-001"
    assert dicts[1]["finding_id"] == "F-002"
    assert dicts[0]["status"] == "needs_human_review"


def test_findings_from_dicts():
    """验证 dict 列表可以批量恢复成 Finding 对象列表。"""

    findinga = make_finding()
    findingb = make_finding()
    findinga.finding_id="F-001"
    findingb.finding_id="F-002"
    dicts = findings_to_dicts([findinga,findingb])
    findings = findings_from_dicts(dicts)
    assert len(findings) == 2
    assert findings[0] == findinga
    assert findings[0].status == FindingStatus.NEEDS_HUMAN_REVIEW


def test_save_and_load_findings(tmp_path):
    """验证 Finding 列表可以保存到 JSON 文件，并能完整读取回来。"""

    path = tmp_path / "findings.json"
    findinga = make_finding()
    findingb = make_finding()
    findinga.finding_id="F-001"
    findingb.finding_id="F-002"
    save_findings(path,[findinga,findingb])
    loaded_findings = load_findings(path)
    assert len(loaded_findings) == 2
    assert loaded_findings[0] == findinga
    assert loaded_findings[0].status == FindingStatus.NEEDS_HUMAN_REVIEW
    assert loaded_findings[0].finding_id == "F-001"


def test_load_findings_returns_empty_list_when_file_missing(tmp_path):
    """验证读取不存在的 findings.json 时，返回空列表而不是报错。"""

    path = tmp_path / "findings.json"
    loaded_findings = load_findings(path)
    assert len(loaded_findings) == 0
    assert loaded_findings == []
    findinga = make_finding()
    save_findings(path,[findinga])
    loaded_findingsa = load_findings(path)
    assert len(loaded_findingsa) == 1


def test_next_findings_returns_f_001_for_empty_list():
    """验证没有任何历史 Finding 时，下一个编号从 F-001 开始。"""

    findings = []
    next_finding_id_result = next_finding_id(findings)
    assert next_finding_id_result == "F-001"

def test_next_findings_returns_next_id_after_existing_findings():
    """验证已有 Finding 时，下一个编号会在最大编号基础上加 1。"""

    findinga = make_finding()
    findingb = make_finding()
    findingc = make_finding()
    findinga.finding_id = "F-001"
    findingb.finding_id = "F-002"
    findingc.finding_id = "F-003"
    findings = [findinga, findingb, findingc]
    max_id = 0
    for finding in findings:
        temp_id=int(finding.finding_id[2:])
        max_id = max(max_id, temp_id)
    next_finding_id_result = next_finding_id(findings)
    next_finding_id_result_int = int(next_finding_id_result[2:])
    assert next_finding_id_result_int == max_id+1


def test_create_finding_from_candidate():
    """验证候选风险 dict 可以被包装成新的 Finding，且不污染原始 candidate。"""

    candidate = {
    "actor_role": "normal_user",
    "endpoint": "GET /api/admin/users",
    "expected_result": "普通用户不应该访问管理员用户列表",
    "actual_status_code": 200,
    "actual_response_summary": "返回了用户列表数据",
    "assertion_reason": "期望拒绝访问，但实际请求成功",
    }
    findings = []
    finding = create_finding_from_candidate(candidate, next_finding_id(findings))
    assert isinstance(finding,Finding)
    assert finding.status == FindingStatus.NEEDS_HUMAN_REVIEW
    assert finding.finding_id == next_finding_id(findings)
    assert finding.actor_role == candidate["actor_role"]
    assert "status" not in candidate
    assert "finding_id" not in candidate



def test_append_candidate_creates_first_finding(tmp_path):
    """验证向空 JSON 文件追加第一条 candidate 时，会生成 F-001。"""

    path = tmp_path / "findings.json"
    candidate = {
    "actor_role": "normal_user",
    "endpoint": "GET /api/admin/users",
    "expected_result": "普通用户不应该访问管理员用户列表",
    "actual_status_code": 200,
    "actual_response_summary": "返回了用户列表数据",
    "assertion_reason": "期望拒绝访问，但实际请求成功",
    }
    finding = append_candidate(path,candidate)
    assert isinstance(finding,Finding)
    assert finding.status == FindingStatus.NEEDS_HUMAN_REVIEW
    assert finding.actor_role == candidate["actor_role"]
    assert "status" not in candidate
    assert "finding_id" not in candidate

def test_append_candidate_use_next_id(tmp_path):
    """验证连续追加 candidate 时，第二张卡片会自动生成 F-002。"""

    path = tmp_path / "findings.json"
    candidatea = {
    "actor_role": "normal_user",
    "endpoint": "GET /api/admin/users",
    "expected_result": "普通用户不应该访问管理员用户列表",
    "actual_status_code": 200,
    "actual_response_summary": "返回了用户列表数据",
    "assertion_reason": "期望拒绝访问，但实际请求成功",
    }
    candidateb = {
        "actor_role": "normal_user",
        "endpoint": "GET /api/admin/users/login",
        "expected_result": "普通用户不应该访问管理员用户列表",
        "actual_status_code": 200,
        "actual_response_summary": "返回了用户列表数据",
        "assertion_reason": "期望拒绝访问，但实际请求成功",
        }
    findinga = append_candidate(path,candidatea)
    findingb = append_candidate(path,candidateb)
    with open(path, "r", encoding="utf-8") as f:
        dicts = json.load(f)
        findings = findings_from_dicts(dicts)
    
    assert len(findings) == 2
    assert findings[1].finding_id == "F-002"


def test_find_finding_by_id_returns_matching_finding():
    """验证可以按 finding_id 从列表中找到目标 Finding，找不到时返回 None。"""

    findinga = make_finding()
    findinga.finding_id = "F-001"
    findingb = make_finding()
    findingb.finding_id = "F-002"
    findings = [findinga, findingb]
    found_finding = find_finding_by_id(findings, "F-002")
    assert found_finding == findingb
    assert found_finding.status == FindingStatus.NEEDS_HUMAN_REVIEW
    assert find_finding_by_id(findings, "F-999") == None


def test_review_finding_by_id_marks_confirmed_and_false_positive(tmp_path):
    """验证文件级人工复核入口可以把不同 Finding 标记为真漏洞或误报。"""

    path = tmp_path / "findings.json"
    findinga = make_finding()
    findinga.finding_id = "F-001"
    findingb = make_finding()
    findingb.finding_id = "F-002"
    findings = [findinga, findingb]
    save_findings(path,findings)
    found_findinga = review_finding_by_id(path,"F-001","confirmed","确认这是一个确认的漏洞")
    found_findingb = review_finding_by_id(path,"F-002","false_positive","确认这是工具误报")
    assert found_findinga.status == FindingStatus.CONFIRMED
    assert found_findingb.status == FindingStatus.FALSE_POSITIVE
    assert found_findinga.human_note == "确认这是一个确认的漏洞"
    assert found_findingb.human_note == "确认这是工具误报"


def test_review_finding_by_id_raises_when_finding_not_found(tmp_path):
    """验证人工复核不存在的 finding_id 时，会抛出 ValueError。"""

    path = tmp_path / "findings.json"
    findinga = make_finding()
    findinga.finding_id = "F-001"
    findingb = make_finding()
    findingb.finding_id = "F-002"
    findings = [findinga, findingb]
    save_findings(path,findings)
    with pytest.raises(ValueError):
        review_finding_by_id(path,"F-999","confirmed","确认这是一个确认的漏洞")


def test_review_finding_by_id_raises_when_decision_invalid(tmp_path):
    """验证人工复核 decision 不合法时，会抛出 ValueError。"""

    path = tmp_path / "findings.json"
    findinga = make_finding()
    findinga.finding_id = "F-001"
    findingb = make_finding()
    findingb.finding_id = "F-002"
    findings = [findinga, findingb]
    save_findings(path,findings)
    with pytest.raises(ValueError):
        review_finding_by_id(path,"F-001","wrong_decision","确认这是一个确认的漏洞")


def test_mark_confirmed_finding_waiting_fix_by_id(tmp_path):
    """验证文件级入口可以把 confirmed Finding 标记为 waiting_fix。"""

    path = tmp_path / "findings.json"
    findinga = make_finding()
    findinga.finding_id = "F-001"
    findinga.status = FindingStatus.CONFIRMED
    findingb = make_finding()
    findingb.finding_id = "F-002"
    findings = [findinga, findingb]
    save_findings(path,findings)
    found_findinga = mark_finding_waiting_fix_by_id(path,"F-001","1.0.1")
    assert found_findinga.status == FindingStatus.WAITING_FIX
    assert found_findinga.fix_version == "1.0.1"
    with pytest.raises(ValueError):
        mark_finding_waiting_fix_by_id(path,"F-002","1.0.1")


def test_mark_finding_by_id_raises_when_finding_not_found(tmp_path):
    """验证标记 waiting_fix 时，如果 finding_id 不存在，会抛出 ValueError。"""

    path = tmp_path / "findings.json"
    findinga = make_finding()
    findinga.finding_id = "F-001"
    findingb = make_finding()
    findingb.finding_id = "F-002"
    findings = [findinga, findingb]
    save_findings(path,findings)
    with pytest.raises(ValueError):
        mark_finding_waiting_fix_by_id(path,"F-999","1.0.1")


def test_already_mark_waiting_fix_finding_can_be_marked_again_by_id(tmp_path):
    """验证复测失败后，waiting_fix Finding 可以再次更新新的修复版本。"""

    path = tmp_path / "findings.json"
    findinga = make_finding()
    findinga.finding_id = "F-001"
    findinga.mark_confirmed("确认这是一个确认的漏洞")
    findinga.mark_waiting_fix("1.0.1")
    findinga.mark_retest_failed("R-001","1.0.1 版本复测失败，仍然越权")
    findings = [findinga]
    save_findings(path,findings)
    final_findinga=mark_finding_waiting_fix_by_id(path,"F-001","1.0.2")
    assert final_findinga.status == FindingStatus.WAITING_FIX
    assert final_findinga.fix_version == "1.0.2"


def test_mark_finding_retested_by_id_marks_waiting_fix_as_retest_passed(tmp_path):
    path = tmp_path / "findings.json"
    findinga = make_finding()
    findinga.finding_id = "F-001"
    findinga.mark_confirmed("确认这是一个确认的漏洞")
    findinga.mark_waiting_fix("1.0.1")
    findings = [findinga]
    save_findings(path,findings)
    final_findinga = mark_finding_retested_by_id(path,"F-001","1.0.2",True,"1.0.2版本复测通过")
    assert final_findinga.status == FindingStatus.RETEST_PASSED


def test_mark_finding_retested_by_id_keeps_waiting_fix_when_retest_failed(tmp_path):
    path = tmp_path / "findings.json"
    findinga = make_finding()
    findinga.finding_id = "F-001"
    findinga.mark_confirmed("确认这是一个确认的漏洞")
    findinga.mark_waiting_fix("1.0.1")
    findings = [findinga]
    save_findings(path,findings)
    mark_finding_waiting_fix_by_id(path, "F-001", "1.0.2")
    final_findinga = mark_finding_retested_by_id(path,"F-001","R-002",False,"1.0.2版本复测失败")
    assert final_findinga.status == FindingStatus.WAITING_FIX
    assert final_findinga.fix_version == "1.0.2"


def test_mark_finding_retested_by_id_raises_when_finding_not_found(tmp_path):
    path = tmp_path / "findings.json"
    findinga = make_finding()
    findinga.finding_id = "F-001"
    findingb = make_finding()
    findingb.finding_id = "F-002"
    findinga.mark_confirmed("确认这是一个确认的漏洞")
    findinga.mark_waiting_fix("1.0.1")
    findingb.mark_confirmed("确认这是一个确认的漏洞")
    findings = [findinga, findingb]
    save_findings(path,findings)
    with pytest.raises(ValueError):
        mark_finding_retested_by_id(path,"F-999","1.0.2",True,"1.0.2版本复测通过")



def test_close_finding_by_id_closes_retest_passed_finding(tmp_path):
    path = tmp_path / "findings.json"
    findinga = make_finding()
    findinga.finding_id = "F-001"
    findinga.mark_confirmed("确认这是一个确认的漏洞")
    findinga.mark_waiting_fix("1.0.1")
    findinga.mark_retest_passed("R-001","1.0.2版本复测通过")
    findings = [findinga]   
    save_findings(path,findings)
    final_findinga = close_finding_by_id(path,"F-001","复核通过，关闭漏洞")
    assert final_findinga.status == FindingStatus.CLOSED
    assert final_findinga.close_note == "复核通过，关闭漏洞"


def test_close_finding_by_id_closes_false_positive_finding(tmp_path):
    path = tmp_path / "findings.json"
    findinga = make_finding()
    findinga.finding_id = "F-001"
    findinga.mark_false_positive("确认这是一个确认的漏洞")
    findings = [findinga]   
    save_findings(path,findings)
    final_findinga = close_finding_by_id(path,"F-001","确认这是一个系统误报，关闭漏洞")
    assert final_findinga.status == FindingStatus.CLOSED
    assert final_findinga.close_note == "确认这是一个系统误报，关闭漏洞"


def test_close_finding_by_id_raises_when_finding_not_found(tmp_path):
    path = tmp_path / "findings.json"
    findinga = make_finding()
    findinga.finding_id = "F-001"
    findings = [findinga]   
    save_findings(path,findings)
    with pytest.raises(ValueError):
        close_finding_by_id(path,"F-999","确认这是一个系统误报，关闭漏洞")


def test_create_finding_from_execution_result(tmp_path):
    path = tmp_path / "findings.json"
    findings = []
    save_findings(path,findings)
    dict_result = {
    "role": "normal_user",
    "section": "越权",
    "status": "FAIL",
    "name": "管理员用户列表",
    "path": "/api/admin/users",
    "method": "GET",
    "req_body": None,
    "request": {...},
    "req_params": {},
    "req_headers": {},
    "authorization": "",
    "expected": "应拒绝访问",
    "assertion": {
        "expected": "应拒绝访问",
        "verdict": "越权疑似",
        "confidence": "高",
        "evidence": ["HTTP=200", "返回了业务数据"],
    },
    "status_code": 200,
    "body_preview": "返回了用户列表数据",
    "response_body": "...完整响应体...",
    "response_headers": {},
    "failures": [
        "越权疑似（置信度: 高，依据: HTTP=200; 返回了业务数据）"
    ],
    }
    assertion = dict_result["assertion"]
    verdict = assertion.get("verdict", "")
    confidence = assertion.get("confidence", "")
    evidence = assertion.get("evidence") or []
    assertion_reason = f"{verdict}（置信度: {confidence}，依据: {'; '.join(evidence)}）"
    finding_candidate = create_finding_from_execution_result(path, dict_result)
    assert finding_candidate.actor_role == dict_result["role"]
    assert finding_candidate.endpoint == f"{dict_result['method']} {dict_result['path']}"
    assert finding_candidate.expected_result == dict_result["expected"] 
    assert finding_candidate.assertion_reason == dict_result["failures"][0]
    assert finding_candidate.actual_status_code == dict_result["status_code"]
    assert finding_candidate.actual_response_summary == dict_result["body_preview"]


def test_create_finding_from_execution_result_builds_reason_from_assertion_when_failures_empty(tmp_path):
    path = tmp_path / "findings.json"
    findings = []
    save_findings(path,findings)
    dict_result = {
        "role": "normal_user",
        "section": "越权",
        "status": "FAIL",
        "name": "管理员用户列表",
        "path": "/api/admin/users",
        "method": "GET",
        "req_body": None,
        "request": {...},
        "req_params": {},
        "req_headers": {},
        "authorization": "",
        "expected": "应拒绝访问",
        "assertion": {
            "expected": "应拒绝访问",
            "verdict": "越权疑似",
            "confidence": "高",
            "evidence": ["HTTP=200", "返回了业务数据"],
        },
        "status_code": 200,
        "body_preview": "返回了用户列表数据",
        "response_body": "...完整响应体...",
        "response_headers": {},
        "failures": [],
        }
    assertion = dict_result["assertion"]
    verdict = assertion.get("verdict", "")
    confidence = assertion.get("confidence", "")
    evidence = assertion.get("evidence") or []
    assertion_reason = f"{verdict}（置信度: {confidence}，依据: {'; '.join(evidence)}）"
    finding_candidate = create_finding_from_execution_result(path, dict_result)
    assert finding_candidate.assertion_reason == assertion_reason


def test_create_finding_from_execution_result_appends_finding_to_file(tmp_path):
    path = tmp_path / "findings.json"
    findings = []
    save_findings(path,findings)
    dict_result = {
        "role": "normal_user",
        "section": "越权",
        "status": "FAIL",
        "name": "管理员用户列表",
        "path": "/api/admin/users",
        "method": "GET",
        "req_body": None,
        "request": {...},
        "req_params": {},
        "req_headers": {},
        "authorization": "",
        "expected": "应拒绝访问",
        "assertion": {
            "expected": "应拒绝访问",
            "verdict": "越权疑似",
            "confidence": "高",
            "evidence": ["HTTP=200", "返回了业务数据"],
        },
        "status_code": 200,
        "body_preview": "返回了用户列表数据",
        "response_body": "...完整响应体...",
        "response_headers": {},
        "failures": [],
        }
    create_finding_from_execution_result(path, dict_result)
    loaded_findings = load_findings(path)
    assert len(loaded_findings) == 1
    assert loaded_findings[0].finding_id == "F-001"
    assert loaded_findings[0].status == FindingStatus.NEEDS_HUMAN_REVIEW
    assert loaded_findings[0].endpoint == "GET /api/admin/users"



def test_create_findings_from_execution_results_only_appends_failed_idor_rows(tmp_path):
    path = tmp_path / "findings.json"
    findings = []
    save_findings(path,findings)
    dict_resulta = {
            "role": "normal_user",
            "section": "越权",
            "status": "FAIL",
            "name": "管理员用户列表",
            "path": "/api/admin/users",
            "method": "GET",
            "req_body": None,
            "request": {...},
            "req_params": {},
            "req_headers": {},
            "authorization": "",
            "expected": "应拒绝访问",
            "assertion": {
                "expected": "应拒绝访问",
                "verdict": "越权疑似",
                "confidence": "高",
                "evidence": ["HTTP=200", "返回了业务数据"],
            },
            "status_code": 200,
            "body_preview": "返回了用户列表数据",
            "response_body": "...完整响应体...",
            "response_headers": {},
            "failures": [],
            }
    dict_resultb=dict_resulta.copy()
    dict_resultb["status"]="PASS"
    dict_resultc=dict_resulta.copy()
    dict_resultc["section"]="自身权限"
    dict_resultc["status"]="FAIL"
    results = []
    results.append( dict_resulta)
    results.append( dict_resultb)
    results.append( dict_resultc)
    data={}
    data["results"]=results
    created_findings = create_findings_from_execution_results(path, results)
    assert len(created_findings) == 1
    loaded_findings = load_findings(path)
    assert len(loaded_findings) == 1
    assert loaded_findings[0].finding_id == "F-001"
    assert loaded_findings[0].status == FindingStatus.NEEDS_HUMAN_REVIEW
    assert loaded_findings[0].endpoint == "GET /api/admin/users"

    