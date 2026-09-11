import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

const here = dirname(fileURLToPath(import.meta.url))
const srcRoot = resolve(here, '..', '..')

test('workspace header exposes replaceable GSTR 2 Tally branding', () => {
  const sidebar = readFileSync(resolve(here, 'Sidebar.jsx'), 'utf8')
  const brand = readFileSync(resolve(here, 'AppBrand.jsx'), 'utf8')

  assert.match(sidebar, /<AppBrand\s*\/>/, 'workspace navigation should render the isolated brand component')
  assert.match(brand, /className="brand-logo"/, 'brand should reserve a dedicated logo container')
  assert.match(brand, /<img[^>]+objectFit|<img[^>]+className="brand-logo-image"/, 'logo should be replaceable without changing layout')
  assert.match(brand, />GSTR 2 Tally</, 'brand should show the application name')
  assert.match(brand, />GST Return Data Processing</, 'brand should show the professional subtitle')
  assert.doesNotMatch(brand, /GST\s*(?:→|&rarr;|->)\s*Tally Import/, 'old branding must not appear')
})

test('workspace Home uses the login-producing logout flow', () => {
  const page = readFileSync(resolve(srcRoot, 'pages', 'GstTallyImport.jsx'), 'utf8')
  const sidebar = readFileSync(resolve(here, 'Sidebar.jsx'), 'utf8')

  assert.match(page, /onHome=\{onLogout\}/, 'Home should use the existing logout handler that navigates to /login')
  assert.match(sidebar, /item\.key === 'home'[^\n]+onHome\(\)/, 'the complete Home control should invoke onHome')
})

test('selected GSTR indicator alone uses the amber circle and white tick', () => {
  const styles = readFileSync(resolve(srcRoot, 'styles', 'gst-tally.css'), 'utf8')

  assert.match(styles, /\.app-workspace\s+\.return-check\s*\{[^}]*width:\s*22px[^}]*height:\s*22px[^}]*background:\s*#fdb73e[^}]*color:\s*#fff[^}]*transition:/si)
  assert.doesNotMatch(styles, /\.app-workspace\s+\.return-option\.active\s*\{[^}]*background:\s*#fdb73e/si, 'selected card must not become solid amber')
})

test('workspace theme mixes blue with mustard accents', () => {
  const styles = readFileSync(resolve(srcRoot, 'styles', 'gst-tally.css'), 'utf8')

  assert.match(styles, /--ws-primary:\s*#1F5FAE;/, 'workspace primary should use the deeper blue')
  assert.match(styles, /--ws-mustard:\s*#D6A21E;/, 'workspace should expose a mustard accent token')
  assert.match(styles, /\.app-sidebar\s*\{[^}]*linear-gradient\([^}]*var\(--ws-primary\)[^}]*var\(--ws-mustard\)/si, 'sidebar should blend blue with mustard')
  assert.match(styles, /\.sidebar-item\.active\s*\{[^}]*border-left:\s*3px solid var\(--ws-mustard\)/si, 'active sidebar item should carry the mustard accent')
  assert.match(styles, /\.process-progress span\s*\{[^}]*linear-gradient\(90deg,\s*#1F5FAE,\s*#D6A21E\)/si, 'processing progress should use matching blue and mustard')
})
