# 前端模块边界

这些文件仍以普通 `<script>` 顺序加载，因此不会引入构建步骤，也不会改变 FastAPI 的静态资源部署方式。

- `core.js`：全局状态、DOM 工具、认证、请求客户端、页面切换。
- `project.js`：项目配置、角色、接口池、页面路由、场景和项目列表。
- `planning.js`：执行计划、AI 审查和计划分页。
- `execution.js`：执行摘要、结果列表、详情、历史与左右对比。
- `integration.js`：项目保存、文件导入、人工录制、运行和报告。
- `bootstrap.js`：页面启动和所有事件绑定。它必须最后加载。

新写的功能应优先放入 `webapp/static/features/`。例如 `features/findings-review.js` 是一个独立的 Vue 功能区，只通过 `window.IDORWorkbench` 桥接既有工作台。

`legacy/app.before-module-split.js` 是拆分前的只读回滚副本；确认稳定后可在单独提交中删除。
