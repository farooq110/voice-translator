/**
 * Coarse client platform tag (Phase 6, KI-004).
 *
 * Sent once in `hello.config.client` so the server's /health can show which
 * device families actually use the app. Deliberately coarse — a short tag
 * like "ios-safari", never a raw user-agent string. UA-only detection cannot
 * see iPadOS 13+ touch devices that masquerade as desktop Safari; that
 * residual gap is accepted (the tag is observational, not functional).
 */

export type PlatformTag =
  | 'ios-safari'
  | 'ios-other'
  | 'android-chrome'
  | 'android-other'
  | 'desktop-chrome'
  | 'desktop-edge'
  | 'desktop-firefox'
  | 'desktop-safari'
  | 'desktop-other'

/** Map a user-agent string to a coarse platform tag (pure, testable). */
export function platformFromUserAgent(ua: string): PlatformTag {
  const s = ua.toLowerCase()
  // Third-party iOS browsers are all WebKit shells with their own UA marks.
  if (/iphone|ipad|ipod/.test(s)) {
    return /crios|fxios|edgios/.test(s) ? 'ios-other' : 'ios-safari'
  }
  if (/android/.test(s)) {
    return /chrome|crios/.test(s) && !/edg\//.test(s) && !/samsungbrowser/.test(s)
      ? 'android-chrome'
      : 'android-other'
  }
  if (/edg\//.test(s)) return 'desktop-edge'
  if (/firefox/.test(s)) return 'desktop-firefox'
  if (/macintosh|mac os x/.test(s)) {
    return /chrome/.test(s) ? 'desktop-chrome' : 'desktop-safari'
  }
  if (/windows|cros|linux/.test(s)) {
    return /chrome/.test(s) ? 'desktop-chrome' : 'desktop-other'
  }
  return 'desktop-other'
}

/** Detect this browser's platform tag (safe outside a browser too). */
export function detectPlatform(ua?: string): PlatformTag {
  if (ua === undefined) {
    if (typeof navigator === 'undefined') return 'desktop-other'
    ua = navigator.userAgent
  }
  return platformFromUserAgent(ua)
}
