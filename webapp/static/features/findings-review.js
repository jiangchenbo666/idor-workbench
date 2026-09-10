/* global Vue, IDORWorkbench */
/**
 * Finding 人工复核 Vue 功能区。
 *
 * 这个文件刻意只依赖 window.IDORWorkbench 三个稳定入口：
 * getProjectId()、request()、refreshFindings()。
 * 因此未来把旧 app.js 拆成 ES modules 时，Finding UI 不需要跟着重写。
 */
(() => {
  const mountPoint = document.getElementById("findingReviewApp");
  if (!mountPoint || !window.Vue) return;

  const STATUS_META = {
    needs_human_review: {label: "待人工复核", tone: "pending"},
    confirmed: {label: "确认漏洞", tone: "danger"},
    false_positive: {label: "工具误报", tone: "muted"},
    waiting_fix: {label: "等待修复", tone: "warning"},
    retest_passed: {label: "复测通过", tone: "success"},
    closed: {label: "已关闭", tone: "closed"},
  };

  Vue.createApp({
    data() {
      return {
        items: [],
        loading: false,
        error: "",
        filter: "",
        activeFinding: null,
        form: this.emptyForm(),
      };
    },
    computed: {
      projectId() {
        return window.IDORWorkbench?.getProjectId?.() || "";
      },
      filteredItems() {
        if (!this.filter) return this.items;
        return this.items.filter(item => item.status === this.filter);
      },
      counts() {
        return this.items.reduce((result, item) => {
          result[item.status] = (result[item.status] || 0) + 1;
          return result;
        }, {});
      },
      formTitle() {
        const names = {
          review: "人工复核",
          waiting_fix: "登记待修复版本",
          retest: "记录复测结论",
          close: "关闭风险卡片",
        };
        return names[this.form.action] || "处理风险卡片";
      },
    },
    mounted() {
      window.addEventListener("idor:findings-changed", this.handleWorkspaceRefresh);
      this.loadFindings();
    },
    beforeUnmount() {
      window.removeEventListener("idor:findings-changed", this.handleWorkspaceRefresh);
    },
    methods: {
      emptyForm() {
        return {action: "", decision: "confirmed", note: "", fixVersion: "", retestRunId: "", passed: true};
      },
      statusMeta(status) {
        return STATUS_META[status] || {label: status || "未知状态", tone: "muted"};
      },
      handleWorkspaceRefresh(event) {
        if (!event.detail?.projectId || event.detail.projectId === this.projectId) this.loadFindings();
      },
      async loadFindings() {
        this.error = "";
        if (!this.projectId) {
          this.items = [];
          this.activeFinding = null;
          return;
        }
        this.loading = true;
        try {
          const result = await window.IDORWorkbench.request(`/api/projects/${encodeURIComponent(this.projectId)}/findings`);
          this.items = result.items || [];
          if (this.activeFinding) {
            this.activeFinding = this.items.find(item => item.finding_id === this.activeFinding.finding_id) || null;
          }
        } catch (error) {
          this.error = `读取 Finding 风险池失败：${error.message || error}`;
        } finally {
          this.loading = false;
        }
      },
      openForm(item, action) {
        this.error = "";
        this.activeFinding = item;
        this.form = {...this.emptyForm(), action};
      },
      cancelForm() {
        this.activeFinding = null;
        this.form = this.emptyForm();
      },
      buildPayload() {
        if (this.form.action === "review") return {action: "review", decision: this.form.decision, note: this.form.note.trim()};
        if (this.form.action === "waiting_fix") return {action: "waiting_fix", fix_version: this.form.fixVersion.trim()};
        if (this.form.action === "retest") {
          return {action: "retest", retest_run_id: this.form.retestRunId.trim(), passed: this.form.passed, note: this.form.note.trim()};
        }
        return {action: "close", note: this.form.note.trim()};
      },
      validateForm() {
        if (this.form.action === "waiting_fix" && !this.form.fixVersion.trim()) return "请填写修复版本。";
        if (this.form.action === "retest" && !this.form.retestRunId.trim()) return "请填写复测运行编号，例如 R-20260906-001。";
        if (!this.form.note.trim()) return "请留下处理说明；它会成为这张风险卡片的审计记录。";
        return "";
      },
      async submitTransition() {
        const validationError = this.validateForm();
        if (validationError) {
          this.error = validationError;
          return;
        }
        if (!this.activeFinding || !this.projectId) return;

        this.loading = true;
        this.error = "";
        try {
          const result = await window.IDORWorkbench.request(
            `/api/projects/${encodeURIComponent(this.projectId)}/findings/${encodeURIComponent(this.activeFinding.finding_id)}/transition`,
            {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(this.buildPayload())},
          );
          const index = this.items.findIndex(item => item.finding_id === result.item.finding_id);
          if (index >= 0) this.items.splice(index, 1, result.item);
          this.cancelForm();
          window.IDORWorkbench.refreshFindings();
        } catch (error) {
          this.error = `状态更新失败：${error.message || error}`;
        } finally {
          this.loading = false;
        }
      },
    },
    template: `
      <section class="findingPanel" aria-labelledby="findingPanelTitle">
        <div class="findingPanel__head">
          <div><h3 id="findingPanelTitle">Finding 人工复核池</h3><p>扫描失败的越权用例会自动生成风险卡片；每一次人工判断、修复和复测都沿状态机留痕。</p></div>
          <div class="findingPanel__actions">
            <label>状态筛选<select v-model="filter"><option value="">全部（{{ items.length }}）</option><option v-for="(_, status) in counts" :key="status" :value="status">{{ statusMeta(status).label }}（{{ counts[status] }}）</option></select></label>
            <button type="button" class="ghost" :disabled="loading" @click="loadFindings">刷新</button>
          </div>
        </div>
        <p v-if="!projectId" class="findingPanel__empty">请先保存或打开一个项目，风险卡片会按项目隔离。</p>
        <p v-else-if="loading && !items.length" class="findingPanel__empty">正在读取风险卡片…</p>
        <p v-else-if="error" class="findingPanel__error">{{ error }}</p>
        <p v-else-if="!filteredItems.length" class="findingPanel__empty">当前没有符合条件的 Finding。执行出现越权失败后，系统会自动创建卡片。</p>
        <div v-else class="findingList">
          <article v-for="item in filteredItems" :key="item.finding_id" class="findingCard">
            <div class="findingCard__head"><strong>{{ item.finding_id }}</strong><span class="findingStatus" :class="'findingStatus--' + statusMeta(item.status).tone">{{ statusMeta(item.status).label }}</span></div>
            <p class="findingCard__endpoint">{{ item.endpoint }}</p>
            <dl class="findingCard__evidence"><div><dt>测试角色</dt><dd>{{ item.actor_role }}</dd></div><div><dt>实际 HTTP</dt><dd>{{ item.actual_status_code }}</dd></div><div><dt>预期</dt><dd>{{ item.expected_result }}</dd></div><div><dt>断言依据</dt><dd>{{ item.assertion_reason }}</dd></div></dl>
            <p class="findingCard__response"><b>响应摘要：</b>{{ item.actual_response_summary }}</p>
            <p v-if="item.human_note" class="findingCard__note"><b>人工结论：</b>{{ item.human_note }}</p>
            <p v-if="item.fix_version" class="findingCard__note"><b>当前修复版本：</b>{{ item.fix_version }}</p>
            <p v-if="item.retest_note" class="findingCard__note"><b>最近复测：</b>{{ item.retest_run_id }} · {{ item.retest_note }}</p>
            <div v-if="item.retest_history?.length" class="findingCard__history">复测历史 {{ item.retest_history.length }} 次</div>
            <div class="findingCard__actions">
              <template v-if="item.status === 'needs_human_review'"><button type="button" @click="openForm(item, 'review')">人工复核</button></template>
              <template v-else-if="item.status === 'confirmed' || item.status === 'waiting_fix'"><button type="button" @click="openForm(item, 'waiting_fix')">{{ item.status === 'confirmed' ? '登记修复版本' : '更新修复版本' }}</button><button v-if="item.status === 'waiting_fix'" type="button" @click="openForm(item, 'retest')">记录复测</button></template>
              <template v-else-if="item.status === 'retest_passed' || item.status === 'false_positive'"><button type="button" @click="openForm(item, 'close')">关闭卡片</button></template>
              <span v-else class="findingCard__closed">流程已完成</span>
            </div>
          </article>
        </div>
        <div v-if="activeFinding" class="findingForm" role="dialog" aria-modal="true" :aria-label="formTitle">
          <div class="findingForm__head"><strong>{{ formTitle }} · {{ activeFinding.finding_id }}</strong><button type="button" class="iconBtn" aria-label="关闭" @click="cancelForm">×</button></div>
          <label v-if="form.action === 'review'">复核结论<select v-model="form.decision"><option value="confirmed">确认漏洞</option><option value="false_positive">工具误报</option></select></label>
          <label v-if="form.action === 'waiting_fix'">修复版本<input v-model="form.fixVersion" placeholder="例如 1.0.2 / commit-abc123"></label>
          <template v-if="form.action === 'retest'"><label>复测运行编号<input v-model="form.retestRunId" placeholder="例如 R-20260906-001"></label><label>复测结论<select v-model="form.passed"><option :value="true">已修复，复测通过</option><option :value="false">仍可越权，复测失败</option></select></label></template>
          <label>处理说明<textarea v-model="form.note" rows="3" placeholder="写清楚判断依据、修复范围或复测证据"></textarea></label>
          <p v-if="error" class="findingPanel__error">{{ error }}</p>
          <div class="findingForm__actions"><button type="button" @click="cancelForm">取消</button><button type="button" class="primary" :disabled="loading" @click="submitTransition">保存状态</button></div>
        </div>
      </section>
    `,
  }).mount(mountPoint);
})();
