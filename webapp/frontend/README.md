# IDOR Workbench Vue 前端

这是标准 Vue 3 + Vite 前端工程，不是 `webapp/static/` 中的旧原生 JavaScript 页面。

## 源码入口

- `src/App.vue`：登录态、项目列表和工作台壳。
- `src/components/FindingReviewPanel.vue`：Finding 风险卡片、人工复核、修复、复测、关闭。
- `src/api/client.js`：唯一 HTTP 客户端。
- `src/style.css`：Vue 页面样式。

## 本地开发

先启动 FastAPI，再在此目录运行：

```bash
npm run dev
```

打开 Vite 输出的地址（默认 `http://localhost:5173`）。`/api` 请求会转发到本机 `8000` 端口的 FastAPI。

## 构建并由后端托管

```bash
npm run build
```

构建产物会写入 `webapp/static/vue-app/`。重启 FastAPI 后访问 `http://127.0.0.1:8000/vue`。

旧版全功能页面仍在 `/`；Vue 页面当前承接“项目选择 + Finding 人工复核闭环”，其余旧功能保留运行，等待按模块逐页迁移。
