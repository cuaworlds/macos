import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { runsApi } from './vite-plugins/runs-api'

// https://vite.dev/config/
// runsApi powers offline mode (VITE_DATA_SOURCE=local); it's dev-only and has no
// effect on production builds.
export default defineConfig({
  plugins: [react(), runsApi()],
})
