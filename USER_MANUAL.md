# User Manual — AI Voice Translator

*This manual belongs to the `phase-6` branch and matches exactly what this
branch's code does (v0.6.0): live speech-to-text, the translation decision
engine, spoken translations, two-person rooms, and the mobile behaviors
added in Phase 6. One page, two views: the **host view** (the translator
user) and the **guest view** (the invited second person).*

---

## 1. What this app does

Two people talk. Each speaks their own language; each hears the other
person's words spoken in theirs. The translator user (host) picks a native
and a preferred language once; the guest opens a link and just speaks.
Everything runs on this server — no accounts, no installs, no API keys.

## 2. Before you start

- A browser (Chrome, Edge or Safari; phones supported).
- Microphone permission when the browser asks.
- The server running (`bash start.sh` → `http://localhost:3000`).

## 3. The host view (the translator user)

### 3.1 Header
- **Title + connection badge.** The badge shows *Connected* (green) once
  the app reaches the server. Recording unlocks only when connected.

### 3.2 Room panel (invite)
- Shows a **6-character invite code** (e.g. `TTQJRJ`) and a
  **copy invite link** button.
- **How to invite:** copy the link → send it to the other person (chat,
  email, QR on your screen) → they open it in any browser → the room panel
  flips to *"Both participants connected"*.
- The link is all the guest needs — they never see a settings screen.

### 3.3 Your languages panel
- **Native language** — what YOU will speak.
- **Preferred translation language** — what the other side should hear.
- Hints under each selector explain what will happen. Only you configure
  these; the guest gets the pair from the room.

### 3.4 Capture panel (microphone)
- **The round mic button** — tap to start, tap again to stop. The button
  turns red and pulses while listening.
- **Status line** under the button says exactly what is happening:
  *Connected — tap the microphone…* → *Hands-free listening… 12 s* or
  *Push-to-talk recording…* → *Transcribing…* → *Finishing… capturing the
  last words*.
- **Level meter** — a thin bar that dances with your voice. If it never
  moves, the wrong mic is selected in the browser.
- **Hands-free toggle** — ON: sentences are cut and transcribed at every
  natural pause while you keep talking. OFF: true push-to-talk — everything
  between tap-start and tap-stop becomes ONE transcription.
- **Spoken translations toggle** — ON: translated sentences are spoken
  aloud. OFF: text only. A **stop voice** button appears while a clip is
  playing.

### 3.5 Transcript panel
- Newest sentence on top. Each line shows the detected language, a
  confidence chip when uncertain, the processing time, and — when the
  server translation engine is on — the decision badge (`UR → EN`,
  `pass-through · third language`, …) with the **translated text below**.
- **✕ on a line** deletes it from your screen (and stops its playback).
  Peer lines show a **Host/Guest chip** so you always know whose words
  they are.
- Per-line warning chips (e.g. *translation failed*) keep the original
  text — nothing is lost.

### 3.6 Mobile behaviors (Phase 6)
- **Add to Home Screen** installs the app (manifest + icons); it opens
  full-screen from the launcher.
- Backgrounding the tab, locking the phone, or a phone call **stops the
  recording honestly** with a banner. Tap the mic to continue — never a
  zombie "recording" state.
- While the mic is open the app asks the phone to keep the screen awake.
- The mic panel stays reachable (sticky) while you scroll a long
  transcript; pull-to-refresh cannot reload the app mid-recording.

## 4. The guest view (the invited person)

1. Open the invite link. The browser asks for the microphone — allow it.
2. You see *"You do not need to configure anything"* plus the host's
   language pair. There is **no language panel** for you.
3. Tap the mic and speak your language. The host hears it in theirs.
4. Your transcript shows both speakers with Host/Guest chips, and you hear
   the host's translated voice on your device — while your own voice is
   never translated back at you.
5. If your connection blips, the app reconnects by itself (up to ~90 s)
   and takes your seat back. If the room truly expired, you'll be asked to
   ask the host for a fresh link.

## 5. Languages and what reaches whom

| You (host) speak… | The guest hears… |
| --- | --- |
| Your native language | A spoken voice in your preferred language |
| Your preferred language | A spoken voice in your native language |
| Any third language (e.g. Spanish in a ur↔en room) | **Your original audio**, relayed as captured — never machine-spoken |

The same table applies in reverse for what you hear from the guest.
Speech is decoded, translated and voiced entirely on this server; nothing
goes to the cloud.

- **Install:** use the browser's *Add to Home Screen*; the app opens
  full-screen from its icon.
- **Backgrounding/locking/interrupted by a call:** the recording stops
  right away with a clear banner (the OS suspends the mic — the app never
  fakes it). Tap the mic when you're back.
- **Headset/earbuds disconnect mid-recording (Bluetooth off, cable
  unplugged):** the recording stops immediately with
  *Recording stopped — the microphone was disconnected* — the app never
  keeps a fake "recording" streaming silence. Tap the mic to continue on
  the new device.
- **Screen wake:** requested only while the mic is open.
- The mic panel stays visible while long transcripts scroll; swiping down
  cannot reload the app mid-recording.

| Message | What it means / what to do |
| --- | --- |
| Badge stuck on *Connecting…* | Server down/restarting → check `bash start.sh` output, reload the page. |
| *no speech detected* | Noise or silence only → speak a little louder/closer. |
| Level meter flat | Wrong input device → pick the mic in the browser's site settings. |
| *Microphone access was refused or is unavailable — check site permissions* | The browser refused the mic (denied permission, or the app's context can't access it) → allow the microphone for this site (padlock icon), then tap the mic again. |
| *Recording stopped — the microphone was disconnected* | The active mic disappeared mid-recording (Bluetooth turned off / headset unplugged) → tap the mic to continue with the current audio device. |
| *pass-through · pair unavailable* | The offline MT pair for those languages isn't provisioned → run `backend/scripts/download_translation_models.py`. |
| Voice never plays | Spoken-translations toggle off, or voices not provisioned → check the toggle and `/health` (`tts.languages`). |
| *room full* | Two people are already in the room → rooms hold exactly two. |
| Guest lost the room after a long offline gap | Room expired (~2 min linger) → host sends a fresh link. |
| *server_busy* notice | That segment was dropped by design under heavy load → keep talking; the next segment is processed normally. |

## Notes

- Everything — recognition, translation, voices — runs on this server;
  no audio or text goes to any cloud service.
- Transcripts live only in the page; a reload clears them.
- Deleting a line is local to your device; the other participant keeps
  their copy.

## Changelog of this manual (v0.6.3)

- The per-line **speaking** chip now also lights up in solo mode while a
  translated sentence is being spoken (before, only the room mode showed
  it).
- The **copy invite link** button is now a comfortable thumb target
  (44 px tall) on phones, like every other control.
