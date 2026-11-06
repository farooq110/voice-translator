# How to use the AI Voice Translator

*This is the user manual for `main` — the current app (v0.6.0). It covers
only what this branch implements: live transcription, the translation
decision engine, spoken translations, two-person rooms, and the mobile
lifecycle behaviors.*

---

## Start here (60 seconds)

1. Start the server: `bash start.sh` → open `http://localhost:3000`.
2. Pick **Native language** (what you speak) and **Preferred translation
   language** (what the other person should hear).
3. Tap the microphone and talk. Sentences appear in the transcript as you
   pause; the other side hears them spoken in their language.
4. To add the second person: press **copy invite link** and send it to
   them. They open it, allow the microphone, and talk. Done.

That's the whole loop. The rest of this file explains each part of the
page and what to do when something looks wrong.

## The page, top to bottom

### Header
The app title and a **connection badge**. Recording only unlocks when the
badge says *Connected*. If it says *Reconnecting…*, the app is already
retrying — wait for green.

### Room panel
Your **invite code** and a **copy invite link** button. The link is the
only thing the second person needs — no install, no settings on their
side. Once they join, the panel shows *Both participants connected*; when
they leave, *Waiting for the other participant* returns.

### Your languages (host only)
Two selectors with hints. The **guest never sees this panel** — the room
carries your pair to them. Changing languages applies from the next
connection onward.

### Microphone panel
- **Mic button** — tap to start/stop. Red + pulsing = live.
- **Status line** — always tells the truth: listening, push-to-talk
  recording, transcribing, finishing, or why it stopped.
- **Level meter** — moves with your voice; if it never moves, check the
  browser's mic device.
- **Hands-free** — ON cuts sentences at natural pauses while you keep
  talking; OFF is classic push-to-talk (one press = one transcription).
- **Spoken translations** — voice output on/off; **stop voice** appears
  while audio is playing.

### Transcript
Newest line on top. Every line shows: the original text, detected
language + confidence, the **decision badge** (`UR → EN`, `pass-through ·
third language`, `same language`, …), the translated text when produced,
and per-line warning chips if translation/voice failed (the original text
is kept). Lines carry a **Host/Guest chip** in rooms. **✕** deletes a line
from your own screen and stops its audio.

## The other person's page (guest view)

They open your invite link, allow the microphone, and see: a short
confirmation, the host's language pair, a mic button and the shared
transcript. That's all — deliberately. Their translations arrive as voice
on their device; the host's arrive on the host's. Nobody hears their own
words translated back at them. A third language (outside the configured
pair) crosses as the speaker's real voice — relayed audio, never
machine-spoken.

## Speaking rules the app follows

| What is spoken | What the other side gets |
| --- | --- |
| Host's native language | Voice in the preferred language |
| Host's preferred language | Voice in the native language |
| Any third language | The original audio, relayed untouched |
| Identical native+preferred settings | Pass-through (no translation needed) |

## On a phone

- **Install:** use the browser's *Add to Home Screen*; the app opens
  full-screen from its icon.
- **Backgrounding/locking/interrupted by a call:** the recording stops
  right away with a clear banner (the OS suspends the mic — the app never
  fakes it). Tap the mic when you're back.
- **Screen wake:** requested only while the mic is open.
- The mic panel stays visible while long transcripts scroll; swiping down
  cannot reload the app mid-recording.

## Troubleshooting

| Symptom | Meaning → action |
| --- | --- |
| Badge stuck on *Connecting…* | Server down/restarting → check `bash start.sh` output, reload the page. |
| *no speech detected* | Noise or silence only → speak a little louder/closer. |
| Level meter flat | Wrong input device → pick the mic in the browser's site settings. |
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
