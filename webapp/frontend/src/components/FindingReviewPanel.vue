<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../api/client'

const props = defineProps({ projectId: { type: String, required: true } })
const emit = defineEmits(['updated'])

const items = ref([])
const loading = ref(false)
const error = ref('')
const filter = ref('')
const activeFinding = ref(null)
const expandedFindingIds = ref(new Set())
const form = ref(emptyForm())

const statusMeta = {
  needs_human_review: ['待人工复核', 'pending'],
  confirmed: ['确认漏洞', 'danger'],
  false_positive: ['工具误报', 'muted'],
  waiting_fix: ['等待修复', 'warning'],
  retest_passed: ['复测通过', 'success'],
  closed: ['已关闭', 'closed'],
}

const filteredItems = computed(() => {
  if (!filter.value) return items.value
  return items.value.filter(item => item.status === filter.value)
})

const counts = computed(() => {
  return items.value.reduce((all, item) => {
    all[item.status] = (all[item.status] || 0) + 1
    return all
  }, {})
})

const title = computed(() => {
  return {
    review: '人工复核',
    waiting_fix: '登记修复版本',
    retest: '记录复测结论',
    close: '关闭风险卡片',
  }[form.value.action] || '处理风险卡片'
})

function emptyForm() {
  return {
    action: '',
    decision: 'confirmed',
    note: '',
    fixVersion: '',
    retestRunId: '',
    passed: true,
  }
}

function meta(status) {
  return statusMeta[status] || [status || '未知状态', 'muted']
}

function isActive(item) {
  return activeFinding.value?.finding_id === item.finding_id
}

function closeForm() {
  activeFinding.value = null
  form.value = emptyForm()
}

function openForm(finding, action) {
  error.value = ''
  activeFinding.value = finding
  form.value = { ...emptyForm(), action }
}

function toggleRetestHistory(findingId) {
  // Set 里保存的是“当前展开了复测历史”的风险编号。
  if (expandedFindingIds.value.has(findingId)) {
    expandedFindingIds.value.delete(findingId)
  } else {
    expandedFindingIds.value.add(findingId)
  }
}

async function load() {
  if (!props.projectId) return

  loading.value = true
  error.value = ''

  try {
    const result = await api(`/api/projects/${encodeURIComponent(props.projectId)}/findings`)
    items.value = result.items || []
  } catch (requestError) {
    error.value = `读取风险卡片失败：${requestError.message}`
  } finally {
    loading.value = false
  }
}

function payload() {
  if (form.value.action === 'review') {
    return {
      action: 'review',
      decision: form.value.decision,
      note: form.value.note.trim(),
    }
  }

  if (form.value.action === 'waiting_fix') {
    return {
      action: 'waiting_fix',
      fix_version: form.value.fixVersion.trim(),
    }
  }

  if (form.value.action === 'retest') {
    return {
      action: 'retest',
      retest_run_id: form.value.retestRunId.trim(),
      passed: form.value.passed,
      note: form.value.note.trim(),
    }
  }

  return {
    action: 'close',
    note: form.value.note.trim(),
  }
}

async function submit() {
  if (!activeFinding.value) return

  if (form.value.action === 'waiting_fix' && !form.value.fixVersion.trim()) {
    error.value = '请填写修复版本。'
    return
  }

  if (form.value.action === 'retest' && !form.value.retestRunId.trim()) {
    error.value = '请填写复测运行编号。'
    return
  }

  if (form.value.action !== 'waiting_fix' && !form.value.note.trim()) {
    error.value = '请填写处理说明；这会成为风险审计记录。'
    return
  }

  loading.value = true
  error.value = ''

  try {
    const result = await api(
      `/api/projects/${encodeURIComponent(props.projectId)}/findings/${encodeURIComponent(activeFinding.value.finding_id)}/transition`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload()),
      },
    )

    const index = items.value.findIndex(item => item.finding_id === result.item.finding_id)
    if (index !== -1) items.value.splice(index, 1, result.item)

    closeForm()
    emit('updated', result.item)
  } catch (requestError) {
    error.value = `状态更新失败：${requestError.message}`
  } finally {
    loading.value = false
  }
}

watch(() => props.projectId, () => {
  closeForm()
  expandedFindingIds.value.clear()
  load()
})

onMounted(load)
</script>

<template>
  <section class="finding-panel">
    <header class="panel-head">
      <div>
        <p class="eyebrow">FINDING WORKFLOW</p>
        <h2>人工复核池</h2>
        <p>只处理真实失败的越权执行结果，每一步均由后端状态机校验。</p>
      </div>

      <div>
        <label>
          筛选
          <select v-model="filter">
            <option value="">全部（{{ items.length }}）</option>
            <option
              v-for="(_, status) in counts"
              :key="status"
              :value="status"
            >
              {{ meta(status)[0] }}（{{ counts[status] }}）
            </option>
          </select>
        </label>
        <button type="button" @click="load">刷新</button>
      </div>
    </header>

    <p v-if="error" class="notice notice--error">{{ error }}</p>
    <p v-else-if="loading && !items.length" class="empty">正在读取风险卡片...</p>
    <p v-else-if="!filteredItems.length" class="empty">
      没有风险卡片。执行发现 FAIL 越权用例后会自动生成。
    </p>

    <div v-else class="finding-grid">
      <article
        v-for="item in filteredItems"
        :key="item.finding_id"
        class="finding-card"
        :class="{ 'finding-card--active': isActive(item) }"
      >
        <header>
          <strong>{{ item.finding_id }}</strong>
          <span class="badge" :class="'badge--' + meta(item.status)[1]">
            {{ meta(item.status)[0] }}
          </span>
        </header>

        <code>{{ item.endpoint }}</code>

        <dl>
          <div>
            <dt>测试角色</dt>
            <dd>{{ item.actor_role }}</dd>
          </div>
          <div>
            <dt>HTTP</dt>
            <dd>{{ item.actual_status_code }}</dd>
          </div>
          <div>
            <dt>预期</dt>
            <dd>{{ item.expected_result }}</dd>
          </div>
          <div>
            <dt>断言依据</dt>
            <dd>{{ item.assertion_reason }}</dd>
          </div>
        </dl>

        <p><b>响应：</b>{{ item.actual_response_summary }}</p>
        <p v-if="item.human_note"><b>人工结论：</b>{{ item.human_note }}</p>
        <p v-if="item.fix_version"><b>修复版本：</b>{{ item.fix_version }}</p>
        <p v-if="item.retest_note">
          <b>最近复测：</b>{{ item.retest_run_id }} · {{ item.retest_note }}
        </p>

        <section v-if="item.retest_history?.length" class="retest-history">
          <button
            type="button"
            class="ghost"
            @click="toggleRetestHistory(item.finding_id)"
          >
            {{ expandedFindingIds.has(item.finding_id) ? '收起' : '展开' }}复测历史（{{ item.retest_history.length }}）
          </button>

          <ul
            v-if="expandedFindingIds.has(item.finding_id)"
            class="retest-history-list"
          >
            <li
              v-for="(record, index) in item.retest_history"
              :key="index"
              class="retest-history-item"
            >
              <strong>第 {{ index + 1 }} 次复测</strong>
              <span>修复版本：{{ record.fix_version || '未记录' }}</span>
              <span>运行编号：{{ record.retest_run_id || '未记录' }}</span>
              <span>复测结论：{{ record.passed ? '通过' : '失败' }}</span>
              <span v-if="record.note">备注：{{ record.note }}</span>
            </li>
          </ul>
        </section>

        <footer>
          <button
            v-if="item.status === 'needs_human_review'"
            type="button"
            @click="openForm(item, 'review')"
          >
            人工复核
          </button>

          <template v-else-if="['confirmed', 'waiting_fix'].includes(item.status)">
            <button type="button" @click="openForm(item, 'waiting_fix')">登记修复</button>
            <button
              v-if="item.status === 'waiting_fix'"
              type="button"
              @click="openForm(item, 'retest')"
            >
              记录复测
            </button>
          </template>

          <button
            v-else-if="['retest_passed', 'false_positive'].includes(item.status)"
            type="button"
            @click="openForm(item, 'close')"
          >
            关闭卡片
          </button>

          <span v-else>流程已完成</span>
        </footer>

        <form
          v-if="isActive(item)"
          class="transition-form"
          @submit.prevent="submit"
        >
          <header>
            <strong>{{ title }} · {{ activeFinding.finding_id }}</strong>
            <button type="button" @click="closeForm">×</button>
          </header>

          <label v-if="form.action === 'review'">
            复核结论
            <select v-model="form.decision">
              <option value="confirmed">确认漏洞</option>
              <option value="false_positive">工具误报</option>
            </select>
          </label>

          <label v-if="form.action === 'waiting_fix'">
            修复版本
            <input v-model="form.fixVersion" placeholder="例如 1.0.2 / commit-abc123" />
          </label>

          <template v-if="form.action === 'retest'">
            <label>
              复测运行编号
              <input v-model="form.retestRunId" placeholder="R-20260906-001" />
            </label>

            <label>
              复测结论
              <select v-model="form.passed">
                <option :value="true">已修复，复测通过</option>
                <option :value="false">仍可越权，复测失败</option>
              </select>
            </label>
          </template>

          <label v-if="form.action !== 'waiting_fix'">
            处理说明
            <textarea
              v-model="form.note"
              rows="3"
              placeholder="写清判断依据、修复范围或复测证据"
            />
          </label>

          <div class="form-actions">
            <button type="submit" class="primary" :disabled="loading">保存状态</button>
            <button type="button" @click="closeForm">取消</button>
          </div>
        </form>
      </article>
    </div>
  </section>
</template>
