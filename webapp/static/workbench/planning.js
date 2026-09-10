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
