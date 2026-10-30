/**
 * PCM capture AudioWorklet.
 *
 * Receives the raw microphone graph and posts fixed-size PCM16 chunks to the
 * main thread:
 *   { type: 'chunk', buffer: ArrayBuffer }  — Int16Array PCM, target rate
 *   { type: 'level', rms: number }          — input level (0..1), per chunk
 *
 * When the AudioContext was created at the target rate (16 kHz) this is a
 * straight passthrough; otherwise a streaming linear resampler is applied so
 * the backend always receives 16 kHz audio regardless of device quirks.
 *
 * Plain JS on purpose: AudioWorklet modules load via addModule(url) and are
 * not part of the Vite/TS bundle (served from /public).
 */
/* eslint-disable */
'use strict'

const TARGET_RATE = 16000
const CHUNK_MS = 100

function floatToPcm16(s) {
  const v = s < -1 ? -1 : s > 1 ? 1 : s
  return Math.round(v < 0 ? v * 32768 : v * 32767)
}

class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super()
    const opts = (options && options.processorOptions) || {}
    this.targetRate = typeof opts.targetRate === 'number' ? opts.targetRate : TARGET_RATE
    this.chunkMs = typeof opts.chunkMs === 'number' ? opts.chunkMs : CHUNK_MS
    this.ratio = sampleRate / this.targetRate
    this.chunkSamples = Math.max(1, Math.round((this.targetRate * this.chunkMs) / 1000))
    this.chunk = new Int16Array(this.chunkSamples)
    this.chunkFilled = 0

    // Streaming resampler state (used only when ratio !== 1).
    this.inBuf = new Float32Array(4096)
    this.inFilled = 0
    this.pos = 0

    this.levelAcc = 0
    this.levelN = 0
  }

  process(inputs) {
    const input = inputs[0]
    if (!input || input.length === 0 || !input[0]) return true
    const ch = input[0]

    let sum = 0
    for (let i = 0; i < ch.length; i++) sum += ch[i] * ch[i]
    this.levelAcc += sum
    this.levelN += ch.length

    if (this.ratio === 1) {
      for (let i = 0; i < ch.length; i++) {
        this.chunk[this.chunkFilled++] = floatToPcm16(ch[i])
        if (this.chunkFilled === this.chunkSamples) this.emitChunk()
      }
    } else {
      this.appendAndResample(ch)
    }
    return true
  }

  appendAndResample(block) {
    // Grow the input buffer if needed (rare; we compact after each block).
    if (this.inFilled + block.length + 2 > this.inBuf.length) {
      const grown = new Float32Array(Math.max(this.inBuf.length * 2, this.inFilled + block.length + 2))
      grown.set(this.inBuf.subarray(0, this.inFilled))
      this.inBuf = grown
    }
    for (let i = 0; i < block.length; i++) this.inBuf[this.inFilled++] = block[i]

    // Emit interpolated samples while a two-sample neighborhood is available.
    for (;;) {
      const i0 = Math.floor(this.pos)
      if (i0 + 1 >= this.inFilled) break
      const frac = this.pos - i0
      const s = this.inBuf[i0] * (1 - frac) + this.inBuf[i0 + 1] * frac
      this.chunk[this.chunkFilled++] = floatToPcm16(s)
      if (this.chunkFilled === this.chunkSamples) this.emitChunk()
      this.pos += this.ratio
    }

    // Compact: keep from the next unread position onward.
    const keepFrom = Math.floor(this.pos)
    if (keepFrom > 0) {
      this.inBuf.copyWithin(0, keepFrom, this.inFilled)
      this.inFilled -= keepFrom
      this.pos -= keepFrom
    }
  }

  emitChunk() {
    const buffer = this.chunk.buffer
    this.port.postMessage({ type: 'chunk', buffer }, [buffer])
    this.chunk = new Int16Array(this.chunkSamples)
    this.chunkFilled = 0

    const rms = this.levelN > 0 ? Math.sqrt(this.levelAcc / this.levelN) : 0
    this.port.postMessage({ type: 'level', rms })
    this.levelAcc = 0
    this.levelN = 0
  }
}

registerProcessor('pcm-capture', PcmCaptureProcessor)
