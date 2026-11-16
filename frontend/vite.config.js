import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { createReadStream, existsSync, statSync } from 'node:fs'
import { resolve } from 'node:path'

// Dev only: serve the connector installer. Without this, Vite's SPA fallback answers
// /gstr2tally/downloads/*.exe with index.html (HTTP 200, ~1 KB), which the browser saves as a
// "corrupted" .exe. The dev server serves the locally built DEV installer (loopback origin) or a clear 404.
const releaseDir = resolve(__dirname, '../local-tally-agent/release')
function connectorDownloads() {
  const handler = (req, res, next) => {
    const match = /^\/gstr2tally\/downloads\/([A-Za-z0-9._-]+\.exe)(?:\?.*)?$/.exec(req.url || '')
    if (!match) return next()
    const dev = resolve(releaseDir, match[1].replace(/\.exe$/i, '-DEV.exe'))
    if (!existsSync(dev) || statSync(dev).size < 1024 * 1024) {
      res.statusCode = 404
      res.setHeader('Content-Type', 'text/plain; charset=utf-8')
      res.end('Local connector installer not built. Run local-tally-agent/build-dev-installer.ps1')
      return
    }
    res.setHeader('Content-Type', 'application/octet-stream')
    res.setHeader('Content-Length', statSync(dev).size)
    res.setHeader('Content-Disposition', `attachment; filename="${match[1]}"`)
    createReadStream(dev).pipe(res)
  }
  // Do not return the connect app: Vite treats a returned function as a post-middleware hook.
  return {
    name: 'connector-downloads',
    configureServer(server) { server.middlewares.use(handler) },
    configurePreviewServer(server) { server.middlewares.use(handler) },
  }
}

export default defineConfig({
  plugins: [react(), connectorDownloads()],
  server: { port: 5173, strictPort: false, proxy: { '/api': 'http://127.0.0.1:8000' } },
})
