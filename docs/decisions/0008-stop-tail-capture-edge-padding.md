# ADR-0008 — Stop-tap tail capture + utterance edge padding; per-line transcript delete

- **Status:** Accepted (2026-10-31, user-reported bugfix round; refines ADR-0007)
- **Context:** KI-008 (user report): tapping stop **2–3 s after finishing
  speech** transcribes 70–90 % correctly, but tapping stop **immediately**
  after finishing produces "completely wrong things". Also: the growing
  transcript list needed a way to remove individual sentences.
- **Root cause of the accuracy gap:** the two paths flush differently.
  Waiting 2–3 s lets the client endpointer close the sentence at a natural
  pause (≥ 800 ms of real trailing silence) — clean boundaries. Tapping stop
  immediately hard-cuts the buffer right at the final word: the last phoneme
  is truncated (the worklet's pending partial chunk is dropped) and the audio
  ends with zero acoustic settling room, which Whisper resolves into garbage.
- **Decision:**
  1. **Tail-capture window (frontend):** tapping stop no longer flushes
     instantly. The mic stays open for `TAIL_CAPTURE_MS = 700` ms (UI shows
     "Finishing… capturing the last words", button untouchable), streaming
     real trailing silence, then the capture stops and the final flush goes
     out. Re-taps during the window are ignored; a connection drop finalizes
     immediately (no window — the socket is gone).
  2. **Edge padding (backend):** `FasterWhisperSTT` pads both ends of every
     utterance with `EDGE_PAD_S = 250` ms of silence before decoding (after
     the dead-audio gate; `audio_duration_s` still reports the real length).
     Covers every remaining hard-edge path (stop tap, 20 s cap flush).
  3. **VAD speech pad 150 → 250 ms** (`stt_vad_speech_pad_ms`) so the Silero
     trim keeps more of the speech tail.
  4. **Per-line delete (frontend):** every transcript bubble gets a small ✕
     button (`.transcript-delete`, always visible for touch, hover-red);
     `deleteTranscript(id)` filters the entry out of the hook state. Pure
     client-side removal — the server keeps no audio/history (privacy
     posture), so there is nothing to delete there.
- **Alternatives rejected:** flushing only after N seconds of forced wait
  always (adds latency even when the endpointer already closed the sentence);
  ignoring the final < 1.2 s segment on stop (loses genuine short
  utterances); swipe-to-delete on bubbles (less discoverable, touch-only).
- **Consequences:** the immediate-stop path now matches the "waited" path
  (same clean boundary + padding), so accuracy no longer depends on how fast
  the user taps stop; stop feels ~0.7 s "softer" but the transcription of the
  final segment starts 0.7 s later — net perceived latency is unchanged
  because decode runs server-side while the tail is still being captured.
  Deleting a line is instant and local.
