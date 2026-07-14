import { defineConfig } from 'vite'
import preact from '@preact/preset-vite'

export default defineConfig({
  plugins: [preact()],
  root: 'frontend',
  build: {
    outDir: '../static',
    emptyOutDir: true,
  },
})
