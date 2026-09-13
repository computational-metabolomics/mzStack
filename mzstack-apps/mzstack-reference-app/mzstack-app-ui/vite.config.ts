import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

const API = process.env.MZSTACK_API_URL ?? 'http://127.0.0.1:5000'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    port: 5173,
    // The API runs as its own process in development. Proxying keeps the
    // browser on one origin, as in production where Flask serves this build.
    proxy: {
      '/api': { target: API, changeOrigin: true },
    },
  },
})
