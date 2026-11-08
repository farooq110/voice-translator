/**
 * qa63 probe — live verification of the v0.6.3 fixes against the local
 * deployment (http://localhost:3000):
 *   BUG-604 — solo TTS: the per-line "speaking" chip renders for the
 *             playing line (wire: the solo `audio` event now carries
 *             speaker:"host" matching the transcription event).
 *   BUG-605 — .room-panel__copy is ≥ 44 px tall at 390 px width.
 * Usage: node probe_qa63.mjs
 */
import { chromium } from 'playwright'

const BASE_URL = process.env.BASE_URL ?? 'http://localhost:3000'
const results = []
const check = (id, ok, detail = '') => {
  results.push({ id, ok, detail })
  console.log(`${ok ? 'PASS' : 'FAIL'} ${id}${detail ? ' — ' + detail : ''}`)
}

// ---------- BUG-605: touch target at 390 px ----------
{
  const browser = await chromium.launch()
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } })
  await page.goto(BASE_URL, { waitUntil: 'networkidle' })
  await page.getByText('Connected', { exact: true }).waitFor({ timeout: 15_000 })
  const copy = page.locator('.room-panel__copy')
  const box = await copy.boundingBox()
  const overflowPx = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  )
  check(
    'BUG-605 copy-button height @390px',
    box !== null && box.height >= 44,
    box ? `h=${Math.round(box.height)}px (was 31px)` : 'button not found',
  )
  check('responsive overflow @390px still 0', overflowPx <= 1, `overflowPx=${overflowPx}`)
  await browser.close()
}

// ---------- BUG-604: solo speaking chip + wire speaker tag ----------
{
  const browser = await chromium.launch({
    args: [
      '--use-fake-ui-for-media-stream',
      '--use-fake-device-for-media-stream',
      `--use-file-for-fake-audio-capture=/tmp/qa63/speech_en.wav`,
      '--autoplay-policy=no-user-gesture-required',
    ],
  })
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } })
  const wire = { audio: [], transcription: [] }
  page.on('websocket', (ws) => {
    ws.on('framereceived', (frame) => {
      const payload = frame.payload
      if (typeof payload !== 'string' || !payload.startsWith('{')) return
      try {
        const evt = JSON.parse(payload)
        if (evt.type === 'audio') wire.audio.push(evt)
        if (evt.type === 'transcription') wire.transcription.push(evt)
      } catch {
        /* binary or partial */
      }
    })
  })
  await page.goto(BASE_URL, { waitUntil: 'networkidle' })
  await page.getByText('Connected', { exact: true }).waitFor({ timeout: 15_000 })
  await page.locator('.mic-button').click()
  await page
    .getByText(/Hands-free listening|Push-to-talk recording/)
    .waitFor({ timeout: 12_000 })
  // speak (the WAV plays as the mic); wait for the utterance to be
  // transcribed, translated and synthesized (Piper ≈5 s speech + pipeline)
  const deadline = Date.now() + 60_000
  let chipSeen = false
  while (Date.now() < deadline && !chipSeen) {
    chipSeen = (await page.locator('.chip--speaking').count()) > 0
    if (!chipSeen) await page.waitForTimeout(300)
  }
  check(
    'BUG-604 per-line speaking chip renders (solo TTS)',
    chipSeen,
    chipSeen ? 'chip--speaking observed during playback' : 'no chip within 60 s',
  )
  const tr = wire.transcription.at(-1)
  const au = wire.audio.at(-1)
  check(
    'BUG-604 wire: solo audio event carries the same speaker tag as its transcription',
    !!au && !!tr && au.speaker === tr.speaker && tr.speaker === 'host',
    `transcription.speaker=${tr?.speaker} audio.speaker=${au?.speaker}`,
  )
  const stopVoice = await page.getByText('stop voice', { exact: false }).count()
  check('playback control ("stop voice") visible', stopVoice > 0, `count=${stopVoice}`)
  await page.screenshot({ path: '/tmp/qa63/bug604-chip.png' })
  await browser.close()
}

const failed = results.filter((r) => !r.ok)
console.log(`\n== qa63 probe: ${results.length - failed.length}/${results.length} passed ==`)
process.exit(failed.length ? 1 : 0)
