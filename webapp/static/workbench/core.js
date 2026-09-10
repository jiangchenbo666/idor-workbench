/*
 * IDOR Workbench 前端控制层。
 *
 * 数据流：
 * 1. index.html 提供固定的 DOM 锚点，例如 #projectName、#endpointRows、#runLog。
 * 2. 本文件把用户输入读取到内存状态 state 里。
 * 3. api() 把 JSON/FormData 发给 idor_workbench.views.api 里的 FastAPI 接口。
 * 4. 后端返回项目、计划、执行结果后，renderXxx() 函数再按 state 重绘页面。
 *
 * 当前是原生浏览器前端，不是 Vue/React。核心模式是：
 * DOM 事件 -> 修改 state -> 必要时请求后端 -> 把 state 渲染回 HTML。
 */
const state = {
  // 项目编辑状态。collectProject() 会把这些值序列化后提交到 /api/projects。
  project_id: "",
 endpoints: [],
  pageRoutes: [],
 scenarios: [],
  plan: null,
  projects: [],
  testcases: null,
  sourceFiles: [],

  // 按角色导入的临时状态。某个角色上传的 HAR 会成为该角色“观察到接口”的证据。
  activeImportRole: "",
  roleImportPreview: [],
  manualRecordingActive: false,

  // 分页/过滤状态。它们只影响前端显示，不会保存为项目配置。
  endpointPage: 1,
  endpointPageSize: "auto",
  endpointSectionFilter: "",
  planPage: 1,
  planPageSize: 20,
  planModsExpanded: false,
  planModsPage: 1,
  planModsPageSize: 10,
  activePlanCategory: "allowed",
  activePlanRole: "",

  // 后端返回的 AI 分析、执行结果、执行历史等状态。
  aiPlanInsights: null,
  aiRunInsights: null,
  aiPlanExpanded: false,
  aiRunExpanded: false,
  manualReview: {},
  runResults: [],
  runHistory: [],
  compareRuns: {left: null, right: null},
  selectedHistoryRunId: "",
  compareDiffOnly: false,
  compareSearch: {left: "", right: ""},
  frontendResults: [],
  scenarioResults: {},
  assertionProfile: {},
  runSummary: {},
  hasReport: false,
  runPage: 1,
  runPageSize: "auto",
  activeResultView: "api",
  runStartedAt: "",
  runFinishedAt: "",
  frontendStartedAt: "",
  frontendFinishedAt: "",
  apiRunActive: false,
  frontendRunActive: false,
  user: null,
  aiSettings: {provider: "server"},

  // 页面导航状态和未保存修改保护。
  activeStep: "history",
  isDirty: false,
  planStale: false,
  restoring: false,
};

const $ = (id) => document.getElementById(id);
const tr = (key, fallback) => (window.t ? window.t(key, fallback) : fallback);
const AI_SETTINGS_KEY = "idorWorkbenchAiSettings";
const ACTIVE_STEP_KEY = "idorWorkbenchActiveStep";
const SCROLL_Y_KEY = "idorWorkbenchScrollY";
const RECORD_SESSION_KEY = "idorWorkbenchSessionRecords";
const UNCAPTURED_SECTION = "其他（无上层索引/未抓取到）";
let pendingStepChangeResolve = null;
const DEFAULT_HINTS = {
  timeout: "10",
  loginType: "WEB",
  tokenPath: "tokenInfo.accessToken",
  frontendTimeout: "8000",
  frontendWait: "1200",
};

function bindDefaultHint(input, defaultValue) {
  // 模板默认值是真实 value，不是 placeholder；这里给它加浅色样式，避免用户误以为是已确认配置。
  if (!input) return;
  input.dataset.defaultHint = defaultValue;
  const sync = () => input.classList.toggle("defaultHintValue", String(input.value || "") === defaultValue);
  input.addEventListener("input", sync);
  input.addEventListener("change", sync);
  sync();
}

function applyDefaultHints() {
  Object.entries(DEFAULT_HINTS).forEach(([id, value]) => bindDefaultHint($(id), value));
  document.querySelectorAll(".roleLogin").forEach(input => bindDefaultHint(input, "WEB"));
}

function clearDefaultHints() {
  document.querySelectorAll(".defaultHintValue").forEach(input => input.classList.remove("defaultHintValue"));
}

function escapeHtml(value) {
  // 任何要塞进 innerHTML 的用户/接口数据都应先经过这里，避免破坏页面结构。
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function escapeCssValue(value) {
  if (window.CSS && typeof window.CSS.escape === "function") return window.CSS.escape(String(value ?? ""));
  return String(value ?? "").replaceAll("\\", "\\\\").replaceAll('"', '\\"');
}

function prettyJson(value) {
  if (value === undefined || value === null || value === "") return "";
  if (typeof value === "string") {
    try {
      return JSON.stringify(JSON.parse(value), null, 2);
    } catch {
      return value;
    }
  }
  return JSON.stringify(value, null, 2);
}

function hasDetailValue(value) {
  if (value === undefined || value === null) return false;
  if (typeof value === "string") {
    const text = value.trim();
    return Boolean(text && text !== "{}" && text !== "[object Object]");
  }
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === "object") return Object.keys(value).length > 0;
  return true;
}

function runResultKey(row, index) {
  return [
    index,
    row?.role || "",
    row?.section || "",
    row?.status || "",
    row?.method || "",
    row?.path || "",
    row?.http ?? "",
  ].join("|");
}

function shellQuote(value) {
  return `'${String(value ?? "").replaceAll("'", "'\"'\"'")}'`;
}

function urlWithParams(url, params = {}) {
  if (!params || typeof params !== "object" || Array.isArray(params) || !Object.keys(params).length) return url;
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (Array.isArray(value)) {
      value.forEach(item => query.append(key, item));
    } else if (value !== undefined && value !== null) {
      query.append(key, value);
    }
  });
  const glue = String(url || "").includes("?") ? "&" : "?";
  const qs = query.toString();
  return qs ? `${url}${glue}${qs}` : url;
}

function frontendScreenshotUrl(path) {
  if (!state.project_id || !path) return "";
  const name = String(path).split(/[\\/]/).filter(Boolean).pop();
  if (!name || !name.toLowerCase().endsWith(".png")) return "";
  return `/api/projects/${encodeURIComponent(state.project_id)}/screenshots/${encodeURIComponent(name)}`;
}

function buildCurlCommand(row) {
  // 结果详情弹窗用它生成可复现请求，方便人工复核或后续提缺陷。
  const replay = row?.replay || {};
  const method = replay.method || row?.method || "GET";
  const url = urlWithParams(replay.url || row?.path || "", replay.params || {});
  const parts = ["curl", "-k", "-X", method, shellQuote(url)];
  const headers = replay.headers || {};
  Object.entries(headers).forEach(([key, value]) => {
    parts.push("-H", shellQuote(`${key}: ${value}`));
  });
  const body = prettyJson(replay.body);
  if (body) parts.push("--data-raw", shellQuote(body));
  return parts.join(" ");
}

function updateManualReviewCount() {
  const items = Object.values(state.manualReview || {});
  const count = items.length;
  if ($("manualReviewCount")) $("manualReviewCount").textContent = `已选 ${count} 条人工复核`;
  // 实时刷新复核清单面板
  if ($("reviewListPanel")) {
    if (count === 0) {
      $("reviewListPanel").innerHTML = "";
      $("reviewListPanel").hidden = true;
      if ($("toggleReviewList")) { $("toggleReviewList").style.display = "none"; $("toggleReviewList").textContent = "展开清单"; }
    } else {
      if ($("toggleReviewList")) $("toggleReviewList").style.display = "";
      $("reviewListPanel").innerHTML = `
        <table class="reviewTable">
          <thead><tr><th>#</th><th>角色</th><th>接口</th><th>状态</th><th>断言</th><th>操作</th></tr></thead>
          <tbody>
          ${items.map((item, i) => `
            <tr>
              <td>${i + 1}</td>
              <td>${escapeHtml(item.role || "")}</td>
              <td><div class="runPath">${escapeHtml(item.method || "")} ${escapeHtml(item.path || "")}</div></td>
              <td>${item.http || ""}</td>
              <td>${escapeHtml(item.verdict || "")}</td>
              <td><button type="button" class="miniBtn removeReview" data-idx="${i}">移除</button></td>
            </tr>
          `).join("")}
          </tbody>
        </table>`;
      $("reviewListPanel").hidden = false;
      // 绑定移除按钮
      $("reviewListPanel").querySelectorAll(".removeReview").forEach(btn => {
        btn.onclick = () => {
          const idx = Number(btn.dataset.idx);
          const keys = Object.keys(state.manualReview);
          if (keys[idx]) {
            delete state.manualReview[keys[idx]];
            updateManualReviewCount();
            renderRunPage();  // 同步取消勾选
          }
        };
      });
    }
  }
}

function markDirty() {
  // 配置变化只标记浏览器草稿；真正持久化必须经过 saveProject() 的后端确认。
  // 标记“当前配置已修改”。页面切换和刷新离开前会根据它提示保存。
  if (state.restoring) return;
  state.isDirty = true;
}

function markPlanStale(reason = "接口池已修改") {
  // 角色/接口/目标变化后旧计划不再可信，强制提示用户重新生成而不是静默沿用。
  if (state.restoring || !state.plan) return;
  state.planStale = true;
  const status = $("planActionStatus");
  if (status) status.textContent = `${reason}，当前执行计划可能不是最新，请重新生成计划并 AI 审查。`;
}

function markClean() {
  state.isDirty = false;
}

function persistViewState() {
  // 刷新后仍停留在当前步骤和滚动位置，而不是跳回历史项目页。
  sessionStorage.setItem(ACTIVE_STEP_KEY, state.activeStep || "history");
  sessionStorage.setItem(SCROLL_Y_KEY, String(window.scrollY || 0));
}

function shouldIgnoreDirtyEvent(target) {
  if (!target || !target.id) return false;
  return /Search$|PageSize$|PageJump$|StatusFilter$/.test(target.id)
    || target.id.startsWith("go")
    || target.id.startsWith("prev")
    || target.id.startsWith("next");
}

function bindDirtyTracking() {
  document.querySelectorAll('[data-panel="0"], [data-panel="1"]').forEach(panel => {
    panel.addEventListener("input", event => {
      if (!shouldIgnoreDirtyEvent(event.target)) markDirty();
    });
    panel.addEventListener("change", event => {
      if (!shouldIgnoreDirtyEvent(event.target)) markDirty();
    });
  });
  window.addEventListener("beforeunload", event => {
    persistViewState();
    if (!state.isDirty) return;
    event.preventDefault();
    event.returnValue = "";
  });
  window.addEventListener("pagehide", persistViewState);
  let scrollTimer = null;
  window.addEventListener("scroll", () => {
    if (scrollTimer) window.clearTimeout(scrollTimer);
    scrollTimer = window.setTimeout(persistViewState, 120);
  }, {passive: true});
}

function restoreSavedView() {
  const savedStep = sessionStorage.getItem(ACTIVE_STEP_KEY) || "history";
  const targetStep = document.querySelector(`.panel[data-panel="${escapeCssValue(savedStep)}"]`) ? savedStep : "history";
  setStep(targetStep);
  const savedScroll = Number(sessionStorage.getItem(SCROLL_Y_KEY) || 0);
  window.setTimeout(() => window.scrollTo({top: savedScroll, behavior: "auto"}), 80);
}

function autoRunPageSize() {
  const pane = document.querySelector(".runTableWrap");
  const height = pane ? pane.getBoundingClientRect().height : window.innerHeight * 0.48;
  const usable = Math.max(260, height - 86);
  return Math.max(8, Math.min(18, Math.floor(usable / 48)));
}

function effectiveRunPageSize() {
  return state.runPageSize === "auto" ? autoRunPageSize() : Number(state.runPageSize || 10);
}

function autoPageSizeFor(tbodyId, rowHeight = 48, min = 8, max = 18) {
  const body = $(tbodyId);
  const top = body ? body.getBoundingClientRect().top : window.innerHeight * 0.45;
  const usable = Math.max(240, window.innerHeight - top - 92);
  return Math.max(min, Math.min(max, Math.floor(usable / rowHeight)));
}

function effectiveEndpointPageSize() {
  return state.endpointPageSize === "auto" ? autoPageSizeFor("endpointRows", 48, 8, 16) : Number(state.endpointPageSize || 10);
}

function effectivePlanPageSize() {
  return Number(state.planPageSize || 20);
}

function syncPageJump(inputId, currentPage, totalPages) {
  const input = $(inputId);
  if (!input) return;
  input.max = String(totalPages);
  input.value = String(currentPage);
  input.title = `可输入 1-${totalPages} 页`;
}

function bindPageJump(inputId, buttonId, pageKey, renderFn) {
  const go = () => {
    const input = $(inputId);
    const total = Math.max(1, Number(input.max || 1));
    const target = Math.min(Math.max(1, Number(input.value || 1)), total);
    state[pageKey] = target;
    input.value = String(target);
    renderFn();
  };
  $(buttonId).onclick = go;
  $(inputId).onkeydown = (event) => {
    if (event.key === "Enter") go();
  };
  $(inputId).onblur = () => {
    if (!$(inputId).value) $(inputId).value = String(state[pageKey] || 1);
  };
}

function formatNow() {
  const now = new Date();
  const pad = value => String(value).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
}

function latestRunTimeText() {
  if (state.runFinishedAt) return `最新执行：${state.runFinishedAt}`;
  if (state.runStartedAt) return `执行中：${state.runStartedAt}`;
  return "最新执行：尚未执行";
}

function updateRunTimeLabels() {
  document.querySelectorAll("[data-run-time]").forEach(node => {
    node.textContent = latestRunTimeText();
  });
}

function updateStepLabels() {
  let index = 1;
  document.querySelectorAll(".step").forEach(btn => {
    const title = btn.dataset.title || btn.textContent.trim();
    if (btn.dataset.step === "history") {
      btn.textContent = title;
      return;
    }
    btn.textContent = `${index} ${title}`;
    index += 1;
  });
  return index - 1;
}

function visibleResultViews() {
  return ["api", "roleLog", "scenario", "frontend", "profile", "artifacts", "history"];
}

function normalizeResultView(view = state.activeResultView) {
  const views = visibleResultViews();
  return views.includes(view) ? view : views[0];
}

function appendRecord(boxId, text, who = "agent") {
  const box = document.createElement("div");
  box.className = `msg ${who}`;
  box.textContent = text;
  $(boxId).appendChild(box);
  $(boxId).scrollTop = $(boxId).scrollHeight;
  persistSessionRecords();
}

function chat(text, who = "agent") {
  appendRecord("chat", text, who);
}

function toolLog(text, who = "tool") {
  appendRecord("toolLog", text, who);
}

function clearToolLog() {
  if ($("toolLog")) $("toolLog").innerHTML = "";
  persistSessionRecords();
}

function persistSessionRecords() {
  const payload = {
    chat: $("chat") ? $("chat").innerHTML : "",
    toolLog: $("toolLog") ? $("toolLog").innerHTML : "",
    saved_at: Date.now(),
  };
  sessionStorage.setItem(RECORD_SESSION_KEY, JSON.stringify(payload));
}

function restoreSessionRecords() {
  try {
    const payload = JSON.parse(sessionStorage.getItem(RECORD_SESSION_KEY) || "{}");
    if (payload.chat && $("chat")) $("chat").innerHTML = payload.chat;
    if (payload.toolLog && $("toolLog")) $("toolLog").innerHTML = payload.toolLog;
  } catch {
    sessionStorage.removeItem(RECORD_SESSION_KEY);
  }
}

function validateProjectBeforeSave(project) {
  const missing = [];
  if (!project.project_name) missing.push("项目名称");
  if (!project.base_url) missing.push("被测环境地址");
  if (missing.length) {
    const message = `请先填写${missing.join("、")}后再保存项目。`;
    chat(message, "error");
    toolLog(`保存被拦截：${message}`, "error");
    if (!project.project_name && $("projectName")) $("projectName").focus();
    else if (!project.base_url && $("baseUrl")) $("baseUrl").focus();
    return false;
  }
  if (!/^https?:\/\/[^/]+/i.test(project.base_url)) {
    const message = "被测环境地址需要填写完整，例如：http://10.50.5.3:8800";
    chat(message, "error");
    toolLog(`保存被拦截：${message}`, "error");
    if ($("baseUrl")) $("baseUrl").focus();
    return false;
  }
  return true;
}

/**
 * 将分散在表单、动态角色行和内存数组中的编辑状态组装成项目 DTO。
 * 这是浏览器配置的唯一出口；新增可持久化字段时应同时检查 fillProject() 的反向映射。
 * source_files 只携带后端签发的 source_id，服务端不会信任这里的 stored_path。
 */
function collectProject() {
  // 前端配置的统一出口：页面上可保存的项目配置都会组装进这个对象。
  // saveProject() 会把它 POST 到 /api/projects，后端再写 project.json 和 SQLite 元数据。
  return {
    project_id: state.project_id,
    project_name: $("projectName").value.trim(),
    base_url: $("baseUrl").value.trim(),
    request_timeout: Number($("timeout").value || 10),
    model_type: $("modelType").value,
    goal: $("goal").value,
    prd_text: $("prdText").value,
    testcases: state.testcases,
    auth: {
      enabled: true,
      token_url: $("tokenUrl").value.trim(),
      auth_method: $("authMethod") ? $("authMethod").value : "json_post",
      login_type: $("loginType").value.trim() || "WEB",
      username_field: $("usernameField") ? $("usernameField").value.trim() || "username" : "username",
      password_field: $("passwordField") ? $("passwordField").value.trim() || "password" : "password",
      token_path: $("tokenPath").value.trim(),
      token_mode: $("tokenMode").value,
    },
    roles: Array.from(document.querySelectorAll(".roleRow")).map(row => ({
      // 角色行是 addRole() 动态创建的 DOM；每一行最终会变成后端里的一个测试账号。
      name: row.querySelector(".roleName").value.trim(),
      username: row.querySelector(".roleUser").value.trim(),
      password: row.querySelector(".rolePass").value,
      login_type: row.querySelector(".roleLogin").value.trim() || $("loginType").value.trim() || "WEB",
    })).filter(r => r.name),
    scenarios: state.scenarios,
    frontend_checks: {
      enabled: true,
      headless: true,
      timeout_ms: Number($("frontendTimeout").value || 8000),
      wait_after_load_ms: Number($("frontendWait").value || 1200),
      token_storage_keys: $("frontendTokenKeys").value,
      denied_keywords: $("frontendDeniedKeywords").value,
   },
   endpoints: state.endpoints,
    page_routes: state.pageRoutes,
   source_files: state.sourceFiles,
  };
}

function collectAiSettings() {
  if (!$("aiSettingsModal") || $("aiSettingsModal").hidden) return state.aiSettings || {provider: "server"};
  return {
    provider: $("aiProvider").value || "openai-compatible",
    base_url: $("aiBaseUrl").value.trim(),
    model: $("aiModel").value.trim(),
    api_key: $("aiApiKey").value,
    timeout: $("aiTimeout").value || "600",
    temperature: $("aiTemperature").value || "0.2",
  };
}

function applyAiSettings(settings = {}) {
  state.aiSettings = settings || {provider: "server"};
  if ($("aiProvider")) $("aiProvider").value = settings.provider && settings.provider !== "server" ? settings.provider : "openai-compatible";
  if ($("aiBaseUrl")) $("aiBaseUrl").value = settings.base_url || "";
  if ($("aiModel")) $("aiModel").value = settings.model || "";
  if ($("aiTimeout")) $("aiTimeout").value = settings.timeout || "600";
  if ($("aiTemperature")) $("aiTemperature").value = settings.temperature || "0.2";
  if ($("aiApiKey")) $("aiApiKey").value = "";
  const label = settings.provider && settings.provider !== "server"
    ? `${settings.provider} ${settings.model || ""}${settings.has_api_key ? "（已保存 key）" : ""}`.trim()
    : "未配置，使用本地规则";
  if ($("aiConfigStatus")) $("aiConfigStatus").textContent = `当前 AI：${label}`;
}

function loadAiSettings() {
  applyAiSettings(state.aiSettings || {provider: "server"});
}

async function saveAiSettings() {
  const settings = collectAiSettings();
  const result = await api("/api/me/ai-settings", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(settings),
  });
  applyAiSettings(result.settings || settings);
  if ($("customAiStatus")) $("customAiStatus").textContent = "AI 设置已保存。";
  chat("个人 AI API 设置已保存，后续计划审查和执行分析会优先使用它。");
}

async function resetAiSettings() {
  const result = await api("/api/me/ai-settings", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({provider: "disabled", base_url: "", model: "", timeout: "600", temperature: "0.2", clear_api_key: true, api_key: ""}),
  });
  applyAiSettings(result.settings || {provider: "disabled"});
  if ($("customAiStatus")) $("customAiStatus").textContent = "AI 设置已清空；后续使用本地规则兜底，不会访问 11434。";
  chat("AI 设置已清空，后续使用本地规则兜底。");
}

async function testAiSettings() {
  const settings = collectAiSettings();
  $("testAiSettings").disabled = true;
  $("testAiSettings").textContent = "检查中...";
  $("aiConfigStatus").textContent = "";
  try {
    const result = await apiWithTimeout("/api/ai/test", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ai_config: settings}),
    }, 25000);
    $("aiConfigStatus").textContent = result.ok
      ? `AI 引擎可用：${result.provider || "server"} ${result.model || ""}`.trim()
      : `AI 引擎不可用：${result.error || "未知错误"}`;
  } catch (err) {
    $("aiConfigStatus").textContent = `AI 引擎不可用：${err.message}`;
  } finally {
    $("testAiSettings").disabled = false;
    $("testAiSettings").textContent = "检查 AI 引擎";
  }
}

function updateUserStatus(user = state.user) {
  state.user = user || null;
  if ($("userStatus")) $("userStatus").textContent = user ? `当前用户：${user.username}` : "未登录";
  if ($("logoutBtn")) $("logoutBtn").disabled = !user;
}

function openAuthModal() {
  if ($("authStatus")) $("authStatus").textContent = "";
  if ($("authConfirmPassword")) $("authConfirmPassword").value = "";
  $("authModal").hidden = false;
}

function closeAuthModal() {
  $("authModal").hidden = true;
}

async function authRequest(path) {
  const username = $("authUsername").value.trim();
  const password = $("authPassword").value;
  if (!username || !password) {
    $("authStatus").textContent = "请填写账号和密码。";
    return;
  }
  if (path === "/api/auth/register") {
    const confirmPwd = $("authConfirmPassword").value;
    if (!confirmPwd) {
      $("authStatus").textContent = "请填写确认密码。";
      return;
    }
    if (password !== confirmPwd) {
      $("authStatus").textContent = "两次输入的密码不一致，请重新输入。";
      return;
    }
  }
  try {
    const body = {username, password};
    if (path === "/api/auth/register") body.confirm_password = $("authConfirmPassword").value;
    const result = await api(path, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body),
    });
    updateUserStatus(result.user);
    if (result.ai_settings) applyAiSettings(result.ai_settings);
    $("authStatus").textContent = "登录成功。";
    closeAuthModal();
    await loadProjects();
    chat(`已登录：${result.user.username}`);
  } catch (err) {
    $("authStatus").textContent = err.message;
  }
}

async function logout() {
  await api("/api/auth/logout", {method: "POST"});
  updateUserStatus(null);
  applyAiSettings({provider: "server"});
  state.projects = [];
  await loadProjects();
  chat("已退出登录。");
}

function openAiSettingsModal() {
  if (!state.user) {
    openAuthModal();
    if ($("authStatus")) $("authStatus").textContent = "保存个人 API Key 前请先登录或注册。";
    return;
  }
  applyAiSettings(state.aiSettings || {provider: "server"});
  if ($("customAiStatus")) $("customAiStatus").textContent = "";
  $("aiSettingsModal").hidden = false;
}

function closeAiSettingsModal() {
  $("aiSettingsModal").hidden = true;
}

async function testCustomAiSettings() {
  const settings = collectAiSettings();
  if ($("customAiStatus")) $("customAiStatus").textContent = "正在检测连通性...";
  try {
    const result = await apiWithTimeout("/api/ai/test", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ai_config: settings}),
    }, 25000);
    if ($("customAiStatus")) $("customAiStatus").textContent = result.ok
      ? `连通成功：${result.provider || settings.provider} ${result.model || settings.model || ""}`.trim()
      : `连通失败：${result.error || "配置不完整"}`;
  } catch (err) {
    if ($("customAiStatus")) $("customAiStatus").textContent = `连通失败：${err.message}`;
  }
}

/**
 * 统一的 JSON API 客户端。
 * fetch 会自动携带同源 HttpOnly Cookie；这里只负责网络错误和非 2xx 规范化，
 * 具体业务函数决定如何向用户展示错误，不能在这里把 401/404 转成“成功响应”。
 */
async function api(path, options = {}) {
  let res;
  try {
    res = await fetch(path, options);
  } catch (err) {
    throw new Error(`网络请求失败（${err.message || "无法连接到后端服务"}），请确认服务是否启动。`);
  }
  if (!res.ok) {
    let detail = await res.text().catch(() => "");
    if (!detail) detail = res.statusText;
    throw new Error(detail || `请求失败 (HTTP ${res.status})`);
  }
  return res.json();
}

/**
 * 新 Vue 功能区与既有工作台的最小桥接层。
 * Vue 不直接读取全局变量，而是只通过这三个稳定入口读取项目 ID、发请求和接收刷新事件。
 */
function notifyFindingsChanged() {
  window.dispatchEvent(new CustomEvent("idor:findings-changed", {
    detail: {projectId: state.project_id},
  }));
}

window.IDORWorkbench = {
  getProjectId: () => state.project_id,
  request: api,
  refreshFindings: notifyFindingsChanged,
};

/**
 * 轮询后端长任务状态机。任务完成时返回 result，失败或超时则抛错。
 * 后端任务必须最终进入 done/failed，否则 UI 会一直停留在执行中。
 */
async function waitForTask(task, statusId = "runNotice", label = "后台任务", maxWaitMs = 120000) {
  // 长任务后端会先返回 task_id；前端轮询 /api/tasks/{id}，直到后台线程完成并返回结果/日志。
  const taskId = task.task_id;
  if (!taskId) return task;
  const startedAt = Date.now();
  let elapsed = 0;
  toolLog(`${label}已入队：${taskId}`);
  while (true) {
    const current = await api(`/api/tasks/${taskId}`);
    const statusText = `${label}${current.status === "queued" ? "排队中" : "执行中"}，已等待 ${elapsed}s`;
    if (statusId && $(statusId) && !["done", "failed"].includes(current.status)) {
      $(statusId).textContent = statusText;
    }
    if (current.log_tail && $("runLog")) {
      $("runLog").textContent = current.log_tail;
    }
    if (current.status === "done") {
      toolLog(`${label}已完成。`);
      return current.result || {};
    }
    if (current.status === "failed") {
      toolLog(`${label}失败：${current.error || current.message || "未知错误"}`, "error");
      throw new Error(current.error || current.message || "后台任务失败");
    }
    if (Date.now() - startedAt >= maxWaitMs) {
      const message = `${label}超过 ${Math.round(maxWaitMs / 1000)} 秒仍未完成，已停止等待。请稍后查看执行历史或检查后端任务日志。`;
      if (statusId && $(statusId)) {
        $(statusId).className = "notice warn";
        $(statusId).textContent = message;
      }
      toolLog(message, "error");
      throw new Error(message);
    }
    await new Promise(resolve => window.setTimeout(resolve, 1500));
    elapsed += 1.5;
  }
}

async function apiWithTimeout(path, options = {}, timeoutMs = 10000) {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await api(path, {...options, signal: controller.signal});
  } catch (err) {
    if (err.name === "AbortError") throw new Error(`请求超过 ${Math.round(timeoutMs / 1000)} 秒，已停止等待`);
    throw err;
  } finally {
    window.clearTimeout(timer);
  }
}

function setStep(index) {
  // 只负责切换显示哪个面板；是否先保存由 requestStepChange() 处理。
  const stepKey = String(index);
  state.activeStep = stepKey;
  sessionStorage.setItem(ACTIVE_STEP_KEY, stepKey);
  document.querySelectorAll(".step").forEach(btn => btn.classList.toggle("active", btn.dataset.step === stepKey));
  document.querySelectorAll(".panel").forEach(panel => panel.classList.toggle("visible", panel.dataset.panel === stepKey));
  if (stepKey === "history") loadProjects();
  if (stepKey === "1") {
    renderManualRecordingRoles();
    applyDefaultHints();
    renderEndpoints();
  }
  if (stepKey === "2") renderPlanPage();
  if (stepKey === "3") renderRunPage();
}

function showUnsavedStepModal() {
  const modal = $("unsavedModal");
  if (!modal) return Promise.resolve("discard");
  modal.hidden = false;
  return new Promise(resolve => {
    pendingStepChangeResolve = resolve;
    const finish = (action) => {
      modal.hidden = true;
      pendingStepChangeResolve = null;
      resolve(action);
    };
    $("confirmSaveStepChange").onclick = () => {
      finish("save");
    };
    $("discardStepChange").onclick = () => {
      finish("discard");
    };
    modal.onclick = event => {
      if (event.target === modal) finish("discard");
    };
  });
}

async function requestStepChange(index) {
  // 导航前先保护未保存草稿；用户确认保存后再切换，避免页面重绘覆盖编辑内容。
  const stepKey = String(index);
  if (stepKey === state.activeStep) return;
  if (state.isDirty) {
    const action = await showUnsavedStepModal();
    if (action === "save") {
      try {
        await saveProject({refreshPlan: false});
      } catch (err) {
        chat(`保存失败，已停留在当前页面：${err.message}`);
        return;
      }
    } else {
      markClean();
    }
  }
  setStep(stepKey);
  window.scrollTo({top: 0, behavior: "smooth"});
}

