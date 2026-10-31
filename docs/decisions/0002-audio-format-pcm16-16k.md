# ADR-0002 — Audio format: PCM16 mono @ 16 kHz over WebSocket binary frames

- **Status:** Accepted (2026-10-31)
- **Context:** Browser capture is float32 at the device rate (44.1/48 kHz);
  Whisper's native rate is 16 kHz; mobile uplink bandwidth matters.

- **Decision:** protocol v1 carries PCM16 LE mono 16 kHz (32 kB/s) in raw
  binary WebSocket frames. The browser downsampled at capture time via
  `AudioContext({sampleRate: 16000})`; a streaming linear resampler inside the
  AudioWorklet is the fallback for engines that ignore the rate constraint.
  The backend additionally carries a `np.interp` resampler as a last resort
  (unused when the protocol is honored).

- **Reasons:**
  - no server-side transcoding cost; whisper consumes the samples directly;
  - predictable framing (100 ms = 3 200 bytes) simplifies validation,
    buffering bounds and latency accounting;
  - raw PCM avoids codec latency/complexity for the MVP; Opus can be
    introduced with a protocol version bump if bandwidth ever matters
    (32 kB/s ≈ 256 kbit/s is acceptable for Wi-Fi/4G).

- **Consequences:** uncompressed uplink (documented); format is enforced by
  the handshake (`unsupported_*` fatal errors) so drift cannot happen silently.
