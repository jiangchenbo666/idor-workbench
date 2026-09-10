import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [vue()],
  // /vue 是 FastAPI 页面路由，构建后的 JS/CSS 仍从 /static/vue-app/ 取。
  base: '/static/vue-app/',
  build: {
    // 构建产物由现有 FastAPI 静态目录托管：访问 /vue 即可打开。
    outDir: '../static/vue-app',
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      // 开发时 Vite 页面仍然调用本地 FastAPI，而不是把 API 写死在组件中。
      '/api': 'http://127.0.0.1:8000',
    },
  },
})
