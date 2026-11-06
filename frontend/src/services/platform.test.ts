/**
 * Platform tag tests (Phase 6, KI-004) — coarse UA mapping only; no raw
 * user-agent strings ever leave the browser.
 */

import { describe, expect, it } from 'vitest'
import { detectPlatform, platformFromUserAgent } from './platform'

const UAS: Array<[string, ReturnType<typeof platformFromUserAgent>]> = [
  [
    'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1',
    'ios-safari',
  ],
  [
    'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/126.0.0.0 Mobile/15E148 Safari/604.1',
    'ios-other',
  ],
  [
    'Mozilla/5.0 (iPad; CPU OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1',
    'ios-safari',
  ],
  [
    'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36',
    'android-chrome',
  ],
  [
    'Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/25.0 Chrome/121.0.0.0 Mobile Safari/537.36',
    'android-other',
  ],
  [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    'desktop-chrome',
  ],
  [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0',
    'desktop-edge',
  ],
  [
    'Mozilla/5.0 (X11; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0',
    'desktop-firefox',
  ],
  [
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15',
    'desktop-safari',
  ],
  [
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    'desktop-chrome',
  ],
  ['', 'desktop-other'],
  ['some exotic crawler', 'desktop-other'],
]

describe('platformFromUserAgent', () => {
  it.each(UAS)('maps %j -> %j', (ua, expected) => {
    expect(platformFromUserAgent(ua)).toBe(expected)
  })
})

describe('detectPlatform', () => {
  it('uses the explicit UA when given', () => {
    expect(detectPlatform('Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Safari/604.1')).toBe(
      'ios-safari',
    )
  })

  it('stays coarse and protocol-safe (lowercase tag ≤ 24 chars)', () => {
    const tag = detectPlatform('Mozilla/5.0 (Linux; Android 14) Chrome/126 Mobile')
    expect(tag).toMatch(/^[a-z0-9-]{2,24}$/)
  })
})
