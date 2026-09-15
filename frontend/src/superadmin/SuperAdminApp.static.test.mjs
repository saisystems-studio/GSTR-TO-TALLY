import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

const here = dirname(fileURLToPath(import.meta.url))
const srcRoot = resolve(here, '..')

test('SuperAdminApp page imports resolve to real files', () => {
  const app = readFileSync(resolve(here, 'SuperAdminApp.jsx'), 'utf8')
  const pageImports = [...app.matchAll(/import\s+\w+\s+from\s+'(\.\/pages\/[^']+)'/g)]
    .map(match => resolve(here, match[1]))

  assert.ok(pageImports.length > 2, 'expected SuperAdminApp to import admin pages')
  for (const filePath of pageImports) {
    assert.ok(existsSync(filePath), `${filePath} should exist`)
  }
})

test('root App delegates /superadmin paths to the isolated Super Admin app', () => {
  const app = readFileSync(resolve(srcRoot, 'App.jsx'), 'utf8')

  assert.match(app, /SuperAdminApp/, 'App should import/render SuperAdminApp')
  assert.match(app, /pathname\.startsWith\('\/superadmin'\)/, 'App should route /superadmin separately')
})

test('super admin login does not force password change before dashboard', () => {
  const app = readFileSync(resolve(here, 'SuperAdminApp.jsx'), 'utf8')

  assert.doesNotMatch(app, /status:\s*'must_change_password'/, 'login should not enter a forced password-change state')
  assert.doesNotMatch(app, /pushRoute\('change-password'\)/, 'session restore should not redirect to change-password')
  assert.match(app, /goTo\('dashboard'\)/, 'successful login should route to dashboard')
})

test('profile page exposes optional password change controls', () => {
  const profile = readFileSync(resolve(here, 'pages', 'Profile.jsx'), 'utf8')

  assert.match(profile, /changePassword/, 'Profile should call the password-change API')
  assert.match(profile, /Change Password/, 'Profile should render a change password option')
})

test('super admin sidebar keeps only final business menus', () => {
  const sidebar = readFileSync(resolve(here, 'components', 'SuperAdminSidebar.jsx'), 'utf8')

  for (const label of ['Dashboard', 'Customers', 'Licenses', 'Device Requests', 'Payments', 'Audit Logs', 'Settings']) {
    assert.match(sidebar, new RegExp(`label: '${label}'`), `${label} should stay in the sidebar`)
  }
  for (const removed of ['Companies', 'Plans', 'Subscriptions', 'Usage', 'Support', 'Product Licenses']) {
    assert.doesNotMatch(sidebar, new RegExp(`label: '${removed}'`), `${removed} should not be a separate sidebar menu`)
  }
})

test('dashboard upcoming expiries route to licenses', () => {
  const dashboard = readFileSync(resolve(here, 'pages', 'Dashboard.jsx'), 'utf8')

  assert.match(dashboard, /Upcoming Expiries/, 'Dashboard should show upcoming expiries')
  assert.match(dashboard, /View All Licenses/, 'Dashboard should expose the all licenses action')
  assert.match(dashboard, /onNavigate\('licenses'\)/, 'Dashboard expiry action should open Licenses')
})

test('customer detail is focused on customer license and device summaries', () => {
  const detail = readFileSync(resolve(here, 'pages', 'CustomerDetail.jsx'), 'utf8')

  for (const section of ['Customer Details', 'License Summary', 'Device Information']) {
    assert.match(detail, new RegExp(section), `${section} should be present`)
  }
  for (const removed of ['Registered Companies', 'Company Usage', 'Internal Notes', 'Audit History']) {
    assert.doesNotMatch(detail, new RegExp(removed), `${removed} should not be a primary customer detail section`)
  }
})

test('customer detail uses compact profile summary layout', () => {
  const detail = readFileSync(resolve(here, 'pages', 'CustomerDetail.jsx'), 'utf8')
  const styles = readFileSync(resolve(srcRoot, 'styles', 'super-admin.css'), 'utf8')

  assert.match(detail, /sa-customer-profile-card/, 'Customer detail should render a compact profile card')
  assert.doesNotMatch(detail, /<PageHeader/, 'Customer detail should not use the loose generic page header')
  assert.match(detail, /sa-customer-detail-grid/, 'Customer and license cards should use the dedicated 35/65 grid')
  assert.match(detail, /sa-device-info-card/, 'Device information should be a full-width horizontal card')
  assert.match(detail, /missingCustomerValue/, 'Customer missing fields should read Not Available')
  assert.match(detail, /missingLicenseValue/, 'License missing fields should read Not Set')
  assert.match(detail, /deviceNameValue/, 'Missing device name should read Not Registered')

  assert.match(styles, /\.sa-customer-detail-grid\s*\{[^}]*35fr[^}]*65fr/s, 'Desktop detail grid should favor License Summary width')
  assert.match(styles, /\.sa-device-grid\s*\{[^}]*repeat\(5,/s, 'Device card should use a five-column desktop grid')
  assert.doesNotMatch(styles, /sa-customer-detail[\s\S]*min-height:\s*(?:550|600)px/, 'Customer detail styles should not force giant card heights')
})

test('super admin shell supports compact profile and collapsible sidebar', () => {
  const shell = readFileSync(resolve(here, 'components', 'SuperAdminShell.jsx'), 'utf8')
  const sidebar = readFileSync(resolve(here, 'components', 'SuperAdminSidebar.jsx'), 'utf8')
  const styles = readFileSync(resolve(srcRoot, 'styles', 'super-admin.css'), 'utf8')

  assert.match(shell, /superadmin_sidebar_collapsed/, 'sidebar collapse state should persist in localStorage')
  assert.match(shell, /is-sidebar-collapsed/, 'workspace should expose collapsed state for layout resizing')
  assert.match(shell, />☰<\/button>/, 'topbar toggle should use a hamburger icon')
  assert.match(shell, /sa-mobile-sidebar-backdrop/, 'mobile drawer should close from a backdrop click')
  assert.doesNotMatch(shell, />ME<\/button>/, 'hamburger should not display the old ME text')

  assert.match(sidebar, /collapsed/, 'sidebar should receive collapsed state')
  assert.match(sidebar, /title=\{item\.label\}/, 'collapsed sidebar icons should expose tooltips')
  assert.match(sidebar, /sa-sidebar-label/, 'sidebar labels should be hideable in collapsed mode')
  assert.match(sidebar, /title="Logout"/, 'logout icon should expose a tooltip')

  assert.match(styles, /\.sa-sidebar\s*\{[^}]*width:\s*240px[^}]*transition:\s*width \.25s ease/s, 'expanded sidebar should be 240px with width transition')
  assert.match(styles, /\.sa-root\.is-sidebar-collapsed\s+\.sa-sidebar\s*\{[^}]*width:\s*72px/s, 'collapsed sidebar should be 72px')
  assert.match(styles, /\.sa-main\s*\{[^}]*width:\s*calc\(100% - 240px\)[^}]*transition:\s*margin-left \.25s ease,\s*width \.25s ease/s, 'main content should size against expanded sidebar')
  assert.match(styles, /\.sa-root\.is-sidebar-collapsed\s+\.sa-main\s*\{[^}]*width:\s*calc\(100% - 72px\)/s, 'main content should resize against collapsed sidebar')
  assert.match(styles, /\.sa-root\.is-sidebar-collapsed\s+\.sa-sidebar-label\s*\{[^}]*display:\s*none/s, 'collapsed sidebar should hide text labels')
  assert.match(styles, /\.sa-profile-chip\s*\{[^}]*padding:\s*8px 14px[^}]*border-radius:\s*999px/s, 'top profile pill should be compact')
})

test('step 3 verification popup blocks master confirmation on verification failure', () => {
  const page = readFileSync(resolve(srcRoot, 'pages', 'GstTallyImport.jsx'), 'utf8')
  const popup = readFileSync(resolve(srcRoot, 'components', 'gst-tally', 'Step3Verification.jsx'), 'utf8')
  const verification = readFileSync(resolve(srcRoot, 'utils', 'step3Verification.js'), 'utf8')
  assert.match(page, /Step3Verification/, 'Step 3 must render the verification popup')
  assert.match(popup, /step3Ready\(licenseResult\)/, 'Confirmation must require all independent checks')
  assert.match(popup, /disabled=\{!complete \|\| confirmDone\}/, 'Incomplete verification must disable confirmation')
  assert.match(popup, /errors\.map/, 'Every reported problem must be visible')
  assert.match(popup, /PRODUCT_LICENSE_NOT_CONFIGURED/, 'Missing registration must be explained')
  assert.match(verification, /registered_serial/, 'Registered serial must be visible')
  assert.match(verification, /detected_serial/, 'Detected serial must be visible')
})
