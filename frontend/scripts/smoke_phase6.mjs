/**
 * smoke_phase6.mjs — headless-Chromium smoke for Phase 6 mobile/browser
 * compatibility (KI-004/KI-015 sandbox proxy).
 *
 * A real phone is not available in this sandbox, so the closest honest
 * proxy is full device emulation: iPhone-class viewport, touch screen,
 * mobile UA (the fake microphone stays — Chrome's tone signal). The run
 * asserts what the CODE controls on a mobile browser:
 *   - the page renders without horizontal overflow;
 *   - the PWA manifest is served and linked;
 *   - the mic starts and stops from TAP gestures;
 *   - backgrounding the tab stops the recording with an honest banner
 *     (the Phase 6 capture guard) instead of streaming silence;
 *   - an UPLINK BLACKOUT (every outgoing frame dropped — the dead-zone/
 *     tunnel analog) recovers to Connected after the link returns, instead
 *     of sticking in "Reconnecting…" forever (BUG-601 regression);
 *   - the self-reported platform tag reaches the server (/health);
 *   - zero console errors throughout.
 *
 * Usage (from the repo root):
 *   npm run build && bash start.sh &   # http://localhost:3000
 *   npx playwright install chromium    # once
 *   node frontend/scripts/smoke_phase6.mjs [--url URL] [--out DIR]
 *
 * Exit code 0 = all checks passed. Screenshots land in --out (default:
 * docs/screenshots/phase6, tracked in git).
 */
import { mkdir } from 'node:fs/promises'
import path from 'node:path'
import { chromium, devices } from 'playwright'

const args = process.argv.slice(2)
function argOf(name, fallback) {
  const i = args.indexOf(name)
  return i >= 0 && args[i + 1] ? args[i + 1] : fallback
}
const BASE_URL = argOf('--url', 'http://localhost:3000')
const OUT_DIR = argOf('--out', 'docs/screenshots/phase6')

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
  await mkdir(OUT_DIR, { recursive: true })
  browser = await chromium.launch({
    args: [
      '--use-fake-device-for-media-stream', // Chrome's fake mic (tone signal)
      '--use-fake-ui-for-media-stream', // auto-grant the permission prompt
    ],
  })

  // Full mobile emulation: iPhone viewport + touch + mobile Safari UA.
  const iphone = devices['iPhone 13']
  const page = await browser.newPage({ ...iphone })
  watchConsole(page, 'mobile')

  // M1 — page renders under device emulation
  await page.goto(BASE_URL, { waitUntil: 'networkidle' })
  await page.locator('.mic-button').waitFor({ timeout: 12_000 })
  check('M1', 'renders under iPhone emulation (mic button present)', true)

  // M2 — PWA manifest is linked and served; viewport meta intact
  const manifestHref = await page.locator('link[rel="manifest"]').getAttribute('href')
  let manifestOk = false
  if (manifestHref) {
    const res = await page.request.get(new URL(manifestHref, BASE_URL).href)
    manifestOk = res.ok() && ((await res.json()).short_name ?? '') === 'Translator'
  }
  const viewportMeta = await page
    .locator('meta[name="viewport"]')
    .getAttribute('content')
  check(
    'M2',
    'manifest linked + served, viewport-fit=cover meta present',
    manifestOk && (viewportMeta ?? '').includes('viewport-fit=cover'),
  )

  // M3 — no horizontal overflow at 390 px
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  )
  check('M3', 'no horizontal overflow at 390 px', overflow <= 1, `overflowPx=${overflow}`)

  // M4 — WS connected (host mode, default ur→en)
  await page.getByText('Connected', { exact: true }).waitFor({ timeout: 12_000 })
  check('M4', 'WebSocket connected on emulated mobile', true)

  // M5 — the platform tag reached the server (hello.client → /health).
  // QA round v0.6.3: assert >= 1, not strict equality — the deployment is
  // shared, sessions from other recent clients can still be counted while
  // being evicted (QA re-test 2026-11-08 observation).
  const health = await page.request.get(`${BASE_URL}/health`).then((r) => r.json())
  check(
    'M5',
    'self-reported platform tag in /health sessions.by_platform',
    (health?.sessions?.by_platform?.['ios-safari'] ?? 0) >= 1,
    `by_platform=${JSON.stringify(health?.sessions?.by_platform ?? {})}`,
  )

  // M6 — recording starts and stops from TAPS (fake mic tone; the server
  // honestly answers no_speech_detected — the only acceptable banner).
  await page.locator('.mic-button').tap()
  await page.getByText(/Hands-free listening|Push-to-talk recording/).waitFor({ timeout: 10_000 })
  check('M6', 'tap starts recording (status goes live)', true)
  await page.screenshot({ path: path.join(OUT_DIR, 'mobile-recording.png') })
  await page.waitForTimeout(2_500)
  await page.locator('.mic-button').tap()
  await page.locator('.banner__code').waitFor({ timeout: 25_000 }).catch(() => {})
  const banner = (
    (await page.locator('.banner__code').textContent().catch(() => '')) ?? ''
  ).replace(/ /g, '_')
  check('M7', 'tap stops recording; only the honest no-speech banner', banner === '' || banner === 'no_speech_detected', `banner=${banner || 'none'}`)

  // M8 — background-tab guard: hiding the tab mid-recording must stop the
  // capture with an honest banner (never stream silence to the server).
  await page.locator('.mic-button').tap()
  await page.getByText(/Hands-free listening|Push-to-talk recording/).waitFor({ timeout: 10_000 })
  await page.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', {
      get: () => 'hidden',
      configurable: true,
    })
    Object.defineProperty(document, 'hidden', { get: () => true, configurable: true })
    document.dispatchEvent(new Event('visibilitychange'))
  })
  await page
    .getByText(/Recording stopped — the browser suspended the tab/)
    .waitFor({ timeout: 8_000 })
  check('M8', 'backgrounding the tab stops the capture (honest banner)', true)
  await page.screenshot({ path: path.join(OUT_DIR, 'mobile-background-guard.png') })

  // M9 — BUG-601 regression: black out the UPLINK mid-session (message-level:
  // every outgoing frame is silently dropped at the JS boundary — the
  // dead-zone/tunnel analog), hold it past the pong timeout so the client
  // recycles the socket and its reconnect hello is dropped too, then restore
  // the link. The handshake must be able to save itself: the repeated hello
  // reaches the server (idempotent re-ack), the hello-ack watchdog and the
  // server's hello timeout recycle any dead handshake — the app must reach
  // Connected again, never stick in "Reconnecting…".
  await page.evaluate(() => {
    // M8 left the page emulating 'hidden' — restore it first
    Object.defineProperty(document, 'visibilityState', {
      get: () => 'visible',
      configurable: true,
    })
    Object.defineProperty(document, 'hidden', { get: () => false, configurable: true })
    document.dispatchEvent(new Event('visibilitychange'))
    if (!window.__wsSendPatched) {
      const originalSend = WebSocket.prototype.send
      WebSocket.prototype.send = function (data) {
        if (window.__uplinkBlackout) return // silently drop every outgoing frame
        return originalSend.call(this, data)
      }
      window.__wsSendPatched = true
    }
  })
  await page.locator('.mic-button').tap()
  await page.getByText(/Hands-free listening|Push-to-talk recording/).waitFor({ timeout: 10_000 })
  await page.evaluate(() => {
    window.__uplinkBlackout = true
  })
  // 14 s > pong timeout (10 s): close → reconnect → ITS hello is dropped too
  await page.waitForTimeout(14_000)
  await page.evaluate(() => {
    window.__uplinkBlackout = false
  })
  await page.getByText('Connected', { exact: true }).waitFor({ timeout: 30_000 })
  check('M9', 'uplink blackout → Connected again after the link returns (BUG-601)', true)
  await page.screenshot({ path: path.join(OUT_DIR, 'mobile-blackout-recovery.png') })

  await page.screenshot({ path: path.join(OUT_DIR, 'mobile-home.png') })
} catch (err) {
  console.error('SMOKE ERROR:', err)
  results.push({ id: 'X', name: 'unexpected error', ok: false, note: String(err) })
} finally {
  await browser?.close()
}

console.log(`\n${passed}/${results.length} checks passed`)
if (consoleErrors.length > 0) {
  console.log('console errors:')
  for (const line of consoleErrors) console.log('  ', line)
}
process.exit(passed === results.length && consoleErrors.length === 0 ? 0 : 1)
