/* One-off live probe for BUG-603 (QA round v0.6.2).
 * Reproduces the Journal's exact scenario: microphone permission DENIED via
 * CDP Browser.setPermission while a fake device is present → tapping the mic
 * must show the honest access-guidance banner ("refused or is unavailable"),
 * never the raw "START FAILED — Not supported".
 * Run with the server live on :3000. Not part of the standing suite — the
 * standing regression guard is src/services/audioCapture.test.ts.
 */
import { chromium } from 'playwright'

const BASE = process.env.BASE_URL ?? 'http://localhost:3000'
const results = []
const check = (name, ok, detail = '') => {
  results.push({ name, ok, detail })
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? ` — ${detail}` : ''}`)
}

const browser = await chromium.launch({
  args: [
    '--use-fake-device-for-media-stream',
    '--autoplay-policy=no-user-gesture-required',
  ],
})
try {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } })
  const page = await context.newPage()
  await page.goto(BASE, { waitUntil: 'domcontentloaded' })
  await page.locator('.mic-button').waitFor({ timeout: 12_000 })
  await page.getByText('Connected', { exact: true }).waitFor({ timeout: 12_000 })
  check('P1 connected before probe', true)

  const cdp = await context.newCDPSession(page)
  await cdp.send('Browser.setPermission', {
    permission: { name: 'microphone' },
    setting: 'denied',
  })
  check('P2 microphone permission denied via CDP', true)

  await page.locator('.mic-button').click()
  await page.locator('.banner').waitFor({ timeout: 15_000 })
  const code = (await page.locator('.banner__code').innerText()).trim()
  const message = (await page.locator('.banner').innerText()).trim()

  const notSupported = /not supported/i.test(message)
  const guidance =
    /refused or is unavailable/i.test(message) ||
    /permission was denied/i.test(message) ||
    /no microphone was found/i.test(message)
  check('P3 no misleading "Not supported" banner (BUG-603)', !notSupported, `code=${code}`)
  check(
    'P4 honest access-guidance banner shown',
    guidance,
    message.replace(/\s+/g, ' ').slice(0, 110),
  )
} finally {
  await browser.close()
}

const failed = results.filter((r) => !r.ok)
console.log(`\n${results.length - failed.length}/${results.length} probe checks passed`)
process.exit(failed.length > 0 ? 1 : 0)
