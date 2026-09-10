<script setup>
import { onMounted, ref } from 'vue'
import { api } from './api/client'
import FindingReviewPanel from './components/FindingReviewPanel.vue'

const user = ref(null)
const projects = ref([])
const activeProject = ref(null)
const loading = ref(false)
const error = ref('')
const auth = ref({ username: '', password: '', confirmPassword: '' })

async function loadWorkspace() {
  loading.value = true
  error.value = ''
  try {
    const bootstrap = await api('/api/bootstrap')
    user.value = bootstrap.user || null
    if (user.value) await loadProjects()
  } catch (requestError) {
    error.value = `无法连接工作台：${requestError.message}`
  } finally {
    loading.value = false
  }
}

async function loadProjects() {
  const result = await api('/api/projects')
  projects.value = result.projects || []
}

async function openProject(projectId) {
  loading.value = true
  error.value = ''
  try {
    const result = await api(`/api/projects/${encodeURIComponent(projectId)}`)
    activeProject.value = result.project
  } catch (requestError) {
    error.value = `打开项目失败：${requestError.message}`
  } finally {
    loading.value = false
  }
}

async function submitAuth(mode) {
  error.value = ''
  try {
    const payload = { username: auth.value.username, password: auth.value.password }
    if (mode === 'register') payload.confirm_password = auth.value.confirmPassword
    const result = await api(`/api/auth/${mode}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    user.value = result.user
    auth.value = { username: '', password: '', confirmPassword: '' }
    await loadProjects()
  } catch (requestError) {
    error.value = `${mode === 'login' ? '登录' : '注册'}失败：${requestError.message}`
  }
}

async function logout() {
  await api('/api/auth/logout', { method: 'POST' })
  user.value = null
  projects.value = []
  activeProject.value = null
}

onMounted(loadWorkspace)
</script>

<template>
  <main class="app-shell">
    <header class="app-header">
      <div>
        <p class="eyebrow">IDOR WORKBENCH · VUE</p>
        <h1>越权风险复核工作台</h1>
        <p class="subtitle">项目维度隔离证据，人工确认后才进入修复与复测闭环。</p>
      </div>
      <a class="back-link" href="/">打开旧版全功能工作台</a>
    </header>

    <p v-if="error" class="notice notice--error">{{ error }}</p>
    <p v-if="loading" class="notice">正在加载…</p>

    <section v-if="!user" class="auth-card">
      <h2>登录后打开你的项目</h2>
      <label>账号<input v-model="auth.username" autocomplete="username" /></label>
      <label>密码<input v-model="auth.password" type="password" autocomplete="current-password" /></label>
      <label>确认密码（注册时需要）<input v-model="auth.confirmPassword" type="password" autocomplete="new-password" /></label>
      <div class="button-row">
        <button class="primary" @click="submitAuth('login')">登录</button>
        <button @click="submitAuth('register')">注册</button>
      </div>
    </section>

    <template v-else>
      <section class="workspace-head">
        <div><strong>{{ user.username }}</strong><span>已登录</span></div>
        <button @click="logout">退出登录</button>
      </section>

      <section class="project-layout">
        <aside class="project-list">
          <div class="section-title"><h2>历史项目</h2><button @click="loadProjects">刷新</button></div>
          <button
            v-for="project in projects"
            :key="project.project_id"
            class="project-item"
            :class="{ active: activeProject?.project_id === project.project_id }"
            @click="openProject(project.project_id)"
          >
            <strong>{{ project.project_name }}</strong>
            <span>{{ project.base_url || '未配置环境' }}</span>
            <small>{{ project.endpoints_count || 0 }} 个接口 · {{ project.updated_at || '未记录时间' }}</small>
          </button>
          <p v-if="!projects.length" class="empty">暂无项目。请在旧版工作台创建项目后回到这里复核。</p>
        </aside>

        <section class="workspace-main">
          <template v-if="activeProject">
            <div class="project-summary">
              <p class="eyebrow">当前项目</p>
              <h2>{{ activeProject.project_name }}</h2>
              <p>{{ activeProject.goal || '尚未填写测试目标' }}</p>
              <dl><div><dt>环境</dt><dd>{{ activeProject.base_url }}</dd></div><div><dt>接口</dt><dd>{{ activeProject.endpoints?.length || 0 }}</dd></div><div><dt>角色</dt><dd>{{ activeProject.roles?.length || 0 }}</dd></div></dl>
            </div>
            <FindingReviewPanel :project-id="activeProject.project_id" />
          </template>
          <div v-else class="empty-state"><h2>选择一个项目</h2><p>选择左侧项目后，可查看并处理该项目自动生成的 Finding 风险卡片。</p></div>
        </section>
      </section>
    </template>
  </main>
</template>
