async function saveProject(options = {}) {
  // 浏览器 -> 后端持久化。后端返回 project_id 和新生成的执行计划。
  const {refreshPlan = true} = options;
  const project = collectProject();
  // 先校验表单必填项，再检查登录状态
  if (!validateProjectBeforeSave(project)) {
    throw new Error("项目名称和被测环境地址为必填项");
  }
  if (!state.user) {
    openAuthModal();
    throw new Error("请先登录后再保存项目");
  }
  const result = await api("/api/projects", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(project),
  });
  state.project_id = result.project_id;
  notifyFindingsChanged();
  $("currentProject").textContent = `当前：${project.project_name || result.project_id}`;
  updateDownloads();
  loadProjects().catch(() => {});
  chat(`项目已保存：${state.project_id}`);
  if (result.ai_task?.task_id) {
    toolLog(`AI 基础配置理解已后台启动：${result.ai_task.task_id}`);
    waitForTask(result.ai_task, null, "AI 基础配置理解")
      .then(taskResult => {
        const ai = taskResult.ai_config_analysis || taskResult;
        logAiActions("AI 基础配置理解完成", ai);
      })
      .catch(err => toolLog(`AI 基础配置理解失败：${err.message}`, "error"));
  }
  markClean();
  if (refreshPlan) {
    renderPlan(result.plan);
    toolLog("基础执行计划已按规则生成；进入执行计划页后可手动触发 AI 审查。");
  } else if (result.plan) {
    renderPlan(result.plan);
  }
}

/**
 * 上传证据并按格式合并解析结果。文件先保存为 source_file，再把 endpoint/PRD/用例
 * 写入内存草稿；项目保存后该 source_id 才归档进项目目录。
 */
async function importFile(kind, inputId, sourceRole = "") {
  // 文件上传流程。sourceRole 只来自角色导入弹窗，后端会把它写成 observed_roles。
  const file = $(inputId).files[0];
  if (!file) return chat("请先选择文件。");
  const input = $(inputId);
  const fd = new FormData();
  fd.append("file", file);
  if (state.project_id) fd.append("project_id", state.project_id);
  if (sourceRole) fd.append("source_role", sourceRole);
  const result = await api(`/api/import/${kind}`, {method: "POST", body: fd});
  if (result.source_file) {
    result.source_file.source_role = sourceRole || "";
    const exists = state.sourceFiles.some(item => item.source_id === result.source_file.source_id);
    if (!exists) state.sourceFiles.push(result.source_file);
    else state.sourceFiles = state.sourceFiles.map(item => item.source_id === result.source_file.source_id ? {...item, ...result.source_file} : item);
    markDirty();
    renderSourceLists();
  }
  if (result.prd_text) {
    $("prdText").value = result.prd_text;
    markDirty();
    chat("导入 PRD 完成");
  }
  if (result.testcases) {
    state.testcases = result.testcases;
    $("testcaseDigest").value = result.testcases.digest || "";
    markDirty();
    chat(`解析测试用例完成：${result.testcases.total} 条`);
  }
  if (result.endpoints) {
    mergeImportedEndpoints(result.endpoints, "接口导入完成");
    analyzeImportWithAi(result.endpoints).catch(err => chat(`AI 接口导入审查失败：${err.message}`));
  }
  if (input) input.value = "";
  updateImportReadyStates();
  if (sourceRole && result.endpoints) {
    state.roleImportPreview = result.endpoints || [];
    renderRoleImportPreview();
  }
}

function updateImportReadyStates() {
  const pairs = [
    ["harFile", "importHar"],
    ["collectionFile", "importCollection"],
    ["roleHarFile", "roleImportHar"],
    ["roleCollectionFile", "roleImportCollection"],
  ];
  pairs.forEach(([inputId, buttonId]) => {
    const input = $(inputId);
    const button = $(buttonId);
    if (input && button) button.classList.toggle("ready", Boolean(input.files && input.files.length));
  });
  if ($("importCurl")) $("importCurl").classList.toggle("ready", Boolean(($("curlText")?.value || "").trim()));
  if ($("roleImportCurl")) $("roleImportCurl").classList.toggle("ready", Boolean(($("roleCurlText")?.value || "").trim()));
}

/**
 * 按 method+path 合并接口池，保留 allowed/observed/discovered 角色和来源证据并集。
 * 录制角色只是观察证据，所以合并后的 needs_review 仍要求用户确认真实授权关系。
 */
function mergeImportedEndpoints(endpoints = [], label = "接口导入完成") {
  // 按 method+path 合并接口；同一接口被另一个角色再次导入时，补充角色证据而不是丢弃。
  let added = 0;
  let merged = 0;
  endpoints.forEach(ep => {
    const method = String(ep.method || "GET").toUpperCase();
    const key = `${method}:${ep.path}`;
    if (!ep.path) return;
    const existing = state.endpoints.find(item => `${String(item.method || "GET").toUpperCase()}:${item.path}` === key);
    if (existing) {
      const hadPageUrl = Boolean(existing.page_url);
      ["allowed_roles", "observed_roles", "discovered_by"].forEach(field => {
        existing[field] = Array.from(new Set([...(existing[field] || []), ...(ep[field] || [])]));
      });
      ["source_sections", "source_pages", "source_page_titles"].forEach(field => {
        existing[field] = Array.from(new Set([...(existing[field] || []), ...(ep[field] || [])]));
      });
      ["params", "headers", "body", "sample_response", "description", "page_url"].forEach(field => {
        if (!existing[field] && ep[field]) existing[field] = ep[field];
      });
      if (!existing.source_section && ep.source_section) existing.source_section = ep.source_section;
      if (!hadPageUrl && ep.page_url) existing.page_url_inferred = Boolean(ep.page_url_inferred);
      existing.sources = Array.from(new Set([...(existing.sources || (existing.source ? [existing.source] : [])), ep.source || "import"]));
      existing.source = existing.sources[0] || existing.source || ep.source || "import";
      existing.needs_review = Boolean(existing.needs_review || ep.needs_review);
      merged += 1;
      return;
    }
    state.endpoints.push({
      ...ep,
      method,
      source: ep.source || "import",
      sources: Array.from(new Set([...(ep.sources || []), ep.source || "import"])),
      observed_roles: ep.observed_roles || [],
      discovered_by: ep.discovered_by || ep.observed_roles || [],
      needs_review: Boolean(ep.needs_review),
      page_url_inferred: Boolean(ep.page_url_inferred),
    });
    added += 1;
  });
  state.endpointPage = 1;
  if (added || merged) markDirty();
  renderEndpoints();
  renderRoleImportStrip();
  chat(`${label}：新增 ${added} 个，合并 ${merged} 个，来源候选 ${endpoints.length} 个`);
}

function sourceBelongsTo(item, kind, sourceRole = "") {
  if (!item || item.kind !== kind) return false;
  if (!sourceRole) return !item.source_role;
  return item.source_role === sourceRole;
}

function renderSourceList(containerId, kind, sourceRole = "") {
  const box = $(containerId);
  if (!box) return;
  const rows = (state.sourceFiles || []).filter(item => sourceBelongsTo(item, kind, sourceRole));
  if (!rows.length) {
    box.innerHTML = "";
    return;
  }
  box.innerHTML = rows.map(item => `
    <div class="sourceItem" title="${escapeHtml(item.original_name || item.stored_path || item.source_id)}">
      <span class="sourceItemName">${escapeHtml(item.original_name || item.stored_path || item.source_id)}</span>
      <button type="button" class="deleteSourceFile" data-source-id="${escapeHtml(item.source_id)}">删除</button>
    </div>
  `).join("");
  box.querySelectorAll(".deleteSourceFile").forEach(button => {
    button.onclick = () => deleteSourceFile(button.dataset.sourceId);
  });
}

function renderSourceLists() {
  renderSourceList("harSourceList", "har");
  renderSourceList("collectionSourceList", "collection");
  renderSourceList("prdSourceList", "prd");
  renderSourceList("testcaseSourceList", "testcase");
  renderSourceList("roleHarSourceList", "har", state.activeImportRole || "");
  renderSourceList("roleCollectionSourceList", "collection", state.activeImportRole || "");
}

async function deleteSourceFile(sourceId) {
  if (!sourceId) return;
  await api(`/api/source-files/${encodeURIComponent(sourceId)}`, {method: "DELETE"});
  state.sourceFiles = (state.sourceFiles || []).filter(item => item.source_id !== sourceId);
  markDirty();
  renderSourceLists();
  chat("已删除导入包记录。");
}

async function importCurl(sourceRole = "") {
  // cURL 导入和文件导入类似；如果在角色弹窗里调用，也会把 source_role 发给后端解析器。
  const textInput = sourceRole ? $("roleCurlText") : $("curlText");
  const text = textInput.value.trim();
  if (!text) return chat("请先粘贴 cURL 请求。");
  const result = await api("/api/import-curl", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({text, source_role: sourceRole, project_id: state.project_id, roles: state.roles}),
  });
  mergeImportedEndpoints(result.endpoints || [], "cURL 解析完成");
  analyzeImportWithAi(result.endpoints || []).catch(err => chat(`AI 接口导入审查失败：${err.message}`));
  if (sourceRole) {
    state.roleImportPreview = result.endpoints || [];
    renderRoleImportPreview();
  }
}

const importHelp = {
  har: {
    title: "HAR 抓包怎么操作",
    text: "用浏览器打开系统，按 F12 进入 Network，勾选 Preserve log，按目标角色正常操作一遍业务页面，然后右键请求列表选择 Save all as HAR。它适合覆盖真实浏览器访问过的接口、参数和响应样例；上传前建议过滤无关静态资源和敏感个人数据。",
  },
  collection: {
    title: "接口集合适合什么文件",
    text: "支持 Postman Collection，以及常见 Apifox/YApi 导出的 JSON。它适合团队已有接口管理平台的场景，能批量拿到路径、方法、Header、Body 模板；导入后仍需要在表格里确认 allowed_roles。",
  },
  manual: {
    title: "手工接口什么时候用",
    text: "当只有少量重点接口，或者测试用例里明确写了接口路径时，直接手工新增最快。建议优先补 method、path、allowed_roles、page_url，后续报告会按这些字段生成越权测试计划。",
  },
  curl: {
    title: "cURL 粘贴怎么拿",
    text: "在浏览器 Network 里右键某个请求，选择 Copy as cURL；Postman/Apifox 也可以导出 cURL。这个方式最适合补单条接口的真实 Header、query 和请求体；工具会过滤 Authorization 和 Cookie，执行时再用角色账号自动获取 token。",
  },
};

function showImportHelp(key) {
  const item = importHelp[key];
  if (!item) return;
  $("importHelpTitle").textContent = item.title;
  $("importHelpText").textContent = item.text;
  $("importHelpModal").hidden = false;
  document.querySelectorAll(".helpDot").forEach(btn => btn.classList.toggle("active", btn.dataset.help === key));
}

function hideImportHelp() {
  $("importHelpModal").hidden = true;
  document.querySelectorAll(".helpDot").forEach(btn => btn.classList.remove("active"));
}

/** 将录制器证据转换为统一 Endpoint；观察角色作为初始候选，并显式标记 needs_review。 */
function recordingEndpointsToProjectEndpoints(recording = {}, source = "manual_playwright") {
  return (recording.merged_endpoints || []).map(ep => {
    const observedRoles = Array.from(new Set(ep.source_roles || (ep.source_role ? [ep.source_role] : [])));
    const sourcePage = (ep.source_pages || []).find(Boolean) || ep.source_page || "";
    return {
      name: ep.path || "录制接口",
      method: String(ep.method || "GET").toUpperCase(),
      path: ep.path || "",
      module: ep.module || "",
      allowed_roles: observedRoles,
      observed_roles: observedRoles,
      discovered_by: observedRoles,
      page_url: sourcePage,
      page_url_inferred: false,
      source,
      sources: [source],
      needs_review: true,
      params: ep.params || {},
      headers: ep.headers || {},
      body: ep.body || "",
      sample_response: ep.sample_response || {},
      source_pages: ep.source_pages || (sourcePage ? [sourcePage] : []),
      source_page_titles: ep.source_page_titles || [],
      source_section: ep.source_section || "",
      source_sections: ep.source_sections || (ep.source_section ? [ep.source_section] : []),
      description: "Playwright 人工录制；已根据实际访问角色建立初始权限证据，仍需人工确认。",
    };
  }).filter(ep => ep.path);
}

/** 保存当前项目后启动指定角色的可见浏览器录制会话。 */
async function startManualRecording() {
  if (!state.project_id || state.isDirty) await saveProject();
  renderManualRecordingRoles();
  const role = $("manualRecordRole")?.value || "";
  const status = $("manualRecordingStatus");
  if (!role) {
    if (status) status.textContent = "请先添加角色并选择录制角色。";
    return;
  }
  $("startManualRecording").disabled = true;
  $("stopManualRecording").disabled = true;
  if (status) status.textContent = `正在为 ${role} 启动可视浏览器，请在弹出的窗口中操作业务流程...`;
  try {
    const result = await api(`/api/projects/${state.project_id}/manual-recording/start`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({role}),
    });
    if (result.error) throw new Error(result.error);
    state.manualRecordingActive = true;
    $("stopManualRecording").disabled = false;
    if (status) status.textContent = `人工录制中：${role}。请在弹出的浏览器里操作，完成后点击“停止并导入”。`;
    toolLog(`Playwright 人工录制已启动：session=${result.session_id || "-"} role=${role}`);
  } catch (err) {
    state.manualRecordingActive = false;
    $("startManualRecording").disabled = false;
    $("stopManualRecording").disabled = true;
    if (status) status.textContent = `人工录制启动失败：${err.message}`;
    toolLog(status?.textContent || err.message, "error");
  }
}

/** 停止录制、导入去重接口、保留 HAR/页面/菜单来源并触发 AI 导入审查。 */
async function stopManualRecording() {
  const status = $("manualRecordingStatus");
  $("stopManualRecording").disabled = true;
  if (status) status.textContent = "正在停止浏览器并导入录制结果...";
  try {
    const result = await api(`/api/projects/${state.project_id}/manual-recording/stop`, {method: "POST"});
    if (result.error) throw new Error(result.error);
    if (result.status === "stopping") {
      $("stopManualRecording").disabled = false;
      if (status) status.textContent = result.error || "浏览器仍在关闭中，请稍后再次停止。";
      return;
    }
    const endpoints = recordingEndpointsToProjectEndpoints(result, "manual_playwright");
    mergeImportedEndpoints(endpoints, "Playwright 人工录制完成");
    state.manualRecordingActive = false;
    $("startManualRecording").disabled = false;
    $("stopManualRecording").disabled = true;
    if (status) status.textContent = `人工录制完成：${result.total_endpoints || 0} 个去重接口，${(result.all_pages || []).length} 个页面，已导入接口池。`;
    if (result.har_path) toolLog(`人工录制 HAR 已保存：${result.har_path}`);
    toolLog(`Playwright 人工录制结果：${JSON.stringify(result, null, 2)}`);
    if (endpoints.length) analyzeImportWithAi(endpoints).catch(err => toolLog(`AI 人工录制接口审查失败：${err.message}`, "error"));
  } catch (err) {
    state.manualRecordingActive = false;
    $("startManualRecording").disabled = false;
    $("stopManualRecording").disabled = true;
    if (status) status.textContent = `人工录制停止失败：${err.message}`;
    toolLog(status?.textContent || err.message, "error");
  }
}

function updateDownloads() {
  if (!state.project_id) return;
  $("downloadPlan").href = `/api/projects/${state.project_id}/download/test_plan.json`;
  $("downloadPlanResult").href = `/api/projects/${state.project_id}/download/test_plan.json`;
  $("downloadConfig").href = `/api/projects/${state.project_id}/download/generated_config.py`;
  $("downloadReport").href = `/api/projects/${state.project_id}/download/report.docx`;
  $("downloadProfile").href = `/api/projects/${state.project_id}/download/assertion_profile.json`;
  $("downloadRunResults").href = `/api/projects/${state.project_id}/download/run_results.json`;
  $("downloadScenarioResults").href = `/api/projects/${state.project_id}/download/scenario_results.json`;
  $("downloadScenarioArtifact").href = `/api/projects/${state.project_id}/download/scenario_results.json`;
  $("downloadFrontendResults").href = `/api/projects/${state.project_id}/download/frontend_results.json`;
  $("downloadLog").href = `/api/projects/${state.project_id}/download/last_run.log`;
  $("downloadLogFromRole").href = `/api/projects/${state.project_id}/download/last_run.log`;
  $("downloadFrontendLog").href = `/api/projects/${state.project_id}/download/last_frontend_run.log`;
  if ($("downloadManualReview")) $("downloadManualReview").href = `/api/projects/${state.project_id}/download/manual_review.json`;
}

/** 把用户勾选的疑难结果固化为报告和复测都能复用的人工复核清单。 */
function collectManualReviewPayload() {
  return {
    notes: "用户在执行结果页勾选的人工复核接口，供报告生成前确认与复测使用。",
    items: Object.values(state.manualReview || {}),
  };
}

async function saveManualReview(showMessage = true) {
  if (!state.project_id) await saveProject();
  const payload = collectManualReviewPayload();
  const result = await api(`/api/projects/${state.project_id}/manual-review`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload),
  });
  updateDownloads();
  if (showMessage) chat(`人工复核清单已保存：${result.count} 条`);
  return payload;
}

async function generateReportFromCurrent(manualReview) {
  $("runNotice").className = "notice";
  $("runNotice").textContent = "AI 分析已完成，正在生成 Word 报告...";
  const task = await api(`/api/projects/${state.project_id}/report`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({manual_review: manualReview || collectManualReviewPayload()}),
  });
  const result = await waitForTask(task, "runNotice", "报告生成");
  $("runLog").textContent = [
    $("runLog").textContent || "",
    "",
    "===== 报告生成 =====",
    `returncode=${result.returncode}`,
    result.stdout || "",
    result.stderr || "",
    result.report_path ? `报告路径：${result.report_path}` : "",
  ].join("\n");
  state.hasReport = Boolean(result.has_report);
  renderResultCards();
  updateDownloads();
  $("runNotice").className = `notice ${state.hasReport ? "ok" : ""}`;
  $("runNotice").textContent = state.hasReport ? "执行、AI 分析与 Word 报告生成完成。" : "接口执行和 AI 分析已完成，但 Word 报告生成失败，请查看原始输出。";
  chat(state.hasReport ? "报告已基于最新执行结果和 AI 分析生成。" : "报告生成失败，请查看原始输出。");
  return result;
}

/**
 * API 测试按钮主链路：确保项目已保存 -> 可选保存人工复核 -> 创建后台任务
 * -> 轮询结果 -> 渲染 -> 可选基于同一结果生成报告。并发锁防止重复点击创建两次运行。
 */
async function run(report = false) {
  // API 执行按钮入口。后端启动后台任务，waitForTask() 轮询直到 run_results.json 准备好。
  const label = report ? "API 测试、AI 分析与报告生成" : "API 测试";
  if (state.apiRunActive) return chat("API 测试仍在执行中，请等待当前 API 任务完成。", "error");
  setParallelRunBusy("api", true);
  try {
    if (!state.project_id) await saveProject();
    const manualReview = report ? await saveManualReview(false) : {items: [], notes: ""};
    if (!report) state.manualReview = {};
    prepareRunStart(label);
    $("runLog").textContent = "执行中，请稍等...\n";
    $("runNotice").className = "notice";
    $("runNotice").textContent = "执行中，请稍等。执行结果会整理成表格，原始日志会放在下方折叠区。";
    const task = await api(`/api/projects/${state.project_id}/run`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({report: false, manual_review: manualReview}),
    });
    const result = await waitForTask(task, "runNotice", "API 测试");
    $("runLog").textContent = [
      `returncode=${result.returncode}`,
      result.stdout || "",
      result.stderr || "",
    ].join("\n");
    renderRunSummary(result.run_summary || {}, result.returncode, result.has_report);
    if (report) {
      await generateReportFromCurrent(manualReview);
    }
    loadRunHistory().catch(() => {});
    updateDownloads();
    notifyFindingsChanged();
    if (!report) chat("API 测试执行完成，AI 分析已由后端执行链路自动生成。");
  } finally {
    setParallelRunBusy("api", false);
  }
}

/**
 * 前端拦截检测主链路；它与 API 测试独立，检查页面跳转、弹窗、拒绝文案和截图证据。
 */
async function runFrontend() {
  const label = "前端拦截检测";
  if (state.frontendRunActive) return chat("前端拦截检测仍在执行中，请等待当前前端任务完成。", "error");
  setParallelRunBusy("frontend", true);
  try {
    if (!state.project_id) await saveProject();
    state.frontendStartedAt = formatNow();
    state.frontendFinishedAt = "";
    $("runLog").textContent = "前端拦截检测执行中，请稍等...\n";
    $("runNotice").className = "notice";
    $("runNotice").textContent = "前端检测中：会按角色访问人工配置或由接口路径推断的页面，并采集弹窗、跳转、无权限文案和截图证据。";
    const task = await api(`/api/projects/${state.project_id}/run-frontend`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
    });
    const result = await waitForTask(task, "runNotice", "前端拦截检测", 900000);
    const frontend = ((result.run_summary || {}).frontend || {});
    const frontendHint = result.frontend_diagnostics?.note || frontend.note || result.frontend_diagnostics?.setup_error || "";
    $("runLog").textContent = [
      `returncode=${result.returncode}`,
      frontendHint,
      result.frontend_diagnostics ? JSON.stringify(result.frontend_diagnostics, null, 2) : "",
      result.stdout || "",
      result.stderr || "",
    ].join("\n");
    renderRunSummary(result.run_summary || {}, result.returncode, false);
    if (Number(result.returncode) !== 0 && frontendHint) {
      $("runNotice").className = "notice warn";
      $("runNotice").textContent = `${tr("frontend.notActuallyExecuted", "前端拦截检测未真正执行：")}${frontendHint}`;
    }
    updateDownloads();
    chat(result.returncode === 0 ? "前端拦截检测完成" : `${tr("frontend.notActuallyExecuted", "前端拦截检测未真正执行：")}${frontendHint || tr("frontend.checkResultHint", "请查看结果提示")}`);
  } finally {
    setParallelRunBusy("frontend", false);
  }
}

/**
 * 页面唯一启动入口：恢复本页记录 -> 拉取服务端 bootstrap -> 初始化配置和用户
 * -> 绑定事件 -> 加载项目列表 -> 恢复步骤/滚动位置。初始化失败只展示错误，不伪造状态。
 */
