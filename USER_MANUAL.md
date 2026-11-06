# User Manual — AI Voice Translator (transcription, translation, voice)

*This manual belongs to the `phase-4` branch and covers ONLY what this
branch's code does: live transcription, the translation decision engine,
and SPOKEN translations — the server synthesizes the translation with
offline voices and your browser plays it. There is no two-person mode in
this branch: one microphone, one transcript, one voice.*

---

## What this app does

You speak; the page transcribes and translates like the previous phase —
and now it also **speaks the translation aloud** in the target language
with an offline voice (Urdu, English, Spanish). The original text always
stays on the line; the voice is a layer on top, never a replacement. When
you speak a third language, there is no voice at all — pass-through means
pass-through.

## Start here

1. `bash start.sh` → open `http://localhost:3000`.
2. Wait for **Connected**.
3. Pick **Native language** and **Preferred translation language**.
4. Tap the mic and talk. After a translated line appears, its voice plays
   automatically.

## The page, piece by piece

### Header + connection badge
Green badge = mic unlocked.

### Language panel
Native + preferred selectors with hints. The voice speaks the preferred
language's script/sound; pick the pair you actually want.

### Microphone panel
- **Mic button** — tap to start/stop.
- **Status line** — listening / transcribing / finishing states as before.
- **Level meter** — live input level.
- **Hands-free toggle** — ON: sentence-per-pause; OFF: push-to-talk.
- **Spoken translations toggle** — voice output ON/OFF (remembered
  between visits). Turning it OFF immediately silences and clears
  scheduled audio.
- **stop voice button** — appears while a clip is queued or playing; one
  tap cancels everything scheduled.

### Transcript panel
Newest on top. Each line: original text + language chip, the decision
badge (`UR → EN`, `pass-through · …`), the translated text, and now:

- a small **"spoken" indicator on the line currently being voiced**
  (queue state: one speaking, others waiting);
- **voice warning chips** when synthesis fails, times out or is
  unavailable — the translated text remains readable;
- **✕ deletes the line and stops ITS audio** (a deleted sentence never
  keeps talking).

### Playback behavior (what makes it pleasant)
- Clips play **strictly one after another** — two utterances can never
  talk over each other, by construction.
- The queue is bounded; under a burst the OLDEST waiting clip is dropped
  so you always hear the newest translation.
- The voice unlocks with your first mic tap (browser autoplay rules) — no
  extra gesture needed.
- Connection lost → all queued audio stops immediately.

## Voice notes

| Situation | What you get |
| --- | --- |
| First sentence into a language | ~1.5 s extra (the voice loads once, then stays hot). |
| `pass-through` line | No voice — your original audio was the point. |
| Voice chip warning (`tts_*`) | Synthesis problem — text still there; usually transient. |
| Very long sentence | Synthesized fully (bounded at 800 chars server-side). |

## Good to know

- All voices are offline on this server; no cloud speech services.
- A reload clears transcript and audio.
- Connection drops stop recording AND playback; the app reconnects by
  itself.

## Troubleshooting

| Symptom | Meaning → action |
| --- | --- |
| Translations appear, no voice | Spoken-translations toggle OFF → switch it on. |
| Voice chip `tts_unsupported_language` | No voice model for that language in this deployment → provision voices (`backend/scripts/download_tts_voices.py`). |
| Voice lags behind | Queue playing catch-up → tap **stop voice** to flush and continue. |
| No audio at all | Device muted / wrong output → check system output; the level meter is input-only. |
| *no speech detected* | Noise only → speak closer. |
| Badge stuck on *Connecting…* | Server down → check `start.sh`, reload. |
