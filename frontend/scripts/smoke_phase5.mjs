/**
 * smoke_phase5.mjs — headless-Chromium browser smoke for the Phase 5
 * two-participant browser MVP (ADR-0014).
 *
 * Opens TWO real pages against the single-port server:
 *   - a HOST page (translator user, configures ur→en);
 *   - a GUEST page opened via the shared invite link (?room=CODE).
 * The guest must see NO language settings; both sides must see the peer
 * presence badge flip to "connected"; both must record without crashing;
 * the transcript must exist on both. Chrome's fake microphone provides the
 * audio (a tone — the server honestly answers no_speech_detected, which is
 * the only acceptable banner, exactly like the Phase 2 smoke).
 *
 * Usage (from the repo root or frontend/):
 *   npm run build && bash start.sh &   # http://localhost:3000
 *   npx playwright install chromium    # once
 *   node frontend/scripts/smoke_phase5.mjs [--url URL] [--out DIR]
 *
 * Exit code 0 = all checks passed. Screenshots land in --out (default:
 * docs/screenshots/phase5, tracked in git).
 */
import { mkdir } from 'node:fs/promises'
import path from 'node:path'
import { chromium } from 'playwright'

const args = process.argv.slice(2)
function argOf(name, fallback) {
  const i = args.indexOf(name)
  return i >= 0 && args[i + 1] ? args[i + 1] : fallback
}
const BASE_URL = argOf('--url', 'http://localhost:3000')
const OUT_DIR = argOf('--out', 'docs/screenshots/phase5')

const results = []
const consoleErrors = []
let browser
let passed = 0

function check(id, name, ok, note = '') {
  results.push({ id, name, ok, note })
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${id}  ${name}${note ? ` — ${note}` : ''}`)
  if (ok) passed += 1
}

function watchConsole(page, label) {
  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(`${label}: ${msg.text()}`)
  })
  page.on('pageerror', (err) => consoleErrors.push(`${label}: ${String(err)}`))
}

try {
  browser = await chromium.launch({
    args: [
      '--use-fake-device-for-media-stream', // Chrome's fake mic (tone signal)
      '--use-fake-ui-for-media-stream', // auto-grant the permission prompt
    ],
  })
  const hostPage = await browser.newPage({ viewport: { width: 1280, height: 900 } })
  watchConsole(hostPage, 'host')

  // R1 — host renders with the invite panel and a room code
  await hostPage.goto(BASE_URL, { waitUntil: 'networkidle' })
  const codeLocator = hostPage.locator('.room-panel__code')
  await codeLocator.waitFor({ timeout: 12_000 })
  const code = ((await codeLocator.textContent()) ?? '').trim()
  check('R1', 'host renders with invite panel + room code', /^[A-Z0-9]{6}$/.test(code), `code=${code}`)

  // R2 — host WS connected
  await hostPage.getByText('Connected', { exact: true }).waitFor({ timeout: 10_000 })
  check('R2', 'host WebSocket connected', true)
  const waiting = await hostPage.getByText(/Waiting for the other participant/).isVisible()
  check('R3', 'host peer status: waiting before join', waiting)

  // R4 — guest opens the SHARED LINK: no language settings anywhere
  const guestPage = await browser.newPage({ viewport: { width: 390, height: 844 } })
  watchConsole(guestPage, 'guest')
  await guestPage.goto(`${BASE_URL}/?room=${code}`, { waitUntil: 'networkidle' })
  await guestPage.getByText('Joined session').waitFor({ timeout: 12_000 })
  const guestLanguagePanel = await guestPage.getByLabel('Language settings').count()
  check('R4', 'guest page: joined-session view, NO language settings', guestLanguagePanel === 0)
  const guestLanguages = await guestPage.locator('.room-panel--guest strong').textContent()
  check(
    'R5',
    'guest sees the HOST language pair (configured nothing)',
    /Urdu/.test(guestLanguages ?? '') && /English/.test(guestLanguages ?? ''),
    `pair=${(guestLanguages ?? '').trim()}`,
  )

  // R6 — host sees the peer flip to connected
  await hostPage.getByText(/Both participants connected/).waitFor({ timeout: 12_000 })
  check('R6', 'host peer status flips to connected', true)

  // R7 — guest connects too
  await guestPage.getByText('Connected', { exact: true }).waitFor({ timeout: 10_000 })
  check('R7', 'guest WebSocket connected', true)

  // R8/R9 — both record (fake mic) and stop without crashing; the only
  // acceptable banner is the honest no_speech_detected (a tone is not speech).
  await guestPage.getByRole('button', { name: /start speaking/i }).click()
  await guestPage.waitForTimeout(3_500)
  await guestPage.getByRole('button', { name: /stop speaking/i }).click()
  await guestPage
    .locator('.banner__code')
    .waitFor({ timeout: 25_000 })
    .catch(() => {})
  const guestBanner = ((await guestPage.locator('.banner__code').textContent().catch(() => '')) ?? '').replace(/ /g, '_')
  check('R8', 'guest records + stops; only the honest no-speech banner', guestBanner === '' || guestBanner === 'no_speech_detected', `banner=${guestBanner || 'none'}`)

  await hostPage.getByRole('button', { name: /start speaking/i }).click()
  await hostPage.waitForTimeout(3_500)
  await hostPage.getByRole('button', { name: /stop speaking/i }).click()
  await hostPage
    .locator('.banner__code')
    .waitFor({ timeout: 25_000 })
    .catch(() => {})
  const hostBanner = ((await hostPage.locator('.banner__code').textContent().catch(() => '')) ?? '').replace(/ /g, '_')
  check('R9', 'host records + stops; only the honest no-speech banner', hostBanner === '' || hostBanner === 'no_speech_detected', `banner=${hostBanner || 'none'}`)

  // R10 — transcript panels exist on both sides
  check('R10', 'transcript panels present (host + guest)',
    (await hostPage.getByLabel('Transcript').count()) === 1 &&
    (await guestPage.getByLabel('Transcript').count()) === 1)

  // R11 — screenshots: host desktop + guest mobile (the guest's real shape)
  await mkdir(OUT_DIR, { recursive: true })
  const hostPath = path.join(OUT_DIR, 'host-desktop.png')
  await hostPage.screenshot({ path: hostPath, fullPage: true })
  const guestPath = path.join(OUT_DIR, 'guest-mobile.png')
  await guestPage.screenshot({ path: guestPath, fullPage: true })
  check('R11', 'host desktop + guest mobile screenshots captured', true, `${hostPath}, ${guestPath}`)

  // R12 — zero console/page errors across BOTH pages
  check('R12', 'zero console/page errors (both pages)', consoleErrors.length === 0, consoleErrors.slice(0, 3).join(' | '))
} catch (err) {
  check('EXC', `unexpected failure: ${String(err).slice(0, 300)}`, false)
} finally {
  await browser?.close()
}

console.log(`\n${passed}/${results.length} checks passed`)
process.exit(passed === results.length ? 0 : 1)
