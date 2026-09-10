async function bootstrap() {
  // 页面首次加载入口。通过 /api/bootstrap 读取运行时默认值，再渲染初始工作台状态。
  try {
    restoreSessionRecords();
    const data = await api("/api/bootstrap");
    updateUserStatus(data.user || null);
    applyAiSettings(data.ai_settings || {provider: "server"});
    const cfg = data.runtime_config || {};
    $("baseUrl").value = cfg.base_url || "";
    $("tokenUrl").value = cfg.token_url || "";
    if ($("authMethod")) $("authMethod").value = cfg.auth_method || "json_post";
    $("loginType").value = cfg.token_login_type || "WEB";
    $("timeout").value = cfg.request_timeout || 10;
    Object.entries(cfg.accounts || {}).forEach(([name, acc]) => {
      addRole({name, ...acc, username: acc.username || "", password: ""});
    });
    state.endpoints = (cfg.endpoints || []).map(ep => ({
      ...ep,
      risk: ep.risk || "中",
      page_url: ep.page_url || "",
      page_url_inferred: Boolean(ep.page_url_inferred),
    }));
    applyDefaultHints();
    renderEndpoints();
    renderResultCards();
    renderScenarioResults({});
    renderFrontendResults({});
    renderAssertionProfile({});
    loadProjects().catch(() => {});
    if (!$("chat").innerHTML.trim()) chat("工作台初始化完成");
  } catch (err) {
    chat(`初始化失败：${err.message}`);
    addRole({name: "角色A"});
    addRole({name: "角色B"});
  }
}

document.querySelectorAll(".step").forEach(btn => btn.onclick = () => requestStepChange(btn.dataset.step));
document.querySelectorAll("[data-step-jump]").forEach(btn => {
  btn.onclick = () => requestStepChange(btn.dataset.stepJump);
});
$("refreshProjects").onclick = loadProjects;
if ($("newProject")) $("newProject").onclick = newProject;
$("projectSearch").oninput = loadProjects;
$("addRole").onclick = () => { addRole(); markDirty(); };
$("addScenario").onclick = () => { addScenario(); markDirty(); };
$("addEndpoint").onclick = () => { addEndpoint(); markDirty(); };
if ($("addPageRoute")) $("addPageRoute").onclick = () => addPageRoute();
$("saveBtn").onclick = () => saveProject({refreshPlan: true}).catch(err => chat(`保存项目失败：${err.message}`));
if ($("saveAiSettings")) $("saveAiSettings").onclick = saveAiSettings;
if ($("testAiSettings")) $("testAiSettings").onclick = testAiSettings;
if ($("resetAiSettings")) $("resetAiSettings").onclick = resetAiSettings;
if ($("openAuthModal")) $("openAuthModal").onclick = openAuthModal;
if ($("closeAuthModal")) $("closeAuthModal").onclick = closeAuthModal;
if ($("authModal")) $("authModal").onclick = (event) => {
  if (event.target === $("authModal")) closeAuthModal();
};
if ($("loginBtn")) $("loginBtn").onclick = () => authRequest("/api/auth/login");
if ($("registerBtn")) $("registerBtn").onclick = () => authRequest("/api/auth/register");
document.querySelectorAll(".eyeToggle").forEach(btn => {
  btn.addEventListener("click", () => {
    const target = $(btn.dataset.target);
    if (!target) return;
    target.type = target.type === "password" ? "text" : "password";
    btn.textContent = target.type === "password" ? "👁" : "🙈";
  });
});
if ($("logoutBtn")) $("logoutBtn").onclick = () => logout().catch(err => chat(`退出登录失败：${err.message}`, "error"));
if ($("openAiSettings")) $("openAiSettings").onclick = openAiSettingsModal;
if ($("closeAiSettings")) $("closeAiSettings").onclick = closeAiSettingsModal;
if ($("aiSettingsModal")) $("aiSettingsModal").onclick = (event) => {
  if (event.target === $("aiSettingsModal")) closeAiSettingsModal();
};
if ($("saveCustomAi")) $("saveCustomAi").onclick = () => saveAiSettings().catch(err => {
  if ($("customAiStatus")) $("customAiStatus").textContent = `保存失败：${err.message}`;
});
if ($("testCustomAi")) $("testCustomAi").onclick = () => testCustomAiSettings();
if ($("useServerAi")) $("useServerAi").onclick = () => resetAiSettings().catch(err => {
  if ($("customAiStatus")) $("customAiStatus").textContent = `切换失败：${err.message}`;
});
if ($("toggleAiKey")) {
  $("toggleAiKey").onclick = () => {
    const input = $("aiApiKey");
    input.type = input.type === "password" ? "text" : "password";
  };
}
$("makePlan").onclick = () => makePlanWithFeedback().catch(err => {
  if ($("planActionStatus")) $("planActionStatus").textContent = `执行计划生成失败：${err.message}`;
  chat(`执行计划生成失败：${err.message}`);
});
if ($("aiAnalyzePlan")) $("aiAnalyzePlan").onclick = () => makePlanWithFeedback().catch(err => chat(`AI 生成计划失败：${err.message}`));
if ($("savePlanEdits")) $("savePlanEdits").onclick = async () => {
  if (!state.project_id || !state.plan) return chat("请先生成并保存项目计划。", "error");
  const result = await api(`/api/projects/${state.project_id}/plan`, {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({plan: state.plan}),
  });
  state.isDirty = false;
  chat(`计划调整已保存：${result.cases || 0} 条计划项。`);
};
$("endpointPageSize").onchange = () => {
  const value = $("endpointPageSize").value;
  state.endpointPageSize = value === "auto" ? "auto" : Number(value || 10);
  state.endpointPage = 1;
  renderEndpoints();
};
// --- 计划页分页 ---
if ($("planPageSizeSel")) $("planPageSizeSel").onchange = () => {
  state.planPageSize = Number($("planPageSizeSel").value || 20);
  state.planPage = 1;
  renderPlanPage();
};
if ($("prevPlanPage")) $("prevPlanPage").onclick = () => { state.planPage -= 1; renderPlanPage(); };
if ($("nextPlanPage")) $("nextPlanPage").onclick = () => { state.planPage += 1; renderPlanPage(); };
bindPageJump("planPageJump", "goPlanPage", "planPage", renderPlanPage);
$("endpointSearch").oninput = () => {
  state.endpointPage = 1;
  renderEndpoints();
};
$("prevEndpointPage").onclick = () => {
  state.endpointPage -= 1;
  renderEndpoints();
};
$("nextEndpointPage").onclick = () => {
  state.endpointPage += 1;
  renderEndpoints();
};
bindPageJump("endpointPageJump", "goEndpointPage", "endpointPage", renderEndpoints);
$("planSearch").oninput = () => {
  state.planPage = 1;
  renderPlanPage();
};
$("runPageSize").onchange = () => {
  const value = $("runPageSize").value;
  state.runPageSize = value === "auto" ? "auto" : Number(value || 10);
  state.runPage = 1;
  renderRunPage();
};
$("apiResultSearch").oninput = () => {
  state.runPage = 1;
  renderRunPage();
};
$("apiStatusFilter").onchange = () => {
  state.runPage = 1;
  renderRunPage();
};
$("frontendResultSearch").oninput = () => renderFrontendResults((state.runSummary || {}).frontend || {});
$("frontendStatusFilter").onchange = () => renderFrontendResults((state.runSummary || {}).frontend || {});
$("scenarioResultSearch").oninput = () => renderScenarioResults((state.runSummary || {}).scenario || {});
$("scenarioStatusFilter").onchange = () => renderScenarioResults((state.runSummary || {}).scenario || {});
$("roleLogSearch").oninput = () => renderPrettyRunLog(state.runSummary || {});
$("runHistorySearch").oninput = renderRunHistory;
if ($("compareLeftSearch")) $("compareLeftSearch").oninput = event => {
  state.compareSearch.left = event.target.value;
  renderRunCompare();
};
if ($("compareRightSearch")) $("compareRightSearch").oninput = event => {
  state.compareSearch.right = event.target.value;
  renderRunCompare();
};
$("refreshRunHistory").onclick = loadRunHistory;
if ($("toggleCompareDiff")) $("toggleCompareDiff").onclick = () => {
  state.compareDiffOnly = !state.compareDiffOnly;
  renderRunCompare();
};
if ($("aiAnalyzeRun")) $("aiAnalyzeRun").onclick = () => analyzeRunWithAi().catch(err => {
  $("aiRunStatus").textContent = `AI 分析失败：${err.message}`;
  chat(`AI 分析失败：${err.message}`);
});
if ($("openAiRunReport")) $("openAiRunReport").onclick = openAiRunReport;
if ($("saveManualReview")) $("saveManualReview").onclick = () => saveManualReview(true).catch(err => chat(`保存复核清单失败：${err.message}`));
if ($("toggleReviewList")) {
  state.reviewListExpanded = false;
  $("toggleReviewList").onclick = () => {
    state.reviewListExpanded = !state.reviewListExpanded;
    if ($("reviewListPanel")) $("reviewListPanel").hidden = !state.reviewListExpanded;
    $("toggleReviewList").textContent = state.reviewListExpanded ? "收起清单" : "展开清单";
  };
}
if ($("closeResultDetail")) $("closeResultDetail").onclick = closeReplayDetail;
if ($("resultDetailModal")) $("resultDetailModal").onclick = (event) => {
  if (event.target === $("resultDetailModal")) closeReplayDetail();
};
if ($("closeAiRunReport")) $("closeAiRunReport").onclick = closeAiRunReport;
if ($("aiRunReportModal")) $("aiRunReportModal").onclick = (event) => {
  if (event.target === $("aiRunReportModal")) closeAiRunReport();
};
$("prevRunPage").onclick = () => {
  state.runPage -= 1;
  renderRunPage();
};
$("nextRunPage").onclick = () => {
  state.runPage += 1;
  renderRunPage();
};
bindPageJump("runPageJump", "goRunPage", "runPage", renderRunPage);
$("importHar").onclick = () => importFile("har", "harFile");
$("importCollection").onclick = () => importFile("collection", "collectionFile");
$("importCurl").onclick = () => importCurl();
if ($("closeRoleImport")) $("closeRoleImport").onclick = closeRoleImport;
if ($("roleImportModal")) $("roleImportModal").onclick = (event) => {
  if (event.target === $("roleImportModal")) closeRoleImport();
};
if ($("roleImportSearch")) $("roleImportSearch").oninput = renderRoleImportPreview;
if ($("roleHarFile")) $("roleHarFile").onchange = updateImportReadyStates;
if ($("roleCollectionFile")) $("roleCollectionFile").onchange = updateImportReadyStates;
if ($("roleCurlText")) $("roleCurlText").oninput = updateImportReadyStates;
if ($("roleImportHar")) $("roleImportHar").onclick = () => importFile("har", "roleHarFile", state.activeImportRole);
if ($("roleImportCollection")) $("roleImportCollection").onclick = () => importFile("collection", "roleCollectionFile", state.activeImportRole);
if ($("roleImportCurl")) $("roleImportCurl").onclick = () => importCurl(state.activeImportRole);
if ($("prdFile")) $("prdFile").onchange = () => importFile("prd", "prdFile").catch(err => chat(`导入 PRD 失败：${err.message}`));
if ($("testcaseFile")) $("testcaseFile").onchange = () => importFile("testcase", "testcaseFile").catch(err => chat(`导入测试用例失败：${err.message}`));
["harFile", "collectionFile"].forEach(id => {
  if ($(id)) $(id).onchange = updateImportReadyStates;
});
if ($("curlText")) $("curlText").oninput = updateImportReadyStates;
if ($("startManualRecording")) $("startManualRecording").onclick = startManualRecording;
if ($("stopManualRecording")) $("stopManualRecording").onclick = stopManualRecording;
$("closeImportHelp").onclick = hideImportHelp;
$("importHelpModal").onclick = (event) => {
  if (event.target === $("importHelpModal")) hideImportHelp();
};
document.querySelectorAll(".helpDot").forEach(btn => {
  btn.onclick = () => showImportHelp(btn.dataset.help);
});
$("runApi").onclick = () => run(false);
$("runFrontend").onclick = runFrontend;
$("runReport").onclick = () => run(true);
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  if ($("unsavedModal") && !$("unsavedModal").hidden) {
    $("unsavedModal").hidden = true;
    if (pendingStepChangeResolve) {
      pendingStepChangeResolve("discard");
      pendingStepChangeResolve = null;
    }
  }
  if (!$("importHelpModal").hidden) hideImportHelp();
  if ($("resultDetailModal") && !$("resultDetailModal").hidden) closeReplayDetail();
  if ($("aiRunReportModal") && !$("aiRunReportModal").hidden) closeAiRunReport();
  if ($("authModal") && !$("authModal").hidden) closeAuthModal();
  if ($("aiSettingsModal") && !$("aiSettingsModal").hidden) closeAiSettingsModal();
});
window.addEventListener("resize", () => {
  if (state.runPageSize === "auto" && state.activeResultView === "api") {
    renderRunPage();
  }
  renderPlanPage();
  if (state.endpointPageSize === "auto") renderEndpoints();
});

applyMode();
bindDirtyTracking();
updateImportReadyStates();
bootstrap().finally(() => {
  markClean();
  restoreSavedView();
});
