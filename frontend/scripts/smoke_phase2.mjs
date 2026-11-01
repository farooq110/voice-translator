/**
 * smoke_phase2.mjs — headless-Chromium browser smoke for the built app.
 *
 * Re-provisioned after the original was lost with the sandbox's download/
 * wipe (OBS-004). Runs the REAL capture → WebSocket → server VAD → UI
 * pipeline end to end using Chrome's fake microphone device.
 *
 * Usage (from the repo root or frontend/):
 *   npm run build                     # serve the production build via start.sh
 *   bash start.sh &                   # http://localhost:3000
 *   npx playwright install chromium   # once (browsers are cached)
 *   node frontend/scripts/smoke_phase2.mjs [--url URL] [--out DIR]
 *
 * Exit code 0 = all checks passed. Screenshots land in --out (default:
 * docs/screenshots/phase2, tracked in git — OBS-004).
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
const OUT_DIR = argOf('--out', 'docs/screenshots/phase2')

const results = []
let consoleErrors = []
let browser
let passed = 0

function check(id, name, ok, note = '') {
  results.push({ id, name, ok, note })
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${id}  ${name}${note ? ` — ${note}` : ''}`)
  if (ok) passed += 1
}

try {
  browser = await chromium.launch({
    args: [
      '--use-fake-device-for-media-stream', // Chrome's fake mic (tone signal)
      '--use-fake-ui-for-media-stream', // auto-grant the permission prompt
    ],
  })
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } })
  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text())
  })
  page.on('pageerror', (err) => consoleErrors.push(String(err)))

  // C1 — app renders
  await page.goto(BASE_URL, { waitUntil: 'networkidle' })
  const mic = page.getByRole('button', { name: /start speaking/i })
  check('C1', 'app renders (mic button present)', await mic.isVisible())

  // C2 — WebSocket connected
  await page.getByText('Connected', { exact: true }).waitFor({ timeout: 10_000 })
  check('C2', 'WebSocket connects (badge: Connected)', true)

  // C4 — hands-free toggle visible, default ON (the flag is lazily persisted:
  // written on first toggle, so a virgin profile reads null — C5 covers persistence)
  const toggle = page.locator('.handsfree input[type=checkbox]')
  const toggleVisible = await toggle.isVisible()
  const defaultChecked = toggleVisible ? await toggle.isChecked() : false
  const storedFlag = await page.evaluate(() => window.localStorage.getItem('vt.hands_free'))
  check(
    'C4',
    'hands-free toggle visible, default ON',
    toggleVisible && defaultChecked,
    `localStorage(before first toggle)=${storedFlag}`,
  )

  // C5 — toggle OFF persists across reload
  await toggle.uncheck()
  await page.reload({ waitUntil: 'networkidle' })
  const persistedOff = await page.evaluate(() => window.localStorage.getItem('vt.hands_free'))
  check('C5', 'hands-free OFF persists across reload', persistedOff === 'false', `localStorage=${persistedOff}`)

  // C6 — push-to-talk status wording while recording (BUG-002 label)
  await page.getByRole('button', { name: /start speaking/i }).click()
  await page.getByText(/Push-to-talk recording/).waitFor({ timeout: 5_000 })
  check('C6', 'PTT status wording while recording', true)

  // C7 — 4 s of fake-audio streaming: no crash; the only acceptable banner
  // afterwards is the honest no_speech_detected path (fake tone ≠ speech).
  await page.waitForTimeout(4_000)
  await page.getByRole('button', { name: /stop speaking/i }).click()
  await page
    .locator('.banner')
    .waitFor({ timeout: 20_000 })
    .catch(() => {})
  const bannerCode = (await page.locator('.banner__code').textContent().catch(() => '')) ?? ''
  check(
    'C7',
    'stream + stop: only the expected no-speech banner',
    bannerCode === '' || bannerCode.replace(/ /g, '_') === 'no_speech_detected',
    `banner=${bannerCode || 'none'}`,
  )
  const stillOpen = await page.getByRole('button', { name: /start speaking/i }).isVisible()
  check('C8', 'app still interactive after stop', stillOpen)

  // C9 — toggle back ON, reload, record again: hands-free wording
  await page.locator('.handsfree input[type=checkbox]').check()
  await page.reload({ waitUntil: 'networkidle' })
  await page.getByText('Connected', { exact: true }).waitFor({ timeout: 10_000 })
  await page.getByRole('button', { name: /start speaking/i }).click()
  await page.getByText(/Hands-free listening/).waitFor({ timeout: 5_000 })
  check('C9', 'hands-free status wording while recording', true)
  await page.getByRole('button', { name: /stop speaking/i }).click()
  await page.waitForTimeout(500)

  // C10 — screenshots (desktop 1280 + mobile 390×844), tracked in git
  // wait for the tail-capture window to close so the shot shows the idle state
  await page.getByRole('button', { name: /start speaking/i }).waitFor({ timeout: 8_000 })
  await page.waitForTimeout(400)
  await mkdir(OUT_DIR, { recursive: true })
  const desktopPath = path.join(OUT_DIR, 'desktop.png')
  await page.screenshot({ path: desktopPath, fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  const mobilePath = path.join(OUT_DIR, 'mobile.png')
  await page.screenshot({ path: mobilePath, fullPage: true })
  check('C10', 'desktop + mobile screenshots captured', true, `${desktopPath}, ${mobilePath}`)

  // C3 — zero console/page errors across the whole run (checked last)
  check('C3', 'zero console/page errors', consoleErrors.length === 0, consoleErrors.slice(0, 3).join(' | '))
} catch (err) {
  check('EXC', `unexpected failure: ${String(err).slice(0, 300)}`, false)
} finally {
  await browser?.close()
}

console.log(`\n${passed}/${results.length} checks passed`)
process.exit(passed === results.length ? 0 : 1)
