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
  if ($("usernameField")) $("usernameField").value = (project.auth || {}).username_field || "username";
  if ($("passwordField")) $("passwordField").value = (project.auth || {}).password_field || "password";
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
  if ($("usernameField")) $("usernameField").value = "username";
  if ($("passwordField")) $("passwordField").value = "password";
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

