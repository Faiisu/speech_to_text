import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'VITE_')
  return {
    plugins: [react()],
    base: './',
    server: {
      proxy: {
        '/api': env.VITE_BACKEND_URL || 'http://localhost:8000',
      },
    },
    build: {
      outDir: 'dist',
      emptyOutDir: true,
    },
  }
})
