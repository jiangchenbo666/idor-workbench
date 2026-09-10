# 代码注释与业务链路阅读指南

这份指南用于把代码内新增的中文注释串成一条完整链路。阅读时以函数实现和自动化测试为准；注释解释设计意图、数据归属和安全边界，但不能替代测试。

## 1. 建议阅读顺序

### 1.1 启动、目录与数据库

1. `foundation/settings.py::get_settings()`：查看进程级默认值怎样从环境变量进入系统。
2. `foundation/paths.py`：理解项目、数据和静态目录的唯一来源。
3. `foundation/database.py::WorkbenchDB.connect()`：理解事务提交、回滚和连接关闭。
4. `WorkbenchDB.project_owned_by()`、`source_file_accessible_by()`、`get_task()`：理解项目、上传文件和任务的授权关系。

SQLite 只保存可检索元数据，大文件落在项目目录。数据库记录和文件产物共同组成一次执行，不能只复制其中一部分。

### 1.2 HTTP 入口与授权边界

从 `views/api.py` 按以下顺序阅读：

1. `_safe_id()`、`_project_dir()`、`_resolve_source_path()`：路径安全。
2. `_current_user()`、`_require_user()`、`_require_owned_project()`：认证和项目授权。
3. `_store_upload_file()`、`_canonicalize_project_sources()`、`_archive_project_sources()`：上传文件从临时证据变成项目证据。
4. `save_project()`：浏览器草稿变成服务端可信项目的核心边界。
5. `_make_plan()`、`_apply_plan_modifications()`：规则基线和 AI 建议的职责分离。
6. `_enqueue_task()`、`_run_background_task()`、`get_task()`：长任务状态机。
7. `_execute_api_run()`、`_archive_run()`：执行和不可变历史归档。

必须始终记住：`project_id`、`task_id`、`run_id` 和 `source_id` 只是资源标识，不天然代表访问权限。每个读取或写入入口都要重新验证当前用户。

## 2. 一次 API 测试的完整链路

```text
前端 run()
  -> POST /api/projects/{id}/run
  -> api.py::run_project()
  -> _require_owned_project()
  -> _enqueue_task()
  -> _execute_api_run()
  -> runner.run_api_project()
       -> prepare_business_scenarios()
       -> scenarios.execute_scenarios()
       -> execution.collect_all_results()
       -> assertions.update_profile()/classify_response()
       -> ai.analyze_run()
       -> write_execution_artifacts()
  -> api.py::_summarize_run_results()
  -> _archive_run()
  -> 前端 waitForTask()
  -> renderRunSummary()/renderRunPage()
```

这里有三类结果，不要混淆：

- 场景结果：是否成功造数、查询资源 ID、清理数据。
- API 断言结果：某角色访问某接口是否符合允许/拒绝预期。
- AI 复核结果：对失败、低置信或疑似越权结果的语义增强，不代替原始 HTTP 证据。

## 3. 断言为什么不能只看 HTTP 状态码

被测系统可能返回：

- `401/403`：明确被后端拒绝。
- `302`：跳转到登录或无权限页。
- `200 + code=403`：HTTP 成功，但业务拒绝。
- `200 + success=true + 真实业务数据`：无权角色可能已经成功读取资源。

`assertions.py` 会把自身权限样本和应被拦截样本分开学习。`execution.py::_classify_row()` 先更新学习档案，再统一产生 verdict、confidence 和 evidence。前端只展示这些字段，不能自行重算 PASS/FAIL。

## 4. 业务场景与写操作安全

`scenarios.execute_scenarios()` 在正式权限扫描前运行：

1. 登录场景角色。
2. 检查场景是否允许写操作。
3. DELETE 再检查步骤级确认。
4. 执行请求并通过小型 JSONPath 提取变量。
5. 把变量传给正式 API 扫描，替换 `{{resource_id}}` 等占位符。

“场景允许写”与“某个 DELETE 已确认”是两级门禁。新增写操作工具时不能绕过这两个判断。

## 5. 接口导入与人工录制

导入链路：

```text
HAR / Collection / cURL / PRD / 用例
  -> import_file()/import_curl()
  -> 服务端解析为统一 Endpoint
  -> 前端 mergeImportedEndpoints()
  -> 用户确认 allowed_roles/page_url
  -> saveProject()
  -> 服务端按 source_id 重新取可信文件元数据
```

人工录制链路：

```text
startManualRecording()
  -> ManualRecordingSession
  -> 用户在可见浏览器操作
  -> page.on("request") 采集网络证据
  -> 记录角色、页面、菜单章节、样例响应
  -> stopManualRecording()
  -> recordingEndpointsToProjectEndpoints()
  -> needs_review=true
```

“某角色录到接口”只证明该角色曾访问，不自动证明该角色有权。录制结果因此保留 `observed_roles/discovered_by`，同时要求用户确认 `allowed_roles`。

## 6. 前端状态与双向映射

`webapp/static/app.js` 采用原生 JavaScript 状态驱动：

- `state`：当前浏览器草稿、筛选和服务端结果。
- `collectProject()`：DOM/state 转成项目请求 DTO。
- `fillProject()`：服务端项目快照反向填回 DOM/state。
- `markDirty()`：只标记草稿已变化。
- `markPlanStale()`：角色、目标或接口变化后标记旧计划不再可信。
- `saveProject()`：只有后端保存成功才清除 dirty。

增加项目字段时至少检查 `collectProject()`、`fillProject()`、后端保存模型和对应测试四处。只改一边会出现“界面能填但刷新丢失”或“后端有值但界面不展示”。

所有进入 `innerHTML` 的动态值都必须经过 `escapeHtml()`。`renderRunPage()` 的 Playwright 回归测试会注入恶意 `<img onerror>`，用于防止以后重构重新引入 DOM 注入。

## 7. AI 审查边界

`ai.py` 的统一策略是：

1. 本地规则先产生确定性结论。
2. `sync_model` 开启时才等待模型语义增强。
3. 模型输出必须解析为 JSON。
4. 超时、鉴权或连接失败时返回本地规则结果和诊断。
5. 计划修改还要经过 `_apply_plan_modifications()` 白名单校验。

当前 AI 是“审查与增强层”，不是完整闭环 Agent。后续 RAG/Agent 重构仍需 Finding、ApprovalRequest、Retest、FeedbackLabel 和检索 ACL 等业务对象。

## 8. 前端拦截检测与 API 测试的区别

`frontend_probe.run_frontend_checks()` 判断页面层是否正确拦截：

- 页面 HTTP 401/403；
- 跳转到 login/403/forbidden；
- 弹出无权限提示；
- 页面出现拒绝关键词；
- 有权角色是否被误拦截。

它不能证明后端接口安全。企业验收必须同时运行 API 权限测试和页面拦截测试：前端隐藏按钮不是后端授权，后端拒绝也不能替代良好的页面交互。

## 9. 调试检查点

遇到“点击后数据没有展示”时，按下面顺序检查：

1. 浏览器 Network 是否发出正确 method/path/payload。
2. FastAPI 路由是否通过认证和项目所有权校验。
3. 后台任务是否进入 done/failed，而不是长期 running。
4. 项目目录是否产生预期 JSON/日志/截图。
5. `_summarize_run_results()` 是否保留前端需要字段。
6. 前端是否把响应写进正确的 `state` 字段。
7. 对应 `render*()` 是否被调用，筛选/分页是否把行隐藏。
8. 动态值是否经过 `escapeHtml()`，控制台是否有异常。

修改后运行：

```powershell
.\.venv\Scripts\python.exe scripts\quality.py
node --check webapp\static\app.js
```

关键交互还要运行两条 Playwright 脚本，单元测试不能替代真实浏览器验证。
