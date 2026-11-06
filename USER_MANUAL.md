# User Manual — AI Voice Translator (two-person sessions)

*This manual belongs to the `phase-5` branch and covers ONLY what this
branch's code does: everything through the two-participant browser MVP —
live transcription, translation with spoken output, and ROOMS: an invite
link, a guest who configures nothing, speaker-labeled transcripts, voices
that reach only the listener, and original-audio relay for third
languages. (The mobile lifecycle behaviors of a later phase are NOT in
this branch.)*

---

## What this app does

Two people, one conversation, each hearing the other in their own
language. The translator user (host) configures a language pair once and
shares an invite link; the guest opens it and just speaks. Speech is
recognized, decided upon and voiced entirely on this server — no accounts,
no installs, no cloud.

## Start here (the host)

1. `bash start.sh` → open `http://localhost:3000`.
2. Wait for **Connected**. The room panel shows your **invite code** and a
   **copy invite link** button.
3. Pick your **Native** and **Preferred** languages (only you ever do
   this).
4. Send the invite link to the other person.
5. When they open it, the panel flips to **Both participants connected**.
6. Tap the mic and talk — that's the whole ceremony.

## Start here (the guest)

1. Open the link the host sent. Allow the microphone.
2. You see a short confirmation and the host's language pair — there is
   deliberately **no language settings panel** for you.
3. Wait for **Connected**, tap the mic, speak your language.
4. You hear the host's words spoken in YOUR side's language; the host
   hears yours in theirs.

## The host page, piece by piece

### Room panel
Invite code + **copy invite link**. Status: *Waiting for the other
participant* → *Both participants connected* → back to waiting if they
leave. The room lingers ~2 minutes after you both leave, so a dropped
connection can rejoin with the same link; after that the code dies
honestly (`room not found` → ask for a fresh link).

### Language panel (host only)
Native + preferred. The room carries YOUR pair to the guest. Changing
languages applies on the next connection.

### Microphone panel
Mic button, truthful status line, level meter, **Hands-free** toggle
(sentence-per-pause vs push-to-talk), **Spoken translations** toggle,
**stop voice** while audio plays.

### Transcript panel (shared)
Newest on top; every line shows **whose words they are** (Host/Guest
chip), the original text with its language, the decision badge
(`UR → EN`, `pass-through · third language`, …), the translated text, and
warning chips for per-line translation/voice issues. **✕ deletes a line
from YOUR screen only** (and stops its audio here).

## Who hears what (the routing table)

| Someone speaks… | The OTHER side gets | The speaker gets |
| --- | --- | --- |
| Host's native language | Transcript + spoken voice in the preferred language | Transcript + translation only — never their own voice |
| Host's preferred language | Transcript + spoken voice in the native language | Same — text only |
| A third language (e.g. Spanish) | Transcript + the ORIGINAL audio relayed as captured (never machine-spoken) | Transcript + pass-through chip only |

While the other side's voice plays on your device, your uplink is briefly
muted (+0.3 s tail) so the room can never fall into a feedback loop —
speaking over playback may clip your first word; that's the guard working.

## Connection behavior

- Brief drops: both sides reconnect automatically; the host re-attaches to
  its room, the guest retries its seat (up to ~90 s) while the server
  frees a stale seat (~12 s after silence).
- The peer's departure is announced (*peer left*) and their return
  (*peer joined*) in the room panel and by transcript presence.
- If your seat was reclaimed after a long unclean drop
  (`seat_taken`), reload the same link — you'll be rejoined as yourself.

## Good to know

- A room holds EXACTLY two participants; a third connection gets a clear
  *room full*.
- Deleting a line is local to your device; the peer keeps theirs.
- A reload clears the local transcript (the room itself lives on the
  server until the linger expires).
- All voices and models are offline on this server.

## Troubleshooting

| Symptom | Meaning → action |
| --- | --- |
| Guest sees *room not found* | Code expired or typo → host sends a fresh link. |
| *room full* on first join | Two people are already inside → only two seats exist. |
| *Could not rejoin the room…* (after retries) | The linger expired while both were offline → fresh link. |
| Guest hears nothing | Host may be speaking a third language (relay plays the ORIGINAL voice — check volume), or spoken-translations toggle is off. |
| Voice on the wrong device | It isn't: voices ONLY ever go to the listener — the speaker sees text. |
| *no speech detected* | Noise/silence → speak closer to the mic. |
| Badge stuck on *Connecting…* | Server down → check `start.sh`, reload. |
