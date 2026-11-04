"""End-to-end check against a LIVE server: two REAL connections in one room —
the Phase 5 two-participant browser MVP (ADR-0014).

Usage (from backend/, with the app running on :3000):
    uv run python scripts/e2e_phase5_check.py

Flow (host ur→en, guest configures NOTHING):

    host creates room ─▶ ready(room code) ─▶ guest joins ─▶ peer(joined)

    S1  host speaks ur_greeting  → guest receives en transcript + translation
                                   + REAL spoken audio (Piper, 22 050 Hz);
                                   host receives NO audio (scenario 11)
    S2  guest speaks en_hello    → host receives ur translation + voice;
                                   guest receives NO audio
    S3  host speaks es_hola      → guest receives the ORIGINAL audio relayed
                                   (16 kHz, no TTS anywhere — Rules 3+4);
                                   no translation either side
    S4  guest speaks ur_greeting → scenario 5 (same language both sides):
                                   host receives the en voice

Honesty note: REAL server, REAL rooms, REAL Whisper detection, REAL
CTranslate2 OPUS-MT, REAL Piper synthesis, REAL binary PCM frames over the
REAL protocol. The only synthetic part is the speakers (edge-tts fixtures).
The acoustic feedback loop (scenario 12) cannot exist server-side (output
audio is never re-ingested); its browser-side guard is unit-tested.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import time
import wave
from pathlib import Path

from websockets.asyncio.client import connect

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"


def read_wav(path: Path) -> tuple[bytes, int]:
    with wave.open(str(path), "rb") as wav:
        if wav.getsampwidth() != 2 or wav.getnchannels() != 1:
            raise SystemExit(f"{path.name}: expected 16-bit mono PCM")
        return wav.readframes(wav.getnframes()), wav.getframerate()


def rms(pcm: bytes) -> float:
    samples = [int.from_bytes(pcm[i : i + 2], "little", signed=True) / 32768 for i in range(0, len(pcm) - 1, 2)]
    if not samples:
        return 0.0
    return math.sqrt(sum(s * s for s in samples) / len(samples))


class Participant:
    def __init__(self, ws, name: str) -> None:
        self.ws = ws
        self.name = name

    async def send_json(self, payload: dict) -> None:
        await self.ws.send(json.dumps(payload))

    async def recv_text(self, timeout: float = 120) -> dict:
        raw = await asyncio.wait_for(self.ws.recv(), timeout)
        if isinstance(raw, bytes):
            raise RuntimeError(f"{self.name}: expected a JSON frame, got binary ({len(raw)} B)")
        return json.loads(raw)

    async def recv_binary(self, timeout: float = 60) -> bytes:
        raw = await asyncio.wait_for(self.ws.recv(), timeout)
        if not isinstance(raw, bytes):
            raise RuntimeError(f"{self.name}: expected a binary frame, got: {str(raw)[:80]}")
        return raw

    async def ping_pong(self) -> None:
        """Prove the NEXT frame is a pong — i.e. nothing (audio!) in between."""
        await self.send_json({"v": 1, "type": "ping", "ts": 1})
        event = await self.recv_text()
        if event.get("type") != "pong":
            raise RuntimeError(f"{self.name}: expected pong, got: {event}")

    async def drain_peer_events(self) -> None:
        """Consume any peer/pong frames left over from the handshake."""
        for _ in range(4):
            try:
                event = await asyncio.wait_for(self.recv_text(), timeout=0.4)
            except (asyncio.TimeoutError, TimeoutError):
                return
            if event.get("type") not in ("peer", "pong"):
                raise RuntimeError(f"{self.name}: unexpected frame during drain: {event}")


async def speak(p: Participant, pcm: bytes, rate: int, utterance_id: str) -> float:
    chunk = int(rate * 0.1) * 2  # 100 ms chunks, like the reference client
    started = time.perf_counter()
    for i in range(0, len(pcm), chunk):
        await p.ws.send(pcm[i : i + chunk])
    await p.send_json({"v": 1, "type": "flush", "utterance_id": utterance_id})
    return started


async def open_room(url: str) -> tuple[Participant, Participant]:
    host_ws = await connect(url, max_size=2**24)
    host = Participant(host_ws, "host")
    await host.send_json(
        {
            "v": 1,
            "type": "hello",
            "config": {
                "role": "host",
                "native_language": "ur",
                "preferred_language": "en",
                "audio": {"encoding": "pcm_s16le", "sample_rate": 16_000, "channels": 1},
            },
        }
    )
    ready = await host.recv_text()
    if ready.get("type") != "ready" or ready.get("room") is None:
        raise RuntimeError(f"host: expected ready with room, got: {ready}")
    code = ready["room"]["code"]
    print(f"room created: {code}")

    guest_ws = await connect(url, max_size=2**24)
    guest = Participant(guest_ws, "guest")
    await guest.send_json(
        {
            "v": 1,
            "type": "hello",
            "config": {
                "role": "guest",
                "room": code,
                "audio": {"encoding": "pcm_s16le", "sample_rate": 16_000, "channels": 1},
            },
        }
    )
    g_ready = await guest.recv_text()
    if g_ready.get("type") != "ready" or g_ready.get("room", {}).get("role") != "guest":
        raise RuntimeError(f"guest: expected ready as guest, got: {g_ready}")
    print(
        f"guest joined: languages {g_ready['room']['native_language']}↔"
        f"{g_ready['room']['preferred_language']} (guest configured nothing)"
    )
    joined = await host.recv_text()
    if joined.get("type") != "peer" or joined.get("event") != "joined":
        raise RuntimeError(f"host: expected peer joined, got: {joined}")
    await host.drain_peer_events()
    await guest.drain_peer_events()
    return host, guest


async def expect_voice(
    speaker: Participant,
    listener: Participant,
    pcm: bytes,
    rate: int,
    utterance_id: str,
    expect_language: str,
) -> bool:
    """One utterance from `speaker`; `listener` must receive transcript +
    translation + voice; the speaker must NOT receive any audio back."""
    print(f"\n— {speaker.name} speaks {utterance_id} (expect {expect_language} voice for {listener.name})")
    await speak(speaker, pcm, rate, utterance_id)

    t0 = time.perf_counter()
    tr_listener = await listener.recv_text()
    t_tr = time.perf_counter() - t0
    if tr_listener.get("type") != "transcription":
        raise RuntimeError(f"{listener.name}: expected transcription, got: {tr_listener}")
    decision = tr_listener.get("decision") or {}
    print(
        f"  listener transcript: speaker={tr_listener.get('speaker')} lang={tr_listener.get('language')} "
        f"decision={decision.get('action')}/{decision.get('reason')} ({t_tr * 1000:.0f} ms after flush)"
    )

    t0 = time.perf_counter()
    tl_listener = await listener.recv_text()
    t_tl = time.perf_counter() - t0
    if tl_listener.get("type") != "translation":
        raise RuntimeError(f"{listener.name}: expected translation, got: {tl_listener}")
    print(
        f"  listener translation: {tl_listener.get('source_language')}→{tl_listener.get('target_language')} "
        f"text={tl_listener.get('text', '')[:48]!r} mt_ms={tl_listener.get('duration_ms')} (delivered {t_tl * 1000:.0f} ms)"
    )

    t0 = time.perf_counter()
    audio_meta = await listener.recv_text()
    audio_pcm = await listener.recv_binary()
    t_audio = time.perf_counter() - t0
    ok = (
        audio_meta.get("type") == "audio"
        and audio_meta.get("id") == utterance_id
        and audio_meta.get("language") == expect_language
        and audio_meta.get("speaker") == speaker.name
        and len(audio_pcm) == audio_meta.get("bytes")
        and rms(audio_pcm) > 0.01
    )
    print(
        f"  listener voice: {expect_language} @ {audio_meta.get('sample_rate')} Hz "
        f"{len(audio_pcm)} B RMS={rms(audio_pcm):.3f} synth_ms={audio_meta.get('synthesis_ms')} "
        f"(delivered {t_audio * 1000:.0f} ms) — RMS audible: {rms(audio_pcm) > 0.01}"
    )

    tr_speaker = await speaker.recv_text()
    if tr_speaker.get("type") != "transcription" or tr_speaker.get("id") != utterance_id:
        raise RuntimeError(f"{speaker.name}: expected own transcription, got: {tr_speaker}")
    await speaker.recv_text()  # own translation broadcast
    await speaker.ping_pong()  # NOTHING else — scenario 11 (no audio back)
    print(f"  speaker stream: transcript + translation only, NO audio ✓")
    return ok


async def expect_relay(speaker: Participant, listener: Participant, pcm: bytes, rate: int, utterance_id: str) -> bool:
    """Third language: the listener receives the ORIGINAL audio as a relay;
    nobody receives a translation or TTS audio."""
    print(f"\n— {speaker.name} speaks {utterance_id} (expect ORIGINAL relay to {listener.name}, no TTS)")
    await speak(speaker, pcm, rate, utterance_id)

    tr_listener = await listener.recv_text()
    if tr_listener.get("type") != "transcription":
        raise RuntimeError(f"{listener.name}: expected transcription, got: {tr_listener}")
    decision = tr_listener.get("decision") or {}

    relay_meta = await listener.recv_text()
    relay_pcm = await listener.recv_binary()
    ok = (
        decision.get("action") == "passthrough"
        and relay_meta.get("type") == "relay"
        and relay_meta.get("speaker") == speaker.name
        and len(relay_pcm) == relay_meta.get("bytes")
        and relay_meta.get("sample_rate") == 16_000
        and rms(relay_pcm) > 0.01
    )
    print(
        f"  decision={decision.get('action')}/{decision.get('reason')} lang={tr_listener.get('language')} · "
        f"relay {len(relay_pcm)} B @ {relay_meta.get('sample_rate')} Hz RMS={rms(relay_pcm):.3f}"
    )

    tr_speaker = await speaker.recv_text()
    if tr_speaker.get("type") != "transcription":
        raise RuntimeError(f"{speaker.name}: expected own transcription, got: {tr_speaker}")
    await speaker.ping_pong()
    await listener.ping_pong()
    print("  no translation, no TTS on either side ✓")
    return ok


async def run(url: str) -> int:
    failures = 0
    host, guest = await open_room(url)

    ur_pcm, ur_rate = read_wav(FIXTURES / "ur_greeting.wav")
    en_pcm, en_rate = read_wav(FIXTURES / "en_hello.wav")
    es_pcm, es_rate = read_wav(FIXTURES / "es_hola.wav")

    # S1 (scenario 1 + 11): host native ur → guest gets the en voice.
    ok = await expect_voice(host, guest, ur_pcm, ur_rate, "h_c_0001_uuuu01", "en")
    failures += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] S1 host ur → guest en voice, host hears nothing")

    # S2 (scenario 2 + 11): guest preferred en → host gets the ur voice.
    ok = await expect_voice(guest, host, en_pcm, en_rate, "g_c_0001_gggg01", "ur")
    failures += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] S2 guest en → host ur voice, guest hears nothing")

    # S3 (scenarios 3/10): host third language es → guest gets the ORIGINAL.
    ok = await expect_relay(host, guest, es_pcm, es_rate, "h_c_0002_eeee02")
    failures += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] S3 host es → guest original relay (no TTS)")

    # S4 (scenario 5): the guest ALSO speaks ur — same language both sides,
    # each translated for the other.
    ok = await expect_voice(guest, host, ur_pcm, ur_rate, "g_c_0002_uuuu02", "en")
    failures += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] S4 guest ur (same language both sides) → host en voice")

    await host.ws.close()
    await guest.ws.close()
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="ws://localhost:3000/ws/audio")
    args = parser.parse_args()
    for name in ("ur_greeting.wav", "en_hello.wav", "es_hola.wav"):
        if not (FIXTURES / name).is_file():
            raise SystemExit(f"missing fixture: {name}")
    failures = asyncio.run(run(args.url))
    if failures:
        raise SystemExit(f"{failures} check(s) FAILED")
    print("\nall Phase 5 two-participant E2E checks passed")


if __name__ == "__main__":
    main()
