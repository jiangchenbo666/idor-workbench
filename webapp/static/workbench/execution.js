function renderRunSummary(summary = {}, returncode = null, hasReport = false) {
  const failed = Number(summary.failed || 0);
  const total = Number(summary.total || 0);
  const frontend = (summary || {}).frontend || {};
  const frontendSummary = frontend.summary || {};
  const runError = (summary.run_error || "").trim();
  const loginErrors = Array.isArray(summary.login_errors) ? summary.login_errors : [];
  const hasRunFailure = Boolean(runError || (returncode !== null && Number(returncode) !== 0));
  state.runSummary = {...(summary || {}), returncode};
  state.hasReport = hasReport;
  state.aiRunInsights = null;
  state.assertionProfile = summary.assertion_profile || {};
  state.frontendResults = frontend.results || [];
  state.scenarioResults = summary.scenario || {};
  if (summary.time || returncode !== null) {
    state.runFinishedAt = summary.time || formatNow();
  }
  $("runNotice").className = `notice ${hasRunFailure ? "warn" : (returncode === 0 ? (failed ? "" : "ok") : "")}`;
  $("runNotice").textContent = hasRunFailure
    ? buildRunFailureMessage(summary, returncode)
    : returncode === null
    ? "已加载历史结果。"
    : `执行完成：returncode=${returncode}。结果已进入下方卡片，可直接查询，也可以按需下载。`;
  $("runSummary").innerHTML = [
    {label: "实际执行", value: total, view: "api", status: ""},
    {label: "通过", value: summary.passed || 0, view: "api", status: "PASS"},
    {label: "失败", value: summary.failed || 0, view: "api", status: "FAIL"},
    {label: "跳过", value: summary.skipped || 0, view: "api", status: "SKIP"},
    {label: "角色数", value: (summary.roles || []).length, view: "roleLog", status: ""},
    {label: "报告", value: hasReport ? "已生成" : "未生成", view: "artifacts", status: ""},
  ].map(item => `
    <button type="button" class="stat runStat" data-result-view="${item.view}" data-status-filter="${item.status}">
      <span>${item.label}</span>
      <strong>${item.value}</strong>
    </button>
  `).join("");
  document.querySelectorAll(".runStat").forEach(card => {
    card.onclick = () => {
      if (card.dataset.resultView === "api") {
        $("apiStatusFilter").value = card.dataset.statusFilter || "";
        state.runPage = 1;
        renderRunPage();
      }
      setResultView(card.dataset.resultView || "api", true);
    };
  });

  const roleStats = summary.role_stats || {};
  $("roleStats").innerHTML = hasRunFailure
    ? renderRunDiagnostics(summary, returncode)
    : Object.entries(roleStats).map(([role, s]) => `
    <div class="roleStat">
      <strong>${role}</strong>
      <div>总计 ${s.total} ｜ <span class="statusPass">PASS ${s.pass}</span> ｜ <span class="statusFail">FAIL ${s.fail}</span> ｜ <span class="statusError">ERROR ${s.error}</span></div>
    </div>
  `).join("");

  state.runResults = summary.results || [];
  if ($("aiRunStatus")) $("aiRunStatus").textContent = "";
  if ($("aiRunInsights")) {
    $("aiRunInsights").hidden = true;
    $("aiRunInsights").innerHTML = "";
  }
  if ($("openAiRunReport")) $("openAiRunReport").disabled = true;
  state.runPage = 1;
  renderResultCards();
  renderRunPage();
  renderPrettyRunLog(summary);
  renderScenarioResults(state.scenarioResults);
  renderFrontendResults(frontend);
  renderAssertionProfile(state.assertionProfile);
  if (summary.ai_analysis && !hasRunFailure && total > 0) {
    state.aiRunExpanded = false;
    renderAiRunInsights(summary.ai_analysis);
    if ($("aiRunStatus")) $("aiRunStatus").textContent = "AI 自动分析已完成，已使用后端执行链路生成的审计结果。";
  } else if (hasRunFailure && $("aiRunStatus")) {
    $("aiRunStatus").textContent = "本次没有有效 API 执行结果，AI 审查只能说明计划/配置质量，不能代表真实运行成功。";
  }
  renderRunCompare();
  setResultView(state.activeResultView || "api");
  updateRunTimeLabels();
}

function buildRunFailureMessage(summary = {}, returncode = null) {
  const total = Number(summary.total || 0);
  if (total === 0) {
    return `API 测试未达到预期：实际执行 0 条，returncode=${returncode ?? "未知"}。请查看下方“原因诊断”和 last_run.log。`;
  }
  return `API 测试未完全成功：returncode=${returncode ?? "未知"}。请查看下方“原因诊断”和 last_run.log。`;
}

function buildRunFailureDetail(summary = {}, returncode = null) {
  const diagnostics = summary.diagnostics || {};
  if (Array.isArray(diagnostics.evidence) && diagnostics.evidence.length) {
    return diagnostics.evidence.join("；").slice(0, 500);
  }
  const explicit = (summary.run_error || "").trim();
  if (explicit) return explicit.slice(0, 500);
  const loginErrors = Array.isArray(summary.login_errors) ? summary.login_errors.filter(Boolean) : [];
  if (loginErrors.length) return `角色登录或 token 获取失败：${loginErrors.join("；").slice(0, 420)}`;
  if (Number(returncode) === 2) {
    return "后端没有产出任何接口执行行。请优先检查：角色账号是否完整、getToken URL/鉴权方式/token 提取路径是否正确、接口清单是否为空、被测环境是否可连通。";
  }
  return "后台任务结束但未返回可用结果，请查看 last_run.log 或执行历史中的错误详情。";
}

function renderRunDiagnostics(summary = {}, returncode = null) {
  const diagnostics = summary.diagnostics || {};
  const evidence = Array.isArray(diagnostics.evidence) ? diagnostics.evidence.filter(Boolean) : [];
  const suggestions = Array.isArray(diagnostics.suggestions) ? diagnostics.suggestions.filter(Boolean) : [];
  const title = diagnostics.title || "API 测试没有产生有效执行结果";
  const logHref = state.project_id ? `/api/projects/${state.project_id}/download/${diagnostics.log_file || "last_run.log"}` : "#";
  return `
    <div class="emptyScenario runDiagnostic">
      <strong>${escapeHtml(title)}</strong>
      <p>returncode=${escapeHtml(returncode ?? summary.returncode ?? "未知")}，实际执行 0 条。</p>
      ${evidence.length ? `<p>原因证据：</p><ul>${evidence.map(item => `<li>${escapeHtml(item)}</li>`).join("")}</ul>` : ""}
      ${suggestions.length ? `<p>修复建议：</p><ul>${suggestions.map(item => `<li>${escapeHtml(item)}</li>`).join("")}</ul>` : ""}
      <p><a href="${escapeHtml(logHref)}" download>下载 last_run.log 查看原始日志</a></p>
    </div>`;
}

function prepareRunStart(label = "API 测试") {
  state.runStartedAt = formatNow();
  state.runFinishedAt = "";
  state.runResults = [];
  state.runSummary = {};
  state.aiRunInsights = null;
  state.assertionProfile = {};
  state.frontendResults = [];
  state.scenarioResults = {};
  state.hasReport = false;
  state.runPage = 1;
  state.activeResultView = normalizeResultView(state.activeResultView);
  $("runSummary").innerHTML = "";
  $("roleStats").innerHTML = "";
  $("runRows").innerHTML = `<tr><td colspan="8">${label}执行中，请稍等...</td></tr>`;
  if ($("aiRunStatus")) $("aiRunStatus").textContent = "";
  if ($("aiRunInsights")) {
    $("aiRunInsights").hidden = true;
    $("aiRunInsights").innerHTML = "";
  }
  if ($("openAiRunReport")) $("openAiRunReport").disabled = true;
  updateManualReviewCount();
  $("prettyRunLog").innerHTML = `${label}执行中，旧日志已清空，完成后会按角色重新展示。`;
  $("scenarioRows").innerHTML = `<tr><td colspan="8">${label}执行中，业务场景结果将在完成后展示。</td></tr>`;
  $("frontendRows").innerHTML = `<tr><td colspan="7">等待新的执行结果。</td></tr>`;
  $("profileRows").innerHTML = `<tr><td colspan="3">等待新的断言策略。</td></tr>`;
  $("profileCards").innerHTML = "";
  renderResultCards();
  setResultView(state.activeResultView);
  updateRunTimeLabels();
}

function setRunActionBusy(busy, label = "") {
  state.isRunTaskActive = Boolean(busy);
  state.activeRunTaskLabel = busy ? label : "";
  ["runApi", "runFrontend", "runReport"].forEach(id => {
    const button = $(id);
    if (button) button.disabled = Boolean(busy);
  });
}

function blockConcurrentRun(label) {
  if (!state.isRunTaskActive) return false;
  const message = `${state.activeRunTaskLabel || "后台任务"}仍在执行中，请等待完成后再启动${label}。`;
  chat(message, "error");
  if ($("runNotice")) {
    $("runNotice").className = "notice warn";
    $("runNotice").textContent = message;
  }
  return true;
}

function setParallelRunBusy(kind, busy) {
  if (kind === "api") {
    state.apiRunActive = Boolean(busy);
    ["runApi", "runReport"].forEach(id => { if ($(id)) $(id).disabled = Boolean(busy); });
    return;
  }
  state.frontendRunActive = Boolean(busy);
  if ($("runFrontend")) $("runFrontend").disabled = Boolean(busy);
}

function renderResultCards() {
  const apiFailed = Number((state.runSummary || {}).failed || 0);
  const apiTotal = Number((state.runSummary || {}).total || 0);
  const apiBroken = Boolean((state.runSummary || {}).run_error || Number((state.runSummary || {}).returncode || 0) !== 0);
  const frontendData = (state.runSummary || {}).frontend || {};
  const frontend = frontendData.summary || {};
  const frontendTotal = Number(frontend.total || 0);
  const frontendProblem = Boolean(frontendData.note || frontendData.setup_error || frontend.failed || frontend.error);
  const scenario = ((state.runSummary || {}).scenario || {}).summary || {};
  const profileMode = (state.assertionProfile || {}).mode || "未生成";
  const runTime = state.runFinishedAt || state.runStartedAt || "尚未执行";
  const cards = [
    {key: "api", title: "API 结果", value: apiTotal, meta: `${apiBroken ? "未达预期" : `失败 ${apiFailed} 条`} ｜ ${runTime}`, status: apiBroken || apiFailed ? "warn" : "ok"},
    {key: "roleLog", title: "角色日志", value: (state.runSummary.roles || []).length || 0, meta: `按角色分组 ｜ ${runTime}`, status: apiTotal ? "ok" : "idle"},
    {key: "scenario", title: "业务场景", value: scenario.total || 0, meta: `跳过 ${scenario.skipped || 0} / 失败 ${scenario.failed || 0}`, status: (scenario.failed || 0) ? "warn" : ((scenario.total || 0) ? "ok" : "idle")},
    {key: "frontend", title: "前端拦截", value: frontendTotal, meta: frontendData.note || `${tr("frontend.failed", "失败")} ${frontend.failed || 0} / ${tr("frontend.error", "错误")} ${frontend.error || 0}`, status: frontendProblem ? "warn" : (frontendTotal ? "ok" : "idle")},
    {key: "profile", title: "断言策略", value: profileMode, meta: `自学习响应体 ｜ ${runTime}`, status: profileMode === "未生成" ? "idle" : "ok"},
    {key: "artifacts", title: "报告产物", value: state.hasReport ? "已生成" : "未生成", meta: `Word / JSON / 日志 ｜ ${runTime}`, status: state.hasReport ? "ok" : "idle"},
    {key: "history", title: "执行历史", value: state.runHistory.length || "-", meta: "复测记录 / 左右对比", status: state.runHistory.length ? "ok" : "idle"},
  ].filter(card => visibleResultViews().includes(card.key));
  state.activeResultView = normalizeResultView(state.activeResultView);
  $("resultCards").innerHTML = cards.map(card => `
    <button class="resultCard ${card.status} ${state.activeResultView === card.key ? "active" : ""}" type="button" data-view="${card.key}">
      <span>${card.title}</span>
      <strong>${escapeHtml(card.value)}</strong>
      <em>${card.meta}</em>
    </button>
  `).join("");
  document.querySelectorAll(".resultCard").forEach(btn => {
    btn.onclick = () => setResultView(btn.dataset.view, true);
  });
}

function setResultView(view, jump = false) {
  state.activeResultView = normalizeResultView(view);
  document.querySelectorAll(".resultCard").forEach(btn => btn.classList.toggle("active", btn.dataset.view === state.activeResultView));
  document.querySelectorAll(".resultPane").forEach(pane => pane.classList.toggle("active", pane.dataset.resultPane === state.activeResultView));
  if (jump) {
    const pane = document.querySelector(`.resultPane[data-result-pane="${state.activeResultView}"]`);
    if (pane) pane.scrollIntoView({behavior: "smooth", block: "start"});
  }
  if (state.activeResultView === "history") loadRunHistory().catch(err => chat(`加载执行历史失败：${err.message}`));
  updateRunTimeLabels();
}

async function loadLatestRunResult(hasReport = false) {
  await loadRunHistory();
  const latestApiRun = state.runHistory.find(item => Number(item.total || 0) > 0);
  if (!latestApiRun) return;
  const result = await api(`/api/projects/${state.project_id}/runs/${latestApiRun.run_id}`);
  renderRunSummary(result.run_summary || {}, null, hasReport);
}

function flattenScenarioSteps(scenario = {}) {
  const rows = [];
  for (const item of scenario.scenarios || []) {
    for (const step of item.steps || []) {
      rows.push({...step, scenario_name: item.name, scenario_status: item.status});
    }
  }
  return rows;
}

function renderScenarioResults(scenario = {}) {
  state.scenarioResults = scenario || state.scenarioResults || {};
  const summary = state.scenarioResults.summary || {};
  const rows = flattenScenarioSteps(state.scenarioResults);
  const query = ($("scenarioResultSearch")?.value || "").trim().toLowerCase();
  const statusFilter = $("scenarioStatusFilter")?.value || "";
  const filtered = rows.filter(r => {
    const text = [
      r.scenario_name, r.name, r.purpose, r.role, r.method, r.path, r.status, r.reason,
      JSON.stringify(r.extracts || {}), r.response_preview,
    ].join(" ").toLowerCase();
    return (!query || text.includes(query)) && (!statusFilter || r.status === statusFilter);
  });
  $("scenarioSummary").innerHTML = [
    ["场景步骤", summary.total || 0],
    ["通过", summary.passed || 0],
    ["跳过", summary.skipped || 0],
    ["失败", summary.failed || 0],
  ].map(([label, value]) => `<div class="stat"><span>${label}</span><strong>${value}</strong></div>`).join("");

  const loginErrors = state.scenarioResults.login_errors || [];
  if (!rows.length && loginErrors.length) {
    $("scenarioRows").innerHTML = `<tr><td colspan="8">${escapeHtml(loginErrors.join("；"))}</td></tr>`;
    return;
  }
  $("scenarioRows").innerHTML = filtered.map(r => {
    const cls = r.status === "PASS" ? "statusPass" : (r.status === "SKIPPED" ? "statusError" : "statusFail");
    const extracts = Object.entries(r.extracts || {}).map(([key, value]) => `${key}=${value}`).join("\n");
    return `
      <tr>
        <td>${escapeHtml(r.scenario_name || "")}</td>
        <td>${escapeHtml(r.purpose || "")}</td>
        <td>${escapeHtml(r.role || "")}</td>
        <td class="${cls}">${escapeHtml(r.status || "")}</td>
        <td><div class="runIface"><div class="runPath">${escapeHtml(r.method || "")} ${escapeHtml(r.path || "")}</div><div class="runName">${escapeHtml(r.name || "")}</div></div></td>
        <td>${escapeHtml(r.http ?? "")}</td>
        <td><pre class="inlinePre">${escapeHtml(extracts || "")}</pre></td>
        <td><div class="runFailure">${escapeHtml(r.reason || r.response_preview || "")}</div></td>
      </tr>`;
  }).join("") || `<tr><td colspan="8">暂无业务场景结果。请先在 Pro 的账号鉴权页编排场景，再执行 API 测试。</td></tr>`;
}

function renderFrontendResults(frontend = {}) {
  const diagnostics = frontend.diagnostics || {};
  state.frontendResults = frontend.results || state.frontendResults || [];
  const query = ($("frontendResultSearch")?.value || "").trim().toLowerCase();
  const statusFilter = $("frontendStatusFilter")?.value || "";
  const results = state.frontendResults.filter(r => {
    const text = [r.role, r.status, r.name, r.page_url, r.full_url, r.final_url, r.conclusion, r.confidence, ...(r.signals || [])].join(" ").toLowerCase();
    return (!query || text.includes(query)) && (!statusFilter || r.status === statusFilter);
  });
  $("frontendSummary").innerHTML = [
    ["检测数", frontend.summary?.total ?? state.frontendResults.length],
    ["通过", frontend.summary?.passed ?? 0],
    ["失败", frontend.summary?.failed ?? 0],
    ["错误", frontend.summary?.error ?? 0],
    ["页面", diagnostics.page_url_count ?? "-"],
    ["计划", diagnostics.planned_checks ?? "-"],
  ].map(([k, v]) => `<div><span>${escapeHtml(k)}</span><strong>${escapeHtml(v)}</strong></div>`).join("");
  const box = $("frontendRows");
  if (frontend.setup_error || frontend.note) {
    box.innerHTML = `<div class="emptyScenario">${escapeHtml(frontend.setup_error || frontend.note)}</div>`;
    return;
  }
  box.innerHTML = results.map(r => {
    const cls = r.status === "PASS" ? "statusPass" : (r.status === "ERROR" ? "statusError" : "statusFail");
    const equivalentRoles = r.equivalent_roles || [];
    const roleLabel = equivalentRoles.length > 1 ? `${r.role || ""}（代表）` : (r.role || "");
    const roleTitle = equivalentRoles.length > 1 ? `同权限合并：${equivalentRoles.join("、")}` : "";
    const screenshotUrl = frontendScreenshotUrl(r.screenshot);
    const signals = (r.signals || []).filter(Boolean);
    return `
      <article class="frontendResultCard">
        <div class="frontendResultMain">
          <div class="frontendResultHead">
            <span title="${escapeHtml(roleTitle)}">${escapeHtml(roleLabel)}</span>
            <strong class="${cls}">${escapeHtml(r.status || "")}</strong>
            <em>HTTP ${escapeHtml(r.main_status ?? r.http ?? "-")}</em>
          </div>
          <div class="frontendRoute">
            <strong>${escapeHtml(r.page_url || "")}</strong>
            <span>${escapeHtml(r.final_url || r.full_url || r.name || "")}</span>
          </div>
          <div class="frontendConclusion">
            <strong>${escapeHtml(r.conclusion || "")}</strong>
            ${r.confidence ? `<span>置信度：${escapeHtml(r.confidence)}</span>` : ""}
            ${r.access_policy ? `<span>${escapeHtml(r.access_policy)}</span>` : ""}
          </div>
          ${signals.length ? `<div class="frontendSignals">${signals.map(item => `<span>${escapeHtml(item)}</span>`).join("")}</div>` : ""}
        </div>
        <div class="frontendShot">
          ${screenshotUrl ? `<a href="${escapeHtml(screenshotUrl)}" target="_blank" rel="noopener"><img src="${escapeHtml(screenshotUrl)}" alt="前端拦截图"></a>` : `<span>无截图</span>`}
        </div>
      </article>`;
  }).join("") || `<div class="emptyScenario">暂无前端检测结果。请先给接口配置页面 URL，然后点击“执行前端拦截检测”。</div>`;
}
/**
 * 渲染 API 执行结果表。所有来自接口响应、导入文件或 AI 的动态字段必须 escapeHtml()；
 * 不允许为了展示富文本直接拼接未转义 innerHTML。
 */
function renderRunPage() {
  const query = ($("apiResultSearch")?.value || "").trim().toLowerCase();
  const statusFilter = $("apiStatusFilter")?.value || "";
  const filtered = state.runResults.map((r, index) => ({r, index})).filter(({r}) => {
    const text = [r.role, r.section, r.status, r.name, r.method, r.path, r.http, r.verdict, r.confidence, r.body_preview, ...(r.failures || [])].join(" ").toLowerCase();
    const statusMatched = !statusFilter
      || r.status === statusFilter
      || (statusFilter === "SKIP" && ["SKIP", "SKIPPED", "SKIPPED_BY_POLICY"].includes(r.status));
    return (!query || text.includes(query)) && statusMatched;
  });
  const pageSize = effectiveRunPageSize();
  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
  state.runPage = Math.min(Math.max(1, state.runPage), totalPages);
  const start = (state.runPage - 1) * pageSize;
  const rows = filtered.slice(start, start + pageSize);
  $("runRows").innerHTML = rows.map(({r, index}) => {
    const cls = r.status === "PASS" ? "statusPass" : (r.status === "ERROR" ? "statusError" : "statusFail");
    const failures = (r.failures || []).join("；");
    const key = runResultKey(r, index);
    const checked = state.manualReview[key] ? "checked" : "";
    const replay = r.replay || {};
    return `
      <tr>
        <td>${escapeHtml(r.role || "")}</td>
        <td>${escapeHtml(r.section || "")}</td>
        <td class="${cls}">${escapeHtml(r.status || "")}</td>
        <td><div class="runIface"><div class="runPath">${escapeHtml(r.method || "")} ${escapeHtml(r.path || "")}</div><div class="runName">${escapeHtml(r.name || "")}</div><div class="runUrl">${escapeHtml(replay.url || "")}</div></div></td>
        <td>${escapeHtml(r.http ?? "")}</td>
        <td><div class="runVerdict"><strong>${escapeHtml(r.verdict || "未记录")}</strong>${r.confidence ? `<span>置信度: ${escapeHtml(r.confidence)}</span>` : ""}</div></td>
        <td><div class="runFailure">${escapeHtml(failures || r.body_preview || "")}</div></td>
        <td>
          <div class="reviewActions">
            <label class="miniCheck"><input class="reviewCheck" type="checkbox" data-index="${index}" ${checked}>复核</label>
            <button class="miniBtn showReplay" type="button" data-index="${index}">详情</button>
          </div>
        </td>
      </tr>`;
  }).join("") || `<tr><td colspan="8">${emptyRunResultMessage(query, statusFilter)}</td></tr>`;
  document.querySelectorAll(".reviewCheck").forEach(input => {
    input.onchange = () => {
      const index = Number(input.dataset.index);
      const row = state.runResults[index];
      const key = runResultKey(row, index);
      if (input.checked) {
        state.manualReview[key] = manualReviewItem(row, index);
      } else {
        delete state.manualReview[key];
      }
      updateManualReviewCount();
    };
  });
  document.querySelectorAll(".showReplay").forEach(button => {
    button.onclick = () => openReplayDetail(Number(button.dataset.index));
  });
  updateManualReviewCount();
  $("runPageInfo").textContent = `第 ${state.runPage} / ${totalPages} 页，共 ${filtered.length} 条，每页 ${pageSize} 条`;
  syncPageJump("runPageJump", state.runPage, totalPages);
  $("prevRunPage").disabled = state.runPage <= 1;
  $("nextRunPage").disabled = state.runPage >= totalPages;
}

function emptyRunResultMessage(query = "", statusFilter = "") {
  const summary = state.runSummary || {};
  if (summary.run_error || Number(summary.total || 0) === 0) {
    const filterHint = query || statusFilter ? "当前筛选条件下没有结果；本次执行总数也是 0。" : "本次执行没有产生任何 API 结果。";
    return `
      <div class="emptyScenario">
        <strong>${filterHint}</strong>
        <p>原因证据、修复建议和原始日志下载入口已在上方“原因诊断”中展示。</p>
        <p>注意：Agent 计划审查只检查配置和计划完整性，不代表账号登录、token 提取、被测环境连通性一定成功。</p>
      </div>`;
  }
  return query || statusFilter ? "当前筛选条件下没有执行结果。" : "暂无执行结果";
}

function manualReviewItem(row, index) {
  const replay = row?.replay || {};
  return {
    index,
    role: row?.role || "",
    section: row?.section || "",
    status: row?.status || "",
    name: row?.name || "",
    method: row?.method || replay.method || "",
    path: row?.path || replay.path || "",
    url: replay.url || "",
    http: row?.http ?? "",
    verdict: row?.verdict || "",
    confidence: row?.confidence || "",
    failures: row?.failures || [],
    request: replay,
    response_preview: row?.response_body || row?.body_preview || "",
    review_status: "待人工复核",
  };
}

function openReplayDetail(index) {
  const row = state.runResults[index];
  if (!row) return;
  const replay = row.replay || {};
  const finalUrl = urlWithParams(replay.url || row.path || "", replay.params || {});
  const title = `${row.method || replay.method || ""} ${row.path || replay.path || ""}`;
  $("resultDetailTitle").textContent = `接口复测详情：${title}`;
  $("resultDetailBody").innerHTML = `
    <div class="detailGrid">
      <div><span>角色</span><strong>${escapeHtml(row.role || "")}</strong></div>
      <div><span>分组</span><strong>${escapeHtml(row.section || "")}</strong></div>
      <div><span>状态</span><strong>${escapeHtml(row.status || "")}</strong></div>
      <div><span>HTTP</span><strong>${escapeHtml(row.http ?? "")}</strong></div>
    </div>
    <h4>请求 URL</h4>
    <pre class="codeBlock">${escapeHtml(`${replay.method || row.method || "GET"} ${finalUrl}`)}</pre>
    <h4>Headers</h4>
    <pre class="codeBlock">${escapeHtml(prettyJson(replay.headers || {}))}</pre>
    <h4>URL 参数</h4>
    <pre class="codeBlock">${escapeHtml(prettyJson(replay.params || {}))}</pre>
    <h4>请求体</h4>
    <pre class="codeBlock">${escapeHtml(prettyJson(replay.body ?? {}))}</pre>
    <h4>断言与响应</h4>
    <pre class="codeBlock">${escapeHtml([
      `预期：${row.expected || ""}`,
      `结论：${row.verdict || "未记录"} ${row.confidence ? `(${row.confidence})` : ""}`,
      `失败原因：${(row.failures || []).join("；") || "无"}`,
      "",
      row.response_body || row.body_preview || "",
    ].join("\n"))}</pre>
    <h4>参考 cURL</h4>
    <pre class="codeBlock">${escapeHtml(buildCurlCommand(row))}</pre>
  `;
  $("resultDetailModal").hidden = false;
}

function closeReplayDetail() {
  $("resultDetailModal").hidden = true;
}

/** 展示 AI 复核和人工复测建议；不在浏览器侧自动修改后端断言结论。 */
function renderAiRunInsights(result = {}) {
  state.aiRunInsights = result;
  const provider = result.provider || {};
  const summary = result.summary || {};
  const priorities = result.manual_retest_priorities || [];
  const findings = result.risk_findings || [];
  const expanded = Boolean(state.aiRunExpanded);
  const providerNote = provider.error
    ? `${provider.note || "AI 分析已回退"}：${provider.error}`
    : (provider.note || "");
  const html = `
    <div class="aiInsightHead">
      <div>
        <strong>${escapeHtml(result.report_brief || summary.risk_text || "AI 分析完成")}</strong>
        <span>${escapeHtml(result.generated_at || "")} · ${escapeHtml(provider.mode || "")} ${escapeHtml(provider.model || "")}</span>
      </div>
      <div class="aiInsightActions">
        <div class="aiBadges">
          <span>P0 ${summary.p0 || 0}</span>
          <span>P1 ${summary.p1 || 0}</span>
          <span>P2 ${summary.p2 || 0}</span>
        </div>
        <button class="ghost miniToggle" type="button" data-ai-toggle="run">${expanded ? "收起详情" : "展开详情"}</button>
      </div>
    </div>
    ${providerNote ? `<p class="hint">${escapeHtml(providerNote)}</p>` : ""}
    <div class="aiInsightBody" ${expanded ? "" : "hidden"}>
    <div class="aiInsightGrid">
      <div>
        <h4>风险发现</h4>
        ${findings.slice(0, 8).map(item => `
          <div class="aiInsightItem">
            <b>${escapeHtml(item.priority || "")} ${escapeHtml(item.role || "")} ${escapeHtml(item.method || "")} ${escapeHtml(item.path || item.name || "")}</b>
            <span>${escapeHtml(item.reason || "")}</span>
            <p>${escapeHtml(item.evidence || "")}</p>
          </div>
        `).join("") || `<p class="hint">暂无高风险发现。</p>`}
      </div>
      <div>
        <h4>手工复测优先级</h4>
        ${priorities.slice(0, 10).map(item => `
          <div class="aiInsightItem">
            <b>${escapeHtml(item.priority || "")} ${escapeHtml(item.role || "")}</b>
            <span>${escapeHtml(item.method || "")} ${escapeHtml(item.path || "")}</span>
            <p>${escapeHtml(item.manual_retest || item.reason || "")}</p>
          </div>
        `).join("") || `<p class="hint">暂无复测建议。</p>`}
      </div>
    </div>
    </div>`;
  $("aiRunInsights").hidden = false;
  $("aiRunInsights").innerHTML = html;
  $("aiRunReportBody").innerHTML = html
    .replace(/<button class="ghost miniToggle" type="button" data-ai-toggle="run">.*?<\/button>/, "")
    .replace(/<div class="aiInsightBody" hidden>/, '<div class="aiInsightBody">');
  $("openAiRunReport").disabled = false;
  $("aiRunInsights").querySelector('[data-ai-toggle="run"]')?.addEventListener("click", () => {
    state.aiRunExpanded = !state.aiRunExpanded;
    renderAiRunInsights(state.aiRunInsights || {});
  });
}

function openAiRunReport() {
  if (state.aiRunInsights) {
    $("aiRunReportModal").hidden = false;
  }
}

function closeAiRunReport() {
  $("aiRunReportModal").hidden = true;
}

function renderAssertionProfile(profile = {}) {
  const rows = [
    ["断言模式", profile.mode || "未生成", "当前项目采用的自适应断言模式"],
    ["成功字段", profile.success_field || "未识别", "例如 success=true/false"],
    ["成功值", JSON.stringify(profile.success_values || [], null, 2), "被学习为成功响应的字段值"],
    ["拦截值", JSON.stringify(profile.blocked_values || [], null, 2), "被学习为权限拦截的字段值"],
    ["业务码字段", profile.code_field || "未识别", "例如 code/status/errorCode"],
    ["成功业务码", JSON.stringify(profile.success_codes || [], null, 2), "项目里成功响应常见业务码"],
    ["拦截业务码", JSON.stringify(profile.blocked_codes || [], null, 2), "项目里权限拦截常见业务码"],
    ["消息字段", profile.message_field || "未识别", "用于识别无权限、成功等文案"],
    ["数据字段", profile.data_field || "未识别", "用于判断是否返回真实业务数据"],
  ];
  $("profileCards").innerHTML = [
    ["模式", profile.mode || "未生成"],
    ["成功字段", profile.success_field || "未识别"],
    ["业务码字段", profile.code_field || "未识别"],
  ].map(([label, value]) => `<div class="stat"><span>${label}</span><strong>${escapeHtml(value)}</strong></div>`).join("");
  $("profileRows").innerHTML = rows.map(([key, value, desc]) => `
    <tr>
      <td>${escapeHtml(key)}</td>
      <td><pre class="inlinePre">${escapeHtml(value)}</pre></td>
      <td>${escapeHtml(desc)}</td>
    </tr>
  `).join("");
}

function renderPrettyRunLog(summary = {}) {
  const query = ($("roleLogSearch")?.value || "").trim().toLowerCase();
  const results = (summary.results || []).filter(r => {
    const text = [r.role, r.section, r.status, r.name, r.method, r.path, r.http, r.verdict, r.confidence, r.body_preview, ...(r.failures || [])].join(" ").toLowerCase();
    return !query || text.includes(query);
  });
  if (!results.length) {
    $("prettyRunLog").innerHTML = query ? "没有匹配的执行日志。" : "暂无执行日志。";
    return;
  }
  const byRole = new Map();
  for (const item of results) {
    const role = item.role || "未知角色";
    if (!byRole.has(role)) byRole.set(role, []);
    byRole.get(role).push(item);
  }
  const parts = [];
  parts.push(`<div>======================================================================</div>`);
  parts.push(`<div>  测试汇总</div>`);
  parts.push(`<div>  总计: ${summary.total || results.length}  |  通过: ${summary.passed || 0}  |  失败: ${summary.failed || 0}  |  跳过: ${summary.skipped || 0}</div>`);
  parts.push(`<div>======================================================================</div>`);

  for (const [role, roleRows] of byRole.entries()) {
    parts.push(`<div class="logRole">`);
    parts.push(`<div class="logRoleTitle">============================= 角色: ${escapeHtml(role)} =============================</div>`);
    for (const section of ["自身权限", "越权"]) {
      const sectionRows = roleRows.filter(r => r.section === section);
      if (!sectionRows.length) continue;
      parts.push(`<div class="logSection">──────────────────────  ${section}测试 (${sectionRows.length} 个接口)  ──────────────────────</div>`);
      for (const r of sectionRows) {
        const status = r.status || "";
        const cls = status === "PASS" ? "pass" : (status === "ERROR" ? "error" : "fail");
        const label = status === "PASS" ? "[PASS]" : (status === "ERROR" ? "[ERROR]" : "[FAIL]");
        const suffix = section === "越权" && status === "FAIL" ? " (应被拦截)" : (section === "自身权限" && status === "FAIL" ? " (应可访问)" : "");
        parts.push(`<div class="logLine ${cls}">${label} ${escapeHtml(r.name || "")}  ${escapeHtml(r.path || "")}${suffix}</div>`);
        parts.push(`<div class="logMeta">HTTP ${escapeHtml(r.http ?? "")} | ${escapeHtml(r.body_preview || "")}</div>`);
        if (r.verdict || r.confidence) {
          parts.push(`<div class="logMeta">assertion ${escapeHtml(r.verdict || "")}${r.confidence ? `（置信度: ${escapeHtml(r.confidence)}）` : ""}</div>`);
        }
        for (const failure of (r.failures || [])) {
          parts.push(`<div class="logAssert">assert ${escapeHtml(failure)}</div>`);
        }
      }
    }
    const stats = (summary.role_stats || {})[role] || {};
    parts.push(`<div class="logMeta">小结: ${stats.pass || 0}/${stats.total || roleRows.length} PASS  |  ${(stats.fail || 0) + (stats.error || 0)}/${stats.total || roleRows.length} FAIL/ERROR</div>`);
    parts.push(`</div>`);
  }
  $("prettyRunLog").innerHTML = parts.join("");
}

/** 读取不可变执行归档；当前运行固定作为左侧基准，选中的历史运行作为右侧。 */
async function loadRunHistory() {
  if (!state.project_id) {
    $("runHistoryRows").innerHTML = `<tr><td colspan="5">请先打开或保存项目。</td></tr>`;
    renderRunCompare();
    return;
  }
  const result = await api(`/api/projects/${state.project_id}/runs`);
  state.runHistory = result.runs || [];
  renderResultCards();
  renderRunHistory();
  renderRunCompare();
}

function renderRunHistory() {
  const query = ($("runHistorySearch")?.value || "").trim().toLowerCase();
  const rows = state.runHistory.filter(item => {
    const text = [item.run_id, item.time, item.env, item.returncode, item.total, item.passed, item.failed, item.skipped, ...(item.roles || []), item.note].join(" ").toLowerCase();
    return !query || text.includes(query);
  });
  $("runHistoryRows").innerHTML = rows.map(item => `
    <tr class="${state.selectedHistoryRunId === item.run_id ? "selectedRun" : ""}">
      <td>
        <label class="miniCheck">
          <input class="pickRun" type="radio" name="historyRunPick" data-id="${escapeHtml(item.run_id)}" ${state.selectedHistoryRunId === item.run_id ? "checked" : ""}>
        </label>
      </td>
      <td>
        <div class="historyMeta">
          <strong>${escapeHtml(item.time || "")}</strong>
          <span title="${escapeHtml(item.run_id || "")}">${escapeHtml(item.run_id || "").slice(0, 22)}</span>
          <em>${escapeHtml(item.env || "")}</em>
        </div>
      </td>
      <td>
        <div class="historyResult">
          <span>code=${escapeHtml(item.returncode ?? "-")}</span>
          <span>总 ${escapeHtml(item.total ?? 0)}</span>
          <span class="statusPass">PASS ${escapeHtml(item.passed ?? 0)}</span>
          <span class="statusFail">FAIL ${escapeHtml(item.failed ?? 0)}</span>
          <span>SKIP ${escapeHtml(item.skipped ?? 0)}</span>
        </div>
      </td>
      <td><input class="runNoteInput" data-id="${escapeHtml(item.run_id)}" placeholder="备注背景 / 版本 / 修复点" value="${escapeHtml(item.note || "")}"></td>
      <td>
        <div class="rowActions historyActions">
          <button type="button" class="miniBtn saveRunNote" data-id="${escapeHtml(item.run_id)}">保存</button>
          <button type="button" class="miniBtn dangerBtn deleteRun" data-id="${escapeHtml(item.run_id)}">删除</button>
        </div>
      </td>
    </tr>
  `).join("") || `<tr><td colspan="5">暂无匹配的执行历史。</td></tr>`;
  document.querySelectorAll(".pickRun").forEach(btn => {
    btn.onchange = () => loadRunForCompare(btn.dataset.id);
  });
  document.querySelectorAll(".saveRunNote").forEach(btn => {
    btn.onclick = () => saveRunNote(btn.dataset.id).catch(err => chat(`保存历史备注失败：${err.message}`));
  });
  document.querySelectorAll(".deleteRun").forEach(btn => {
    btn.onclick = () => deleteRunHistory(btn.dataset.id).catch(err => chat(`删除历史记录失败：${err.message}`));
  });
}

async function loadRunForCompare(runId) {
  if (!state.project_id || !runId) return;
  state.selectedHistoryRunId = runId;
  const result = await api(`/api/projects/${state.project_id}/runs/${runId}`);
  state.compareRuns.right = result;
  renderRunHistory();
  renderRunCompare();
}

async function saveRunNote(runId) {
  if (!state.project_id || !runId) return;
  const input = document.querySelector(`.runNoteInput[data-id="${escapeCssValue(runId)}"]`);
  const note = input ? input.value : "";
  await api(`/api/projects/${state.project_id}/runs/${runId}`, {
    method: "PATCH",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({note}),
  });
  const item = state.runHistory.find(row => row.run_id === runId);
  if (item) item.note = note;
  chat("历史记录备注已保存。");
  renderRunHistory();
}

async function deleteRunHistory(runId) {
  if (!state.project_id || !runId) return;
  if (!confirm("确认删除这条执行历史吗？只会删除归档记录，不会删除当前执行结果。")) return;
  await api(`/api/projects/${state.project_id}/runs/${runId}`, {method: "DELETE"});
  state.runHistory = state.runHistory.filter(item => item.run_id !== runId);
  if (state.selectedHistoryRunId === runId) {
    state.selectedHistoryRunId = "";
    state.compareRuns.right = null;
  }
  renderRunHistory();
  renderRunCompare();
  renderResultCards();
  await loadRunHistory();
  chat("已删除一条执行历史。");
}

function currentRunForCompare() {
  if (!state.runSummary || !Object.keys(state.runSummary).length) return null;
  return {
    meta: {
      time: state.runSummary.time || state.runFinishedAt || "-",
      returncode: "当前",
      env: state.runSummary.env || "",
      total: state.runSummary.total || 0,
      passed: state.runSummary.passed || 0,
      failed: state.runSummary.failed || 0,
      skipped: state.runSummary.skipped || 0,
      roles: state.runSummary.roles || [],
      note: "当前执行结果，固定作为左侧基准。",
    },
    run_summary: state.runSummary,
    log: $("runLog")?.textContent || "",
  };
}

/**
 * 生成计划的 Agent 编排入口：规则基线 -> AI 审查 -> 后端已校验的 modified_plan -> UI。
 * AI 不可用时后端仍返回本地规则审查，因此计划生成不依赖模型在线。
 */
async function makePlanWithFeedback(existingPlan = null) {
  // Agent 模式：
  // 1. 规则生成基线计划
  // 2. AI 审查计划并返回具体修改建议
  // 3. 后端自动合并 AI 修改建议，返回 modified_plan
  // 4. 如果 AI 修改了计划，用 modified_plan 替换原有计划
  if ($("planActionStatus")) $("planActionStatus").textContent = "";
  await runWithElapsed("makePlan", "planActionStatus", "计划生成中", async () => {
    const plan = existingPlan || await api("/api/plan", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(collectProject()),
    });

    $("planActionStatus").textContent = "基线计划已生成，AI Agent 正在审查并优化计划...";
    const ai = await api("/api/ai/analyze-plan", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({project: collectProjectForAi(), plan, ai_config: collectAiSettings()}),
    });

    // AI 返回了修改后的计划 → 用它替换基线计划
    const finalPlan = ai.modified_plan || plan;
    state.plan = finalPlan;
    renderPlan(finalPlan);

    state.aiPlanExpanded = false;
    state.planModsPage = 1;
    renderAiPlanInsights(ai);

    const total = (finalPlan.cases || []).length;
    const modCount = ai.modifications_applied || 0;
    const provider = ai.provider || {};
    const modText = modCount ? `，AI Agent 已自动优化 ${modCount} 条计划项` : "";
    $("planActionStatus").textContent = `计划生成完成：${total} 条计划项${modText}。${provider.error ? "(AI 已回退本地审查)" : ""}`;
    chat(`计划生成完成：${total} 条计划项${modText}，AI Agent 审查已完成。`);
  }, "30-90 秒（AI Agent 审查中）");
}

function compareResultKey(row = {}) {
  return [row.role || "", row.section || "", row.method || "", row.path || row.name || ""].join("|");
}

function runRowsByKey(run) {
  const rows = ((run || {}).run_summary || {}).results || [];
  const map = new Map();
  rows.forEach(row => {
    const key = compareResultKey(row);
    if (key && !map.has(key)) map.set(key, row);
  });
  return map;
}

function compareFieldChanged(left, right) {
  if (!left || !right) return false;
  return String(left.status || "") !== String(right.status || "")
    || String(left.http ?? "") !== String(right.http ?? "")
    || String(left.verdict || "") !== String(right.verdict || "")
    || String(left.confidence || "") !== String(right.confidence || "");
}

function compareFilterKeys(leftRun, rightRun) {
  if (!state.compareDiffOnly || !rightRun) return null;
  const leftMap = runRowsByKey(leftRun);
  const rightMap = runRowsByKey(rightRun);
  const keys = new Set();
  leftMap.forEach((left, key) => {
    const right = rightMap.get(key);
    if (right && compareFieldChanged(left, right)) keys.add(key);
  });
  return keys;
}

function comparedRunRows(run, keys = null, query = "") {
  const rows = (((run || {}).run_summary || {}).results || []);
  const needle = query.trim().toLowerCase();
  return rows.filter(row => {
    if (keys && !keys.has(compareResultKey(row))) return false;
    const text = [
      row.role, row.section, row.status, row.method, row.path, row.name,
      row.http, row.verdict, row.confidence, row.body_preview, ...(row.failures || []),
    ].join(" ").toLowerCase();
    return !needle || text.includes(needle);
  });
}

function summarizeComparedRun(run, emptyText = "请选择一次执行。", keys = null, query = "") {
  if (!run) return emptyText;
  const meta = run.meta || {};
  const rows = comparedRunRows(run, keys, query);
  return `
    <dl class="compareStats">
      <dt>时间</dt><dd>${escapeHtml(meta.time || "-")}</dd>
      <dt>返回码</dt><dd>${escapeHtml(meta.returncode ?? "-")}</dd>
      <dt>环境</dt><dd>${escapeHtml(meta.env || "-")}</dd>
      <dt>展示接口</dt><dd>${escapeHtml(rows.length)}</dd>
      <dt>总数</dt><dd>${escapeHtml(meta.total ?? ((run.run_summary || {}).total ?? 0))}</dd>
      <dt>通过</dt><dd class="statusPass">${escapeHtml(meta.passed ?? ((run.run_summary || {}).passed ?? 0))}</dd>
      <dt>失败</dt><dd class="statusFail">${escapeHtml(meta.failed ?? ((run.run_summary || {}).failed ?? 0))}</dd>
      <dt>跳过</dt><dd>${escapeHtml(meta.skipped ?? ((run.run_summary || {}).skipped ?? 0))}</dd>
    </dl>
    <div class="compareTableWrap">
      <table class="compareResultTable">
        <thead><tr><th>角色</th><th>分组</th><th>状态</th><th>接口</th><th>HTTP</th><th>断言/响应</th></tr></thead>
        <tbody>
          ${rows.map(row => {
            const cls = row.status === "PASS" ? "statusPass" : (row.status === "ERROR" ? "statusError" : "statusFail");
            return `
              <tr>
                <td>${escapeHtml(row.role || "")}</td>
                <td>${escapeHtml(row.section || "")}</td>
                <td class="${cls}">${escapeHtml(row.status || "")}</td>
                <td><div class="runPath">${escapeHtml(row.method || "")} ${escapeHtml(row.path || "")}</div><div class="runName">${escapeHtml(row.name || "")}</div></td>
                <td>${escapeHtml(row.http ?? "")}</td>
                <td><div class="runFailure">${escapeHtml(row.verdict || "")}${row.confidence ? ` / ${escapeHtml(row.confidence)}` : ""}<br>${escapeHtml((row.failures || []).join("；") || row.body_preview || "")}</div></td>
              </tr>`;
          }).join("") || `<tr><td colspan="6">${query ? "没有匹配的接口结果。" : (state.compareDiffOnly ? "没有两次共有且结果不同的接口。" : "暂无接口结果。")}</td></tr>`}
        </tbody>
      </table>
    </div>
  `;
}

/** 按接口业务键对齐两次运行，可选择仅展示共有接口中状态/HTTP/断言发生变化的项。 */
function renderRunCompare() {
  const leftRun = currentRunForCompare();
  const rightRun = state.compareRuns.right;
  const keys = compareFilterKeys(leftRun, rightRun);
  $("compareLeft").innerHTML = summarizeComparedRun(leftRun, "当前项目尚未加载执行结果。", keys, state.compareSearch.left);
  $("compareRight").innerHTML = summarizeComparedRun(rightRun, "请在上方选择一条历史记录。", keys, state.compareSearch.right);
  if ($("toggleCompareDiff")) {
    $("toggleCompareDiff").classList.toggle("active", state.compareDiffOnly);
    $("toggleCompareDiff").textContent = state.compareDiffOnly ? "显示全部接口" : "只看差异接口";
  }
}

/**
 * 保存项目主链路：前端校验 -> 登录检查 -> POST 项目 -> 更新 project_id/下载链接
 * -> 可选刷新计划 -> 重新拉取项目列表。只有后端成功后才清除 isDirty。
 */
