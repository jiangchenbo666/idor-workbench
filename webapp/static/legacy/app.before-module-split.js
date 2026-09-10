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

function applyMode() {
  updateStepLabels();
  state.activeResultView = normalizeResultView(state.activeResultView);
  renderResultCards();
  setResultView(state.activeResultView);
  updateRunTimeLabels();
}

function addRole(role = {}) {
  // 新增一行可编辑角色；collectProject() 后续会从 .roleName/.roleUser/.rolePass 读取它。
  const row = document.createElement("div");
  row.className = "roleRow";
  row.innerHTML = `
    <input class="roleName" placeholder="角色名称" value="${role.name || ""}">
    <input class="roleUser" placeholder="用户名" value="${role.username || ""}">
    <div class="passWrap">
      <input class="rolePass" placeholder="密码/密钥" type="password" value="${role.password || ""}">
      <button class="iconBtn togglePass" type="button" aria-label="显示密码" title="显示/隐藏密码">
        <svg viewBox="0 0 24 24"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"></path><circle cx="12" cy="12" r="3"></circle></svg>
      </button>
    </div>
    <input class="roleLogin" placeholder="loginType" value="${role.login_type || $("loginType").value || "WEB"}">
    <button class="deleteRole" type="button">删除</button>`;
  row.querySelector(".deleteRole").onclick = () => {
   row.remove();
   renderManualRecordingRoles();
   renderRoleImportStrip();
    renderPageRoutes();
  };
  row.querySelector(".togglePass").onclick = () => {
    const input = row.querySelector(".rolePass");
    input.type = input.type === "password" ? "text" : "password";
  };
  row.querySelector(".roleName").oninput = () => {
   renderManualRecordingRoles();
   renderRoleImportStrip();
    renderPageRoutes();
  };
  bindDefaultHint(row.querySelector(".roleLogin"), "WEB");
  $("roles").appendChild(row);
 renderManualRecordingRoles();
 renderRoleImportStrip();
  renderPageRoutes();
 renderScenarios();
}

function scenarioId() {
  return `scenario-${Date.now()}-${Math.random().toString(16).slice(2, 7)}`;
}

function endpointSearchOptions(query = "") {
  const needle = query.trim().toLowerCase();
  return state.endpoints.filter(ep => !needle || [ep.name, ep.path, ep.method, ep.module]
    .join(" ").toLowerCase().includes(needle));
}

function addScenario(scenario = {}) {
  state.scenarios.push({
    id: scenario.id || scenarioId(),
    name: scenario.name || "新业务场景",
    actor_role: scenario.actor_role || "",
    enabled: scenario.enabled !== false,
    allow_write: Boolean(scenario.allow_write),
    steps: (scenario.steps || []).map(step => ({
      id: step.id || scenarioId(), name: step.name || "", path: step.path || "",
      method: step.method || "GET", params: step.params || {}, body: step.body ?? null,
      headers: step.headers || {}, role: step.role || "", purpose: step.purpose || "prepare",
      extract: step.extract || [], confirm_delete: Boolean(step.confirm_delete),
    })),
  });
  renderScenarios();
}

function addScenarioStep(scenarioIndex, endpoint = {}) {
  const scenario = state.scenarios[scenarioIndex];
  if (!scenario) return;
  scenario.steps.push({
    id: scenarioId(), name: endpoint.name || "", path: endpoint.path || "", method: endpoint.method || "GET",
    params: endpoint.params || {}, body: endpoint.body ?? null, headers: endpoint.headers || {},
    role: scenario.actor_role || "", purpose: "prepare", extract: [], confirm_delete: false,
  });
  renderScenarios();
}

function updateScenarioStepFromEndpoint(scenarioIndex, stepIndex, value) {
  const endpoint = state.endpoints.find(ep => `${ep.method || "GET"} ${ep.path}` === value);
  if (!endpoint) return;
  const step = state.scenarios[scenarioIndex].steps[stepIndex];
  Object.assign(step, {
    name: endpoint.name || "", path: endpoint.path || "", method: endpoint.method || "GET",
    params: endpoint.params || {}, body: endpoint.body ?? null, headers: endpoint.headers || {},
  });
  renderScenarios();
}

/** 渲染造数场景；写操作和 DELETE 的审批开关会原样进入后端场景安全门禁。 */
function renderScenarios() {
  // 渲染业务场景编排。场景步骤可以提取变量，供后续接口路径/请求体里的 {{变量}} 使用。
  const box = $("scenarios");
  if (!box) return;
  const roles = Array.from(document.querySelectorAll(".roleRow .roleName")).map(el => el.value.trim()).filter(Boolean);
  if (!state.scenarios.length) {
    box.innerHTML = `<div class="emptyScenario">暂无业务场景。可新增“创建测试数据 → 提取 ID → 查询/越权验证 → 清理数据”的接口链路。</div>`;
    return;
  }
  box.innerHTML = state.scenarios.map((scenario, scenarioIndex) => {
    const roleOptions = [`<option value="">选择角色</option>`, ...roles.map(role => `<option value="${escapeHtml(role)}" ${role === scenario.actor_role ? "selected" : ""}>${escapeHtml(role)}</option>`)].join("");
    const steps = scenario.steps.map((step, stepIndex) => {
      const endpointValue = `${step.method || "GET"} ${step.path || ""}`;
      const stepRoleOptions = [`<option value="">跟随场景角色</option>`, ...roles.map(role => `<option value="${escapeHtml(role)}" ${role === step.role ? "selected" : ""}>${escapeHtml(role)}</option>`)].join("");
      const endpointOptions = [`<option value="">从接口库选择并填充</option>`, ...state.endpoints.map(ep => {
        const value = `${ep.method || "GET"} ${ep.path || ""}`;
        return `<option value="${escapeHtml(value)}" ${value === endpointValue ? "selected" : ""}>${escapeHtml(value)} · ${escapeHtml(ep.name || "未命名")}</option>`;
      })].join("");
      return `<div class="scenarioStep" data-scenario-index="${scenarioIndex}" data-step-index="${stepIndex}">
        <div class="scenarioStepTop"><span class="stepOrder">${stepIndex + 1}</span><select class="scenarioEndpointPick">${endpointOptions}</select><button type="button" class="moveStepUp" title="上移">↑</button><button type="button" class="moveStepDown" title="下移">↓</button><button type="button" class="deleteStep dangerBtn" title="删除步骤">删除</button></div>
        <div class="scenarioStepGrid">
          <label>步骤名称<input class="scenarioStepName" value="${escapeHtml(step.name || "")}" placeholder="例如：创建测试任务"></label>
          <label>执行角色<select class="scenarioStepRole">${stepRoleOptions}</select></label>
          <label>用途<select class="scenarioPurpose"><option value="prepare" ${step.purpose === "prepare" ? "selected" : ""}>准备数据</option><option value="verify" ${step.purpose === "verify" ? "selected" : ""}>业务验证</option><option value="teardown" ${step.purpose === "teardown" ? "selected" : ""}>清理数据</option></select></label>
          <label>方法<input class="scenarioMethod" value="${escapeHtml(step.method || "GET")}"></label>
          <label class="spanTwo">路径（可引用 {{变量}}）<input class="scenarioPath" value="${escapeHtml(step.path || "")}" placeholder="/api/v1/task/{{task_id}}"></label>
        </div>
        <div class="scenarioExtract"><label>提取变量（每行：变量名 = $.data.id）<textarea class="scenarioExtractInput" placeholder="task_id = $.data.id">${escapeHtml((step.extract || []).map(item => `${item.name} = ${item.path}`).join("\n"))}</textarea></label><label class="deleteConfirm"><input type="checkbox" class="scenarioDeleteConfirm" ${step.confirm_delete ? "checked" : ""}> 我确认该 DELETE 仅删除测试数据</label></div>
      </div>`;
    }).join("");
    return `<article class="scenarioCard" data-scenario-index="${scenarioIndex}">
      <div class="scenarioHead"><input class="scenarioName" value="${escapeHtml(scenario.name)}" aria-label="场景名称"><select class="scenarioActor">${roleOptions}</select><label class="scenarioToggle"><input type="checkbox" class="scenarioEnabled" ${scenario.enabled ? "checked" : ""}> 启用</label><label class="scenarioToggle writeToggle"><input type="checkbox" class="scenarioAllowWrite" ${scenario.allow_write ? "checked" : ""}> 仅测试环境允许写操作</label><button type="button" class="deleteScenario dangerBtn">删除场景</button></div>
      <div class="scenarioStepList">${steps || `<div class="emptySteps">通过搜索接口名称或 URL 添加第一条步骤。</div>`}</div>
      <div class="scenarioAdd"><input class="scenarioSearch" placeholder="搜索接口名称、URL、方法或模块"><select class="scenarioSearchResults"><option value="">选择接口加入场景</option></select><button type="button" class="addScenarioStep">添加步骤</button></div>
    </article>`;
  }).join("");

  box.querySelectorAll(".scenarioCard").forEach(card => {
    const si = Number(card.dataset.scenarioIndex);
    const scenario = state.scenarios[si];
    card.querySelector(".scenarioName").oninput = event => { scenario.name = event.target.value; };
    card.querySelector(".scenarioActor").onchange = event => { scenario.actor_role = event.target.value; renderScenarios(); };
    card.querySelector(".scenarioEnabled").onchange = event => { scenario.enabled = event.target.checked; };
    card.querySelector(".scenarioAllowWrite").onchange = event => { scenario.allow_write = event.target.checked; };
    card.querySelector(".deleteScenario").onclick = () => { state.scenarios.splice(si, 1); renderScenarios(); };
    const search = card.querySelector(".scenarioSearch");
    const results = card.querySelector(".scenarioSearchResults");
    const updateSearch = () => {
      results.innerHTML = `<option value="">选择接口加入场景</option>` + endpointSearchOptions(search.value).slice(0, 60).map(ep => {
        const value = `${ep.method || "GET"} ${ep.path || ""}`;
        const label = `${value} · ${ep.name || "未命名"}`;
        return `<option value="${escapeHtml(value)}" title="${escapeHtml(label)}">${escapeHtml(label.length > 110 ? `${label.slice(0, 107)}...` : label)}</option>`;
      }).join("");
    };
    search.oninput = updateSearch;
    updateSearch();
    card.querySelector(".addScenarioStep").onclick = () => {
      const ep = state.endpoints.find(item => `${item.method || "GET"} ${item.path || ""}` === results.value);
      if (!ep) return chat("请先从搜索结果中选择一个接口。");
      addScenarioStep(si, ep);
    };
  });
  box.querySelectorAll(".scenarioStep").forEach(row => {
    const si = Number(row.dataset.scenarioIndex);
    const ti = Number(row.dataset.stepIndex);
    const step = state.scenarios[si].steps[ti];
    row.querySelector(".scenarioEndpointPick").onchange = event => updateScenarioStepFromEndpoint(si, ti, event.target.value);
    row.querySelector(".scenarioStepName").oninput = event => { step.name = event.target.value; };
    row.querySelector(".scenarioStepRole").onchange = event => { step.role = event.target.value; };
    row.querySelector(".scenarioPurpose").onchange = event => { step.purpose = event.target.value; };
    row.querySelector(".scenarioMethod").oninput = event => { step.method = event.target.value.toUpperCase(); };
    row.querySelector(".scenarioPath").oninput = event => { step.path = event.target.value; };
    row.querySelector(".scenarioDeleteConfirm").onchange = event => { step.confirm_delete = event.target.checked; };
    row.querySelector(".scenarioExtractInput").oninput = event => {
      step.extract = event.target.value.split("\n").map(line => line.split("=")).map(([name, path]) => ({name: (name || "").trim(), path: (path || "").trim()})).filter(item => item.name && item.path);
    };
    row.querySelector(".deleteStep").onclick = () => { state.scenarios[si].steps.splice(ti, 1); renderScenarios(); };
    row.querySelector(".moveStepUp").onclick = () => { if (ti) { [state.scenarios[si].steps[ti - 1], state.scenarios[si].steps[ti]] = [state.scenarios[si].steps[ti], state.scenarios[si].steps[ti - 1]]; renderScenarios(); } };
    row.querySelector(".moveStepDown").onclick = () => { if (ti < state.scenarios[si].steps.length - 1) { [state.scenarios[si].steps[ti + 1], state.scenarios[si].steps[ti]] = [state.scenarios[si].steps[ti], state.scenarios[si].steps[ti + 1]]; renderScenarios(); } };
  });
}

function addEndpoint(ep = {}) {
  // 添加一个接口到前端内存接口库；只有点击保存后才会持久化到项目文件。
  state.endpoints.push({
    name: ep.name || "新接口",
    description: ep.description || "",
    path: ep.path || "/api/v1/example",
    method: ep.method || "GET",
    params: ep.params || {},
    headers: ep.headers || {},
    body: ep.body || null,
    sample_response: ep.sample_response || {},
    module: ep.module || "待确认",
    allowed_roles: ep.allowed_roles || [],
    observed_roles: ep.observed_roles || [],
    discovered_by: ep.discovered_by || ep.observed_roles || [],
    source: ep.source || "manual",
    sources: Array.from(new Set([...(ep.sources || []), ep.source || "manual"])),
    needs_review: Boolean(ep.needs_review),
    risk: ep.risk || "中",
    operation: ep.operation || "查询",
    page_url: ep.page_url || "",
    page_url_inferred: Boolean(ep.page_url_inferred),
    source_section: ep.source_section || "",
    source_sections: ep.source_sections || (ep.source_section ? [ep.source_section] : []),
    source_page_titles: ep.source_page_titles || [],
    source_pages: ep.source_pages || [],
  });
  renderEndpoints();
  renderScenarios();
}

function endpointSourceLabel(ep = {}) {
  const rawSources = Array.isArray(ep.sources) ? ep.sources : [ep.source || "manual"];
  const sources = rawSources.map(item => String(item || "").toLowerCase()).filter(Boolean);
  const sourceMap = {
    manual: "手工填写",
    har: "HAR 文件",
    collection: "接口集合文件",
    api_tool: "API 工具导出",
    curl: "cURL 导入",
    site: "站点发现",
    browser_recording: "历史录制数据",
    manual_playwright: "Playwright 人工录制",
    import: "文件导入",
  };
  const base = Array.from(new Set(sources)).map(source => sourceMap[source] || source).join("+") || "手工填写";
  const roles = ep.observed_roles || ep.discovered_by || [];
  const flags = [];
  if (roles.length) flags.push(`角色:${roles.join("、")}`);
  if (ep.needs_review) flags.push("待确认");
  const tone = sources.length === 1 ? (sources[0] || "manual") : "mixed";
  return {base, detail: flags.join(" / "), tone};
}

function endpointSections(ep = {}) {
  const sections = Array.isArray(ep.source_sections) ? ep.source_sections : [];
  const aliases = new Set(["无上层索引/未捕捉到", "无上层索引/未抓取到"]);
  const raw = [...sections, ep.source_section]
    .map(item => String(item || "").trim())
    .filter(Boolean)
    .map(item => aliases.has(item) ? UNCAPTURED_SECTION : item);
  return Array.from(new Set(raw)).length ? Array.from(new Set(raw)) : [UNCAPTURED_SECTION];
}

function setEndpointSection(ep, value) {
  const section = String(value || "").trim() || UNCAPTURED_SECTION;
  ep.source_section = section;
  ep.source_sections = [section];
}

function sortEndpointSections(entries) {
  return entries.sort((a, b) => {
    if (a[0] === UNCAPTURED_SECTION) return 1;
    if (b[0] === UNCAPTURED_SECTION) return -1;
    return a[0].localeCompare(b[0], "zh-Hans-CN");
  });
}

function renderEndpointSectionFilters() {
  const box = $("endpointSectionFilters");
  if (!box) return;
  const counts = new Map();
  state.endpoints.forEach(ep => endpointSections(ep).forEach(section => counts.set(section, (counts.get(section) || 0) + 1)));
  const entries = sortEndpointSections(Array.from(counts.entries()));
  const suggestions = $("endpointSectionSuggestions");
  if (suggestions) {
    const values = entries.map(([section]) => section);
    if (!values.includes(UNCAPTURED_SECTION)) values.push(UNCAPTURED_SECTION);
    suggestions.innerHTML = values.map(section => `<option value="${escapeHtml(section)}"></option>`).join("");
  }
  if (!entries.length) {
    state.endpointSectionFilter = "";
    box.innerHTML = "";
    return;
  }
  if (state.endpointSectionFilter && !counts.has(state.endpointSectionFilter)) {
    state.endpointSectionFilter = "";
  }
  box.innerHTML = [
    `<button type="button" class="sectionFilterBtn ${state.endpointSectionFilter ? "" : "active"}" data-section="">全部 <span>${state.endpoints.length}</span></button>`,
    ...entries.map(([section, count]) => `<button type="button" class="sectionFilterBtn ${state.endpointSectionFilter === section ? "active" : ""}" data-section="${escapeHtml(section)}">${escapeHtml(section)} <span>${count}</span></button>`),
  ].join("");
  box.querySelectorAll(".sectionFilterBtn").forEach(button => {
    button.onclick = () => {
      state.endpointSectionFilter = button.dataset.section || "";
      state.endpointPage = 1;
      renderEndpoints();
    };
  });
}

/**
 * 渲染接口池的分页编辑表。修改 allowed_roles/page_url 会使计划过期；
 * 表格只是草稿编辑器，后端保存后才成为测试执行输入。
 */
function renderEndpoints() {
  // 把 state.endpoints 画进 <tbody id="endpointRows">；编辑单元格会直接修改 state.endpoints。
  const body = $("endpointRows");
  renderEndpointSectionFilters();
  body.innerHTML = "";
  const query = ($("endpointSearch")?.value || "").trim().toLowerCase();
  const filtered = state.endpoints
    .map((ep, idx) => ({ep, idx}))
    .filter(({ep}) => {
      if (state.endpointSectionFilter && !endpointSections(ep).includes(state.endpointSectionFilter)) {
        return false;
      }
      const text = [
        ep.name, ep.description, ep.path, ep.method, ep.module,
        ep.source,
        (ep.sources || []).join(" "),
        endpointSections(ep).join(" "),
        (ep.allowed_roles || []).join(" "),
        (ep.observed_roles || ep.discovered_by || []).join(" "),
        ep.page_url, ep.risk, ep.operation,
      ].join(" ").toLowerCase();
      return !query || text.includes(query);
    });
  const pageSize = effectiveEndpointPageSize();
  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
  state.endpointPage = Math.min(Math.max(1, state.endpointPage), totalPages);
  const start = (state.endpointPage - 1) * pageSize;
  const pageItems = filtered.slice(start, start + pageSize);
  pageItems.forEach(({ep, idx}) => {
    const row = document.createElement("tr");
    const rowLabel = `第 ${idx + 1} 行`;
    const pagePlaceholder = ep.page_url_inferred ? "自动推断，可手动修正" : "/admin/users";
    const detailId = `epDetail_${idx}`;
    const headers = ep.headers || {};
    const params = ep.params || {};
    const responseSample = ep.sample_response || {};
    const hasDetails = hasDetailValue(headers) || hasDetailValue(params) || hasDetailValue(ep.body) || hasDetailValue(responseSample);
    const sourceLabel = endpointSourceLabel(ep);
    const sectionText = endpointSections(ep).join("、");
    row.innerHTML = `
      <td><button type="button" class="epExpandToggle" data-detail="${detailId}" title="展开/收起请求和响应详情">${hasDetails ? "▶" : "·"}</button></td>
      <td><input value="${escapeHtml(ep.name || "")}" data-field="name" aria-label="${rowLabel}接口名称"></td>
      <td><input value="${escapeHtml(ep.method || "GET")}" data-field="method" aria-label="${rowLabel}请求方法"></td>
      <td><input value="${escapeHtml(ep.path || "")}" data-field="path" aria-label="${rowLabel}接口路径"></td>
      <td><input value="${escapeHtml(ep.module || "")}" data-field="module" aria-label="${rowLabel}模块"></td>
      <td><input class="endpointSectionInput" value="${escapeHtml(sectionText)}" data-field="source_section" aria-label="${rowLabel}所属索引" list="endpointSectionSuggestions" placeholder="${escapeHtml(UNCAPTURED_SECTION)}"></td>
      <td><div class="sourceCell"><span class="sourceTag sourceTag--${escapeHtml(sourceLabel.tone)}">${escapeHtml(sourceLabel.base)}</span>${sourceLabel.detail ? `<small>${escapeHtml(sourceLabel.detail)}</small>` : ""}${(ep.source_pages || []).length ? `<small>页面分组: ${escapeHtml((ep.source_pages || []).join("、"))}</small>` : ""}</div></td>
      <td><input value="${escapeHtml((ep.allowed_roles || []).join("、"))}" data-field="allowed_roles" aria-label="${rowLabel}有权角色"></td>
      <td><input value="${escapeHtml(ep.page_url || "")}" data-field="page_url" aria-label="${rowLabel}关联页面" placeholder="${escapeHtml(pagePlaceholder)}" title="${ep.page_url_inferred ? "根据接口路径自动推断，建议抽查修正" : ""}"></td>
      <td><button type="button" class="epDeleteBtn" aria-label="删除${rowLabel}接口">删除</button></td>`;
    row.querySelectorAll("input").forEach(input => {
      input.oninput = () => {
        const field = input.dataset.field;
        state.endpoints[idx][field] = field === "allowed_roles"
          ? input.value.split(/[、,，\s]+/).filter(Boolean)
          : input.value;
        if (field === "source_section") {
          setEndpointSection(state.endpoints[idx], input.value);
          renderEndpointSectionFilters();
        }
        if (field === "page_url") state.endpoints[idx].page_url_inferred = false;
        if (["method", "path", "module", "source_section", "allowed_roles", "page_url"].includes(field)) {
          markPlanStale("接口字段已修改");
        }
        markDirty();
      };
    });
    row.querySelector(".epExpandToggle").onclick = (event) => {
      event.preventDefault();
      event.stopPropagation();
      const detail = $(detailId);
      if (!detail) return;
      const opened = detail.style.display !== "none";
      detail.style.display = opened ? "none" : "";
      const toggle = row.querySelector(".epExpandToggle");
      toggle.textContent = opened ? (hasDetails ? "▶" : "·") : "▼";
      toggle.classList.toggle("open", !opened);
    };
    row.querySelector(".epDeleteBtn").onclick = (event) => {
      event.preventDefault();
      event.stopPropagation();
      state.endpoints.splice(idx, 1);
      markPlanStale("接口已删除");
      renderEndpoints();
    };
    body.appendChild(row);

    // 详情行
    const detailRow = document.createElement("tr");
    detailRow.id = detailId;
    detailRow.className = "epDetailRow";
    detailRow.style.display = "none";
    const requestSample = {
      method: ep.method || "GET",
      path: ep.path || "",
      params,
      headers,
      body: hasDetailValue(ep.body) ? ep.body : "",
    };
    const responseDetail = hasDetailValue(responseSample) ? responseSample : "录制或导入来源未提供响应样本。";
    const detailHtml = `<td colspan="10"><div class="epDetailBox">
      <div class="epDetailSplit">
        <div class="epDetailSection"><strong>请求样本</strong><pre class="codeBlock">${escapeHtml(prettyJson(requestSample) || "录制或导入来源未提供请求样本。")}</pre></div>
        <div class="epDetailSection"><strong>响应样本</strong><pre class="codeBlock">${escapeHtml(prettyJson(responseDetail))}</pre></div>
      </div>
      <div class="epDetailSmall">
        <span>Method: ${escapeHtml(ep.method || "GET")}</span>
        <span>Path: ${escapeHtml(ep.path || "")}</span>
        ${ep.description ? `<span>描述: ${escapeHtml(ep.description)}</span>` : ""}
      </div>
    </div></td>`;
    detailRow.innerHTML = detailHtml;
    body.appendChild(detailRow);
  });
  $("endpointPageInfo").textContent = `第 ${state.endpointPage} / ${totalPages} 页，共 ${filtered.length} 个接口，每页 ${pageSize} 条`;
  syncPageJump("endpointPageJump", state.endpointPage, totalPages);
  $("prevEndpointPage").disabled = state.endpointPage <= 1;
  $("nextEndpointPage").disabled = state.endpointPage >= totalPages;
  renderRoleImportStrip();
}

function currentRoleNames() {
  return Array.from(document.querySelectorAll(".roleRow .roleName"))
    .map(input => input.value.trim())
    .filter(Boolean);
}

function normalizePageRoute(value) {
  const raw = String(value || "").trim();
  if (!raw) return "";
  try {
    const parsed = new URL(raw);
    return parsed.hash ? `/${parsed.hash}` : (parsed.pathname || "/");
  } catch (_) {
    return raw.startsWith("/") ? raw : `/${raw}`;
  }
}

function renderPageRoutes() {
  const roles = currentRoleNames();
  const select = $("pageRouteRole");
  if (select) {
    const current = select.value;
    select.innerHTML = roles.map(role => `<option value="${escapeHtml(role)}">${escapeHtml(role)}</option>`).join("");
    if (current && roles.includes(current)) select.value = current;
  }
  const body = $("pageRouteRows");
  if (!body) return;
  body.innerHTML = state.pageRoutes.map((route, index) => `
    <tr><td>${escapeHtml(route.page_url || "")}</td><td>${escapeHtml((route.allowed_roles || []).join("、") || "-")}</td>
    <td>${escapeHtml(route.source || "page_url_supplement")}</td><td><button type="button" class="removePageRoute" data-index="${index}">删除</button></td></tr>
  `).join("") || `<tr><td colspan="4">暂无补录页面 URL。</td></tr>`;
  body.querySelectorAll(".removePageRoute").forEach(button => {
    button.onclick = () => { state.pageRoutes.splice(Number(button.dataset.index), 1); markDirty(); renderPageRoutes(); };
  });
}

function addPageRoute() {
  const role = $("pageRouteRole")?.value || "";
  const pageUrl = normalizePageRoute($("pageRouteInput")?.value);
  if (!role) return chat("请先选择页面所属角色。");
  if (!pageUrl) return chat("请填写页面 URL。");
  const existing = state.pageRoutes.find(item => item.page_url === pageUrl);
  if (existing) {
    existing.allowed_roles = Array.from(new Set([...(existing.allowed_roles || []), role]));
    existing.observed_roles = Array.from(new Set([...(existing.observed_roles || []), role]));
  } else {
    state.pageRoutes.push({ page_url: pageUrl, allowed_roles: [role], observed_roles: [role], source: "page_url_supplement", needs_review: true });
  }
  $("pageRouteInput").value = "";
  markDirty();
  renderPageRoutes();
  chat(`页面 URL 已补录：${pageUrl}（${role}）`);
}

function renderManualRecordingRoles(roles = currentRoleNames()) {
  const select = $("manualRecordRole");
  if (!select) return;
  const current = select.value;
  select.innerHTML = roles.map(role => `<option value="${escapeHtml(role)}">${escapeHtml(role)}</option>`).join("");
  if (current && roles.includes(current)) select.value = current;
}

function renderRoleImportStrip() {
  // 角色卡片用于回答“这个 HAR 是哪个角色抓出来的？”。
  // 数量基于 observed_roles/discovered_by，不是根据路径猜出来的权限。
  const box = $("roleImportStrip");
  if (!box) return;
  const roles = currentRoleNames();
  if (!roles.length) {
    box.innerHTML = `<div class="emptyScenario">请先在基础配置里添加角色账号，再按角色导入 HAR 或接口集合。</div>`;
    return;
  }
  box.innerHTML = roles.map(role => {
    const count = state.endpoints.filter(ep => (ep.observed_roles || ep.discovered_by || []).includes(role)).length;
    return `<article class="roleImportCard">
      <div>
        <strong>${escapeHtml(role)}</strong>
        <span>${count} 个来源接口</span>
      </div>
      <button type="button" class="openRoleImport" data-role="${escapeHtml(role)}">接口导入</button>
    </article>`;
  }).join("");
  box.querySelectorAll(".openRoleImport").forEach(btn => {
    btn.onclick = () => openRoleImport(btn.dataset.role || "");
  });
}

function openRoleImport(role) {
  // 打开某个角色专属的导入弹窗；从这里上传的文件都会带上 source_role。
  state.activeImportRole = role;
  state.roleImportPreview = state.endpoints.filter(ep => (ep.observed_roles || ep.discovered_by || []).includes(role));
  $("roleImportTitle").textContent = `${role} · 接口导入`;
  $("roleImportHint").textContent = "这里导入的是该角色实际访问过的请求，用于形成 allowed_roles 的初始证据；导入后仍可在表格里人工调整。";
  $("roleImportModal").hidden = false;
  renderSourceLists();
  renderRoleImportPreview();
}

function closeRoleImport() {
  $("roleImportModal").hidden = true;
  state.activeImportRole = "";
  state.roleImportPreview = [];
  ["roleHarFile", "roleCollectionFile"].forEach(id => { if ($(id)) $(id).value = ""; });
  if ($("roleCurlText")) $("roleCurlText").value = "";
}

function renderRoleImportPreview() {
  // 展示当前角色观察到的接口，方便用户在弹窗里先看一遍导入结果。
  const body = $("roleImportRows");
  if (!body) return;
  const query = ($("roleImportSearch")?.value || "").trim().toLowerCase();
  const rows = (state.roleImportPreview || []).filter(ep => {
    const text = [ep.method, ep.path, ep.module, (ep.allowed_roles || []).join(" ")].join(" ").toLowerCase();
    return !query || text.includes(query);
  });
  body.innerHTML = rows.map(ep => `<tr>
    <td>${escapeHtml(ep.method || "GET")}</td>
    <td><div class="runPath">${escapeHtml(ep.path || "")}</div></td>
    <td>${escapeHtml(ep.module || "")}</td>
    <td>${escapeHtml((ep.allowed_roles || []).join("、") || state.activeImportRole || "-")}</td>
    <td><button type="button" class="removeRoleEp" data-key="${escapeHtml(`${ep.method || "GET"}:${ep.path || ""}`)}">移除</button></td>
  </tr>`).join("") || `<tr><td colspan="5">当前角色还没有导入接口。</td></tr>`;
  body.querySelectorAll(".removeRoleEp").forEach(btn => {
    btn.onclick = () => {
      const [method, ...pathParts] = String(btn.dataset.key || "").split(":");
      const path = pathParts.join(":");
      state.endpoints = state.endpoints.filter(ep => `${ep.method || "GET"}:${ep.path || ""}` !== `${method}:${path}`);
      state.roleImportPreview = state.roleImportPreview.filter(ep => `${ep.method || "GET"}:${ep.path || ""}` !== `${method}:${path}`);
      markDirty();
      renderEndpoints();
      renderRoleImportStrip();
      renderRoleImportPreview();
    };
  });
  if ($("roleImportStatus")) $("roleImportStatus").textContent = `当前角色 ${state.activeImportRole || "-"}：${rows.length} 个接口`;
}

/**
 * 将服务端项目快照反向填入表单和 state。
 * 与 collectProject() 构成双向映射；restoring=true 期间禁止脏状态监听器误判为用户修改。
 */
function fillProject(project, plan = null) {
  // 后端项目数据 -> 页面状态。打开历史项目时，会在这里回填输入框和 state。
  clearDefaultHints();
  state.project_id = project.project_id || "";
  state.selectedHistoryRunId = "";
  state.compareRuns = {left: null, right: null};
  state.runHistory = [];
  $("projectName").value = project.project_name || "";
  $("baseUrl").value = project.base_url || "";
  $("timeout").value = project.request_timeout || 10;
  $("modelType").value = project.model_type === "三权分立" ? "综合越权检测" : (project.model_type || "综合越权检测");
  $("goal").value = project.goal || "";
  $("prdText").value = project.prd_text || "";
  state.testcases = project.testcases || null;
  state.sourceFiles = project.source_files || [];
  renderSourceLists();
  $("testcaseDigest").value = state.testcases ? state.testcases.digest || "" : "";
  $("tokenUrl").value = (project.auth || {}).token_url || "";
  if ($("authMethod")) $("authMethod").value = (project.auth || {}).auth_method || "json_post";
  $("loginType").value = (project.auth || {}).login_type || "WEB";
  $("tokenPath").value = (project.auth || {}).token_path || "tokenInfo.accessToken";
  $("tokenMode").value = (project.auth || {}).token_mode || "Bearer Authorization";

  $("roles").innerHTML = "";
  (project.roles || []).forEach(role => addRole(role));
 state.scenarios = project.scenarios || [];
  state.pageRoutes = project.page_routes || [];
 renderScenarios();
  const frontend = project.frontend_checks || {};
  $("frontendTokenKeys").value = frontend.token_storage_keys || "token\naccessToken\nAuthorization";
  $("frontendDeniedKeywords").value = frontend.denied_keywords || "无权限\n没有权限\n未授权\n权限不足\n禁止访问\n403\nforbidden\nunauthorized";
  $("frontendTimeout").value = frontend.timeout_ms || 8000;
  $("frontendWait").value = frontend.wait_after_load_ms || 1200;
 state.endpoints = project.endpoints || [];
 renderEndpoints();
  renderPageRoutes();
 renderScenarios();
  if (plan) renderPlan(plan);
  updateDownloads();
  $("currentProject").textContent = state.project_id ? `当前：${project.project_name || state.project_id}` : "未保存项目";
  notifyFindingsChanged();
}

function resetRunState() {
  state.runResults = [];
  state.runHistory = [];
  state.compareRuns = {left: null, right: null};
  state.selectedHistoryRunId = "";
  state.compareSearch = {left: "", right: ""};
  if ($("compareLeftSearch")) $("compareLeftSearch").value = "";
  if ($("compareRightSearch")) $("compareRightSearch").value = "";
  state.frontendResults = [];
  state.scenarioResults = {};
  state.assertionProfile = {};
  state.runSummary = {};
  state.aiRunInsights = null;
  state.manualReview = {};
  state.hasReport = false;
  state.runStartedAt = "";
  state.runFinishedAt = "";
}

function newProject() {
  clearDefaultHints();
  // 清除 sessionStorage 中的旧项目残留
  sessionStorage.removeItem(ACTIVE_STEP_KEY);
  sessionStorage.removeItem(SCROLL_Y_KEY);
  sessionStorage.removeItem(RECORD_SESSION_KEY);
  clearToolLog();
  state.project_id = "";
  notifyFindingsChanged();
 state.endpoints = [];
  state.pageRoutes = [];
 state.scenarios = [];
  state.plan = null;
  state.testcases = null;
  state.sourceFiles = [];
  state.activeImportRole = "";
  state.roleImportPreview = [];
  renderSourceLists();
  resetRunState();

  $("currentProject").textContent = "未保存项目";
  $("projectName").value = "";
  $("baseUrl").value = "";
  $("timeout").value = "10";
  $("modelType").value = "综合越权检测";
  $("goal").value = "";
  $("prdText").value = "";
  $("testcaseDigest").value = "";
  $("tokenUrl").value = "";
  if ($("authMethod")) $("authMethod").value = "json_post";
  $("loginType").value = "WEB";
  $("tokenPath").value = "tokenInfo.accessToken";
  $("tokenMode").value = "Bearer Authorization";
  $("roles").innerHTML = "";
  $("frontendTokenKeys").value = "token\naccessToken\nAuthorization";
  $("frontendDeniedKeywords").value = "无权限\n没有权限\n未授权\n权限不足\n禁止访问\n403\nforbidden\nunauthorized";
  $("frontendTimeout").value = "8000";
  $("frontendWait").value = "1200";
  ["prdFile", "testcaseFile", "harFile", "collectionFile", "roleHarFile", "roleCollectionFile"].forEach(id => {
    if ($(id)) $(id).value = "";
  });
  if ($("curlText")) $("curlText").value = "";
  if ($("roleCurlText")) $("roleCurlText").value = "";

 renderEndpoints();
 renderRoleImportStrip();
  renderPageRoutes();
 renderScenarios();
  renderPlan(null);
  renderResultCards();
  renderRunPage();
  renderPrettyRunLog({});
  renderScenarioResults({});
  renderFrontendResults({});
  renderAssertionProfile({});
  renderRunCompare();
  applyDefaultHints();
  markDirty();
  setStep("0");
  window.scrollTo({top: 0, behavior: "smooth"});
  chat("已新建未保存项目草稿。");
}

async function loadProjects() {
  // 项目列表是服务端真相；登录状态变化或保存/删除完成后都重新拉取，不使用旧缓存猜测。
  // 历史项目只属于当前登录用户；未登录时保持空列表。
  const rows = $("projectRows");
  if (!state.user) {
    state.projects = [];
    rows.innerHTML = `<tr><td colspan="9">登录后可查看自己的历史项目。</td></tr>`;
    return;
  }
  const result = await api("/api/projects");
  state.projects = result.projects || [];
  const query = ($("projectSearch")?.value || "").trim().toLowerCase();
  const projects = state.projects.filter(p => {
    const text = [p.project_name, p.base_url, p.model_type, p.updated_at, p.project_id].join(" ").toLowerCase();
    return !query || text.includes(query);
  });
  rows.innerHTML = projects.map(p => `
    <tr>
      <td>${p.project_name}</td>
      <td>${p.base_url || "-"}</td>
      <td>${p.model_type || "-"}</td>
      <td>${p.roles_count}</td>
      <td>${p.endpoints_count}</td>
      <td>${p.cases_count}</td>
      <td>${p.has_report ? "已生成" : "无"}</td>
      <td>${p.updated_at || "-"}</td>
      <td>
        <div class="rowActions">
          <button type="button" class="openProject" data-id="${p.project_id}">打开</button>
          <button type="button" class="dangerBtn deleteProject" data-id="${p.project_id}" data-name="${escapeHtml(p.project_name || p.project_id)}">删除</button>
        </div>
      </td>
    </tr>`).join("") || `<tr><td colspan="9">${query ? "没有匹配的历史项目" : "暂无历史项目"}</td></tr>`;
  rows.querySelectorAll(".openProject").forEach(btn => {
    btn.onclick = () => openProject(btn.dataset.id);
  });
  rows.querySelectorAll(".deleteProject").forEach(btn => {
    btn.onclick = () => deleteProject(btn.dataset.id, btn.dataset.name);
  });
}

async function openProject(projectId) {
  // 加载项目聚合：配置、计划、最近结果、历史和产物状态由同一个项目 ID 关联。
  try {
    // 重置所有执行状态，避免跨项目数据污染
    resetRunState();
    // 清除 sessionStorage 中的旧项目残留
    sessionStorage.removeItem(ACTIVE_STEP_KEY);
    sessionStorage.removeItem(SCROLL_Y_KEY);
    sessionStorage.removeItem(RECORD_SESSION_KEY);
    clearToolLog();
   const result = await api(`/api/projects/${projectId}`);
   fillProject(result.project, result.plan);
   state.sourceFiles = result.source_files || result.project.source_files || [];
    await loadLatestRunResult(result.has_report);
   setStep("0");
    markClean();
    window.scrollTo({top: 0, behavior: "smooth"});
    chat(`打开历史项目：${result.project.project_name || projectId}`);
  } catch (err) {
    chat(`打开项目失败：${err.message || err}`);
    // 即使失败也跳转到历史页确保用户不卡住
    setStep("history");
    loadProjects();
  }
}

async function deleteProject(projectId, projectName) {
  if (!confirm(`确认删除项目「${projectName || projectId}」吗？本地项目文件夹和历史结果会一起删除。`)) return;
  await api(`/api/projects/${projectId}`, {method: "DELETE"});
  if (state.project_id === projectId) {
    state.project_id = "";
    clearToolLog();
    $("currentProject").textContent = "未保存项目";
    state.runResults = [];
    state.frontendResults = [];
    state.scenarioResults = {};
    state.runSummary = {};
    state.assertionProfile = {};
    state.hasReport = false;
    renderResultCards();
    renderRunPage();
    renderScenarioResults({});
    renderFrontendResults({});
    renderAssertionProfile({});
  }
  await loadProjects();
  chat(`已删除历史项目：${projectName || projectId}`);
}

/** 将规则/AI 审查后的计划写入 state，再按分类、角色、分页渲染。 */
function renderPlan(plan) {
  // 后端计划 -> 页面展示。_make_plan() 生成用例，AI 审查再补充修改建议。
  if (!plan) {
    state.plan = null;
    state.planPage = 1;
    state.activePlanCategory = "allowed";
    state.activePlanRole = "";
    state.aiPlanInsights = null;
    if ($("missing")) {
      $("missing").className = "notice";
      $("missing").textContent = "新项目尚未生成执行计划。";
    }
    if ($("planSummary")) $("planSummary").innerHTML = "";
    if ($("planSections")) $("planSections").innerHTML = "";
    if ($("aiPlanInsights")) {
      $("aiPlanInsights").hidden = true;
      $("aiPlanInsights").innerHTML = "";
    }
    return;
  }
  state.plan = plan;
  state.planStale = false;
  state.planPage = 1;
  state.activePlanCategory = "allowed";
  state.activePlanRole = "";
  state.aiPlanInsights = null;
  const missing = plan.missing || [];
  $("missing").className = `notice ${missing.length ? "" : "ok"}`;
  $("missing").textContent = missing.length ? `Agent 认为还缺：${missing.join("；")}` : "Agent 检查通过：基础信息已足够生成测试计划。";

  $("aiPlanInsights").hidden = true;
  $("aiPlanInsights").innerHTML = "";
  renderPlanPage();
}

const PLAN_CATEGORIES = [
  ["allowed", "自身权限"],
  ["vertical", "垂直越权"],
  ["role_isolation", "角色隔离"],
  ["horizontal", "水平越权"],
  ["anonymous", "未授权"],
];
const ROLE_FILTER_PLAN_CATEGORIES = new Set(["allowed", "role_isolation"]);

function inferPlanCategory(item = {}) {
  if (item.category) return item.category;
  const type = String(item.type || "");
  if (type.includes("自身")) return "allowed";
  if (type.includes("垂直")) return "vertical";
  if (type.includes("角色隔离")) return "role_isolation";
  if (type.includes("水平")) return "horizontal";
  if (type.includes("未授权")) return "anonymous";
  if (type.includes("前端")) return "frontend";
  if (type.includes("用例")) return "testcase";
  if (type.includes("场景")) return "scenario";
  return "other";
}

function renderPlanSummary(plan = state.plan || {}) {
  $("planSummary").innerHTML = PLAN_CATEGORIES.map(([key, label]) => `
    <button type="button" class="stat planStat ${state.activePlanCategory === key ? "active" : ""}" data-plan-target="${key}">
      <span>${label}</span>
      <strong>${(plan.summary || {})[key] || 0}</strong>
      <em>查看请求计划</em>
    </button>
  `).join("");
  document.querySelectorAll(".planStat").forEach(card => {
    card.onclick = () => {
      state.activePlanCategory = card.dataset.planTarget || "allowed";
      state.activePlanRole = "";
      state.planPage = 1;
      renderPlanPage();
    };
  });
}

function planRiskOptions(value) {
  return ["低", "中", "中高", "高"].map(item => `<option value="${item}" ${item === value ? "selected" : ""}>${item}</option>`).join("");
}

function bindPlanInlineEditors() {
  document.querySelectorAll("[data-plan-case]").forEach(control => {
    const update = () => {
      const item = (state.plan?.cases || [])[Number(control.dataset.planCase)];
      if (!item) return;
      item[control.dataset.planField] = control.value;
      markDirty();
    };
    control.oninput = update;
    control.onchange = update;
  });
  document.querySelectorAll(".planDeleteCase").forEach(button => {
    button.onclick = () => {
      const index = Number(button.dataset.planDelete);
      if (!Number.isInteger(index)) return;
      state.plan.cases.splice(index, 1);
      markDirty();
      renderPlanPage();
    };
  });
}

function renderPlanPage() {
  const cases = (state.plan || {}).cases || [];
  const query = ($("planSearch")?.value || "").trim().toLowerCase();
  if (!PLAN_CATEGORIES.some(([key]) => key === state.activePlanCategory)) {
    state.activePlanCategory = "allowed";
  }
  const categoryItems = cases.filter(c => inferPlanCategory(c) === state.activePlanCategory);
  const needsRoleFilter = ROLE_FILTER_PLAN_CATEGORIES.has(state.activePlanCategory);
  const roleNames = Array.from(new Set(categoryItems.map(item => item.actor || "待确认角色"))).filter(Boolean);
  if (needsRoleFilter) {
    if (!state.activePlanRole || !roleNames.includes(state.activePlanRole)) {
      state.activePlanRole = roleNames[0] || "";
    }
  } else {
    state.activePlanRole = "";
  }
  const filtered = categoryItems.filter(c => {
    const text = [c.type, c.actor, c.target, c.method, c.path, c.name, c.module, c.expected, c.risk].join(" ").toLowerCase();
    const roleMatched = !needsRoleFilter || !state.activePlanRole || (c.actor || "待确认角色") === state.activePlanRole;
    return roleMatched && (!query || text.includes(query));
  });
  const currentLabel = (PLAN_CATEGORIES.find(([key]) => key === state.activePlanCategory) || PLAN_CATEGORIES[0])[1];
  const roleCards = needsRoleFilter ? `
    <div class="planRoleTabs">
      ${roleNames.map(role => {
        const count = categoryItems.filter(item => (item.actor || "待确认角色") === role).length;
        return `<button type="button" class="planRoleTab ${state.activePlanRole === role ? "active" : ""}" data-plan-role="${escapeHtml(role)}">
          <span>${escapeHtml(role)}</span>
          <strong>${count}</strong>
        </button>`;
      }).join("")}
    </div>` : "";
  const pageSize = effectivePlanPageSize();
  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
  state.planPage = Math.min(Math.max(1, state.planPage), totalPages);
  const start = (state.planPage - 1) * pageSize;
  const paged = filtered.slice(start, start + pageSize);
  const tableRows = paged.map(c => {
    const caseIndex = cases.indexOf(c);
    return `
    <tr>
      <td><div class="runIface"><div class="runPath">${escapeHtml(c.method || "")} ${escapeHtml(c.path || "")}</div><div class="runName">${escapeHtml(c.name || c.type || "")}</div></div></td>
      <td>${escapeHtml(c.actor || "")}</td>
      <td>${escapeHtml(c.target || "")}</td>
      <td>${escapeHtml(c.module || "")}</td>
      <td><input data-plan-case="${caseIndex}" data-plan-field="expected" value="${escapeHtml(c.expected || "")}" aria-label="修改预期"></td>
      <td><select data-plan-case="${caseIndex}" data-plan-field="risk" aria-label="修改风险">${planRiskOptions(c.risk || "中")}</select></td>
      <td><button type="button" class="planDeleteCase" data-plan-delete="${caseIndex}">删除</button></td>
    </tr>`;
  }).join("");
  if (needsRoleFilter) {
    renderPlanSummary(state.plan || {});
    $("planSections").innerHTML = `
      <section class="planSection single" data-plan-section="${state.activePlanCategory}">
        <div class="planSectionHead">
          <h3>${currentLabel}${state.activePlanRole ? ` / ${escapeHtml(state.activePlanRole)}` : ""}</h3>
          <span>${filtered.length} 条</span>
        </div>
        ${roleCards}
        ${filtered.length ? `
          <div class="tableWrap">
            <table class="planTable">
              <thead><tr><th>接口</th><th>角色</th><th>目标</th><th>模块</th><th>预期</th><th>风险</th></tr></thead>
              <tbody>${tableRows}</tbody>
            </table>
          </div>` : `<div class="emptyScenario">${query ? "没有匹配的请求计划。" : "当前没有这类请求计划。"}</div>`}
      </section>`;
    document.querySelectorAll(".planRoleTab").forEach(card => {
      card.onclick = () => {
        state.activePlanRole = card.dataset.planRole || "";
        state.planPage = 1;
        renderPlanPage();
      };
    });
    bindPlanInlineEditors();
    _renderPlanFooter(filtered.length, pageSize);
    return;
  }
  const byRole = new Map();
  for (const item of filtered) {
    const role = item.actor || "待确认角色";
    if (!byRole.has(role)) byRole.set(role, []);
    byRole.get(role).push(item);
  }
  const roleBlocks = Array.from(byRole.entries()).map(([role, roleRows]) => `
    <details class="planRoleGroup" open>
      <summary><strong>${escapeHtml(role)}</strong><span>${roleRows.length} 条请求</span></summary>
      <div class="tableWrap">
        <table class="planTable">
          <thead><tr><th>接口</th><th>目标</th><th>模块</th><th>预期</th><th>风险</th></tr></thead>
          <tbody>${roleRows.map(c => `
            <tr>
              <td><div class="runIface"><div class="runPath">${escapeHtml(c.method || "")} ${escapeHtml(c.path || "")}</div><div class="runName">${escapeHtml(c.name || c.type || "")}</div></div></td>
              <td>${escapeHtml(c.target || "")}</td>
              <td>${escapeHtml(c.module || "")}</td>
              <td>${escapeHtml(c.expected || "")}</td>
              <td>${escapeHtml(c.risk || "")}</td>
            </tr>`).join("")}</tbody>
        </table>
      </div>
    </details>
  `).join("");
  renderPlanSummary(state.plan || {});
  $("planSections").innerHTML = `
    <section class="planSection single" data-plan-section="${state.activePlanCategory}">
      <div class="planSectionHead">
        <h3>${currentLabel}</h3>
        <span>${filtered.length} 条</span>
      </div>
      ${roleBlocks || `<div class="emptyScenario">${query ? "没有匹配的请求计划。" : "当前没有这类请求计划。"}</div>`}
    </section>`;
  _renderPlanFooter(0, 0);  // 非角色过滤分类不用分页
}

function _renderPlanFooter(total, pageSize) {
  const footer = $("planFooter");
  if (!footer) return;
  if (total <= 0 || pageSize <= 0) {
    footer.style.display = "none";
    return;
  }
  footer.style.display = "";
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  $("planPageInfo").textContent = `第 ${state.planPage} / ${totalPages} 页，共 ${total} 条，每页 ${pageSize} 条`;
  syncPageJump("planPageJump", state.planPage, totalPages);
  $("prevPlanPage").disabled = state.planPage <= 1;
  $("nextPlanPage").disabled = state.planPage >= totalPages;
}

function collectProjectForAi() {
  // 给 AI 用的脱敏项目数据；发送到 /api/ai/analyze-plan 前会遮罩密码。
  const project = collectProject();
  project.roles = (project.roles || []).map(role => ({...role, password: role.password ? "******" : ""}));
  return project;
}

function renderAiPlanInsights(data = {}) {
  state.aiPlanInsights = data;
  const box = $("aiPlanInsights");
  const summary = data.summary || {};
  const priorities = data.plan_review_items || data.request_review_items || [];
  const scenarios = data.suggested_scenarios || [];
 const modifications = data.plan_modifications || [];
 const modApplied = data.modifications_applied || 0;
  const modRejected = data.modifications_rejected || 0;
  const provider = data.provider || {};
  const aiError = provider.error || "";
  const aiNote = provider.note || "";
  const modPageSize = Number(state.planModsPageSize || 10);
  const modTotalPages = Math.max(1, Math.ceil(modifications.length / modPageSize));
  state.planModsPage = Math.min(Math.max(1, Number(state.planModsPage || 1)), modTotalPages);
  const modStart = (state.planModsPage - 1) * modPageSize;
  const visibleMods = modifications.slice(modStart, modStart + modPageSize);

  const expanded = Boolean(state.aiPlanExpanded);
  box.hidden = false;
  box.innerHTML = `
    <div class="aiInsightHead">
      <div>
        <strong>AI Agent 计划审查</strong>
        ${modApplied ? `<span>已自动优化 ${modApplied} 条计划项</span>` : aiError ? `<span class="aiWarn">${escapeHtml(aiNote)}</span>` : `<span>${escapeHtml(aiNote)}</span>`}
        ${aiError && !modApplied ? `<small class="aiErrorDetail" title="${escapeHtml(aiError)}">${escapeHtml(aiError.slice(0, 150))}</small>` : ""}
      </div>
      <div class="aiInsightActions">
        <div class="aiBadges">
          <span>P0 ${summary.p0 || 0}</span>
          <span>P1 ${summary.p1 || 0}</span>
          <span>P2 ${summary.p2 || 0}</span>
        </div>
        <button class="ghost miniToggle" type="button" data-ai-toggle="plan">${expanded ? "收起详情" : "展开详情"}</button>
      </div>
    </div>
    <div class="aiInsightBody" ${expanded ? "" : "hidden"}>
    <div class="aiInsightGrid">
      ${modifications.length ? `
      <div class="spanTwo">
        <h4>AI Agent 修改记录</h4>
        <p class="hint">这里仅展示已实际应用到执行计划的改动。后端按“角色 + 方法 + 接口路径”去重，不会因分类不同重复新增。</p>
        ${modRejected ? `<p class="hint">另有 ${modRejected} 条 AI 建议因重复或不在当前范围内而未应用。</p>` : ""}
        <div class="modList">
          ${visibleMods.map(mod => {
            const actLabel = mod.action === "add" ? "新增" : mod.action === "remove" ? "删除" : "修改";
            return `<div class="modItem modItem--${mod.action}">
              <span class="modTag">${actLabel}</span>
              <b>${escapeHtml(mod.actor || "")} ${escapeHtml(mod.method || "")} ${escapeHtml(mod.path || "")}</b>
              <p>${escapeHtml(mod.reason || "")}</p>
            </div>`;
          }).join("")}
        </div>
        ${modTotalPages > 1 ? `
          <div class="modPager">
            <span>第 ${state.planModsPage} / ${modTotalPages} 页，共 ${modifications.length} 条，每页 ${modPageSize} 条</span>
            <div class="pager">
              <button class="ghost" type="button" data-mod-page="prev" ${state.planModsPage <= 1 ? "disabled" : ""}>上一页</button>
              <button class="ghost" type="button" data-mod-page="next" ${state.planModsPage >= modTotalPages ? "disabled" : ""}>下一页</button>
            </div>
          </div>` : ""}
      </div>` : ""}
      <div>
        <h4>接口请求列表审查</h4>
        ${priorities.slice(0, 8).map(item => `
          <div class="aiInsightItem">
            <b>${escapeHtml(item.priority || "")} ${escapeHtml(item.method || "")} ${escapeHtml(item.path || item.name || "")}</b>
            <span>${escapeHtml(item.reason || "")}</span>
            <p>${escapeHtml(item.suggestion || item.plan_fix || "")}</p>
          </div>
        `).join("") || `<p class="hint">暂无需要调整的计划项。</p>`}
      </div>
      <div>
        <h4>计划补强建议</h4>
        ${scenarios.slice(0, 5).map(item => `
          <div class="aiInsightItem">
            <b>${escapeHtml(item.priority || "")} ${escapeHtml(item.name || "")}</b>
            <span>${escapeHtml(item.target || "")}</span>
          </div>
        `).join("") || `<p class="hint">暂无场景建议。</p>`}
      </div>
    </div>
    </div>`;
  box.querySelector('[data-ai-toggle="plan"]')?.addEventListener("click", () => {
    state.aiPlanExpanded = !state.aiPlanExpanded;
    renderAiPlanInsights(state.aiPlanInsights || {});
  });
  box.querySelector('[data-mod-page="prev"]')?.addEventListener("click", () => {
    state.planModsPage -= 1;
    renderAiPlanInsights(state.aiPlanInsights || {});
  });
  box.querySelector('[data-mod-page="next"]')?.addEventListener("click", () => {
    state.planModsPage += 1;
    renderAiPlanInsights(state.aiPlanInsights || {});
  });
}

/** 为耗时交互统一管理按钮禁用、耗时提示、成功和异常收尾。 */
async function runWithElapsed(buttonId, statusId, runningText, task, estimateText = "") {
  const button = $(buttonId);
  const status = statusId ? $(statusId) : null;
  const originalHtml = button ? button.innerHTML : "";
  const started = Date.now();
  let timer = null;
  const renderText = (seconds) => {
    const suffix = estimateText ? `，预计 ${estimateText}` : "";
    return `${runningText}，已等待 ${seconds}s${suffix}`;
  };
  if (button) {
    button.disabled = true;
    button.innerHTML = `<span class="loadingSpinner"></span>${escapeHtml(runningText)} 0s`;
  }
  if (status) status.textContent = renderText(0);
  timer = window.setInterval(() => {
    const seconds = Math.floor((Date.now() - started) / 1000);
    if (button) button.innerHTML = `<span class="loadingSpinner"></span>${escapeHtml(runningText)} ${seconds}s`;
    if (status) status.textContent = renderText(seconds);
  }, 1000);
  try {
    return await task();
  } finally {
    if (timer) window.clearInterval(timer);
    if (button) {
      button.disabled = false;
      button.innerHTML = originalHtml;
    }
  }
}

async function analyzePlanWithAi() {
  if (!state.plan || !Array.isArray(state.plan.cases) || !state.plan.cases.length) {
    state.plan = await api("/api/plan", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(collectProject()),
    });
    renderPlan(state.plan);
  }
  await runWithElapsed("aiAnalyzePlan", null, "AI 分析中", async () => {
    const result = await api("/api/ai/analyze-plan", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({project: collectProjectForAi(), plan: state.plan, ai_config: collectAiSettings()}),
    });
    state.aiPlanExpanded = false;
    state.planModsPage = 1;
    renderAiPlanInsights(result);
    logAiActions("AI 计划审查完成", result);
    chat("AI 计划审查已生成：已按接口风险和覆盖缺口给出修改建议。");
  });
}

async function analyzeRunWithAi() {
  if (!state.runResults.length) return chat("请先执行 API 测试，生成结果后再做 AI 分析。");
  $("aiRunStatus").className = "aiStatusLine";
  await runWithElapsed("aiAnalyzeRun", "aiRunStatus", "AI 分析执行结果中", async () => {
    const result = await api("/api/ai/analyze-run", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({project_id: state.project_id, run_summary: state.runSummary, ai_config: collectAiSettings()}),
    });
    state.aiRunExpanded = false;
    renderAiRunInsights(result);
    logAiActions("AI 执行结果深度复核完成", result);
    $("aiRunStatus").textContent = "AI 分析完成，可查看下方建议或打开大卡片报告。";
    chat("AI 执行结果分析已生成，复测优先级已整理。");
  });
}

function logAiActions(prefix, result = {}) {
  const provider = result.provider || {};
  const actions = result.ai_actions || [];
  const endpointFindings = result.endpoint_findings || [];
  const summary = typeof result.summary === "string"
    ? result.summary
    : (result.summary?.risk_text || result.report_brief || "");
  if (summary) toolLog(`${prefix}：${summary}`);
  actions.slice(0, 4).forEach(action => toolLog(`AI 优化动作：${action}`));
  endpointFindings.slice(0, 5).forEach(item => {
    toolLog(`AI 接口审查：${item.priority || ""} ${item.method || ""} ${item.path || ""} · ${item.issue || ""}；${item.suggestion || ""}`);
  });
  if (endpointFindings.length > 5) {
    toolLog(`AI 接口审查：还有 ${endpointFindings.length - 5} 条发现未展开，可查看接口导入审查返回结果。`);
  }
  if (provider.note) toolLog(`AI 引擎状态：${provider.note}`);
  if (provider.error) toolLog(`AI 回退原因：${provider.error}`, "error");
}

async function analyzeConfigWithAi(project = collectProject()) {
  const result = await api("/api/ai/analyze-config", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({project, ai_config: collectAiSettings()}),
  });
  logAiActions("AI 基础配置理解完成", result);
  return result;
}

async function analyzeImportWithAi(endpoints = state.endpoints) {
  if (!endpoints.length) return null;
  const result = await api("/api/ai/analyze-import", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({project: collectProjectForAi(), endpoints, ai_config: collectAiSettings()}),
  });
  logAiActions("AI 接口导入审查完成", result);
  return result;
}

/**
 * 接收后端稳定运行摘要，更新 API/场景/前端/断言四类结果面板。
 * 这里只渲染结论，不重新计算 PASS/FAIL，避免浏览器和后端断言规则漂移。
 */
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
