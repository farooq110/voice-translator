# User Manual — AI Voice Translator (transcription + translation)

*This manual belongs to the `phase-3` branch and covers ONLY what this
branch's code does: live transcription PLUS the translation decision
engine — your language is translated to the configured target, any third
language passes through untouched. There is no spoken output and no
two-person mode in this branch.*

---

## What this app does

You pick a native and a preferred language, tap the microphone, and speak.
Sentences are transcribed live and — when translation makes sense —
translated, with the decision shown right on each line:

- You speak your **native** language → translated into the preferred one.
- You speak the **preferred** language → translated into the native one.
- You speak a **third language** → NOT translated: the original text is
  kept and marked `pass-through · third language`.
- Same language on both sides, or a low-confidence detection → honest
  pass-through with the reason on the chip.

Everything runs on this server (local Whisper + offline OPUS-MT models —
no cloud, no API keys).

## Start here

1. `bash start.sh` → open `http://localhost:3000`.
2. Wait for **Connected**.
3. Pick **Native language** and **Preferred translation language**.
4. Tap the mic and talk — translated text appears under each original
   line as it is produced.

## The page, piece by piece

### Header + connection badge
Same as ever: the mic unlocks only when the badge is green.

### Language panel
Native = what you speak (pinned for recognition). Preferred = the target
language. Hints under the selectors spell out what will happen to your
speech.

### Microphone panel
- **Mic button** — tap to start/stop; red + pulsing while live.
- **Status line** — *Connected…*, *Hands-free listening… N s*,
  *Push-to-talk recording…*, *Transcribing…*, *Finishing…*.
- **Level meter** — voice dance; flat = wrong device.
- **Hands-free toggle** — ON: sentence-per-pause while you keep talking.
  OFF: push-to-talk, one press = one transcription.

### Transcript panel — the heart of this branch
Newest line on top; each line carries:

- the **original text** with detected language + confidence chip;
- a **decision badge** — `UR → EN` (will translate / translated),
  `pass-through · third language`, `pass-through · identical config`,
  `pass-through · low confidence`, `pair unavailable`, etc. The badge says
  WHY, so nothing is a mystery;
- the **translated text below the original** when a translation was
  produced (RTL scripts render right-to-left correctly);
- a small **warning chip** if translation failed or timed out — the
  original text is NEVER lost to a translation error;
- **✕ deletes the line** from your screen.

### Error banner
Connection-level problems only. Utterance-level translation issues stay on
the line as chips, by design.

## Reading the decision badges

| Badge | Meaning |
| --- | --- |
| `UR → EN` (direction varies) | Your utterance is being translated into the configured target. |
| `pass-through · third language` | You spoke a language outside the configured pair — original kept, nothing invented. |
| `pass-through · identical config` | Native = preferred; nothing to translate. |
| `pass-through · low confidence` | Detection was unsure — showing the original rather than guessing. |
| `pair unavailable` | No offline model for this pair in this deployment. |

## Good to know

- Translations for everyday sentences are solid; nuanced or long Urdu →
  English is honest-tier, not professional-tier (small offline models).
- A reload clears the transcript.
- If the connection drops mid-recording, recording stops with a banner and
  reconnects automatically.
- `server_busy` drops that segment by design under load — keep talking.

## Troubleshooting

| Symptom | Meaning → action |
| --- | --- |
| Every line says `pair unavailable` | MT models not provisioned → `backend/scripts/download_translation_models.py`, restart. |
| Badge shows translation but no text after it | Translation still running, or failed → wait a beat; a warning chip appears on failure. |
| Wrong source language detected | Detection is automatic for non-pinned speech; check the confidence chip and speak a full sentence. |
| *no speech detected* | Noise/silence only → speak closer to the mic. |
| Badge stuck on *Connecting…* | Server down → check `start.sh`, reload. |
