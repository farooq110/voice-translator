# User Manual — AI Voice Translator (speech-to-text)

*This manual belongs to the `phase-2` branch and covers ONLY what this
branch's code does: live microphone transcription in the browser with a
language configuration, hands-free or push-to-talk capture, and a
transcript you can prune. There is no translation, no spoken output and no
two-person mode in this branch.*

---

## What this app does

You pick two languages, tap the microphone, and speak. Everything you say
is transcribed on this server (local Whisper models — no cloud) and shown
line by line. Silence and noise are filtered before recognition, so the
page stays quiet when you are.

## Start here

1. Start the server: `bash start.sh` → open `http://localhost:3000`.
2. Wait for the badge in the top corner to say **Connected**.
3. Choose **Native language** (what you speak — it is pinned as the
   recognition language) and **Preferred translation language** (reserved
   here for the configuration shape; this branch does not translate).
4. Tap the **microphone button** and talk. Tap again to stop.

## The page, piece by piece

### Header + connection badge
The badge is your connection truth: *Connected* (green), *Connecting…*,
*Reconnecting…* or *Disconnected*. The mic unlocks only on green.

### Language panel
Two selectors. The native language pins what the recognizer listens for —
pick the one you actually speak. Regional variants (`ur-PK`) are reduced
to the base language automatically.

### Microphone panel
- **Mic button** — round button, tap to start, tap to stop. Red + pulsing
  while live.
- **Status line** — always tells the truth: *Connected — tap the
  microphone…*, *Hands-free listening… 8 s*, *Push-to-talk recording…*,
  *Transcribing…*, *Finishing… capturing the last words*.
- **Level meter** — a thin bar dancing with your voice. Flat meter = wrong
  mic device; check the browser's site settings.
- **Hands-free toggle** —
  - **ON**: keep talking; every natural pause (≥ 0.8 s) closes a sentence
    and transcribes it while you continue. Long nonstop speech is cut
    every ~4.5 s at the first micro-pause so text keeps flowing.
  - **OFF**: push-to-talk. Everything between tap-start and tap-stop
    becomes ONE transcription when you stop (a 20 s safety cap applies).
- **The ✕ trick after stopping** — tapping stop too fast is fine: the app
  captures ~0.7 s of trailing silence ("Finishing…") so your last word is
  never cut.

### Transcript panel
- **Newest sentence on top.**
- Each line: the text, the detected language with a confidence chip when
  uncertain, and how long processing took.
- **✕ deletes a line** from your screen.
- The *Transcribing…* indicator appears only for segments that carried
  real speech and always clears when the result (or an error) arrives.

### Error banner
Red banner with a short code and a human sentence. Dismiss with the ✕.
`no_speech_detected` after a silence-only moment is normal and expected.

## Good to know

- Transcription runs on this server; audio is not stored — buffers are
  dropped the moment a segment is decoded or the connection ends.
- A reload clears the transcript (it lives in the page).
- If the connection drops mid-recording, recording stops with a banner;
  the app reconnects automatically — speak again once it's green.
- Under heavy load a segment may be dropped BY DESIGN
  (`server_busy` with a queue hint) — keep talking; the next segment is
  processed normally. Do not re-flush.

## Troubleshooting

| Symptom | Meaning → action |
| --- | --- |
| Badge stuck on *Connecting…* | Server down → check `bash start.sh` output; reload. |
| Level meter flat | Wrong input device → pick the mic in browser site settings. |
| *no speech detected* often | Microphone too quiet / far → speak closer; check input level. |
| Text in the wrong language/script | Native language selector set wrong → pick what you actually speak. |
| Weird long words from silence | Should not happen (hallucination guard) → if it does, report it. |
| *server_busy* | Load cap reached → wait a beat; keep talking normally. |
