"""RtmsClient protocol tests against the FakeZoomRTMS double (Phase 7).

The bridge integration test (test_zoom_bridge.py) covers the happy path
end-to-end; these tests pin the RTMS client's own contract:

- signature mismatch is rejected by the server → RtmsError (fail loud);
- SESSION_STATE_UPDATE(session_state=STOPPED) ends the session cleanly;
- the defensive audio-content decode: base64 string (reference shape),
  {"data": …} wrapper, raw binary frames, odd-length and garbage content;
- keep-alives are answered on both signaling and media sockets.
"""

from __future__ import annotations

import asyncio
import base64
import json
import struct

import pytest

from app.zoom.fake_rtms import FakeZoomRTMS, wait_until
from app.zoom.rtms import RtmsClient, RtmsError


def _tone(seconds: float = 0.1, rate: int = 16000, amp: int = 5000) -> bytes:
    return b"".join(
        struct.pack("<h", int(amp * (1 if (i // 8) % 2 else -1)))
        for i in range(int(rate * seconds))
    )


def _client(fake: FakeZoomRTMS, *, secret: str = "test-secret", collected: list[bytes]) -> RtmsClient:
    async def on_audio(pcm: bytes) -> None:
        collected.append(pcm)

    return RtmsClient(
        client_id="test-client",
        client_secret=secret,
        meeting_uuid=fake._meeting_uuid,
        rtms_stream_id=fake._stream_id,
        server_urls=fake.signaling_url or "",
        on_audio=on_audio,
    )


def test_wrong_secret_is_rejected_as_rtms_error():
    async def scenario() -> None:
        fake = FakeZoomRTMS(
            client_id="test-client", client_secret="test-secret",
            meeting_uuid="mtg-sec-1", rtms_stream_id="s1",
        )
        await fake.start()
        collected: list[bytes] = []
        client = _client(fake, secret="WRONG", collected=collected)
        with pytest.raises(RtmsError):
            await asyncio.wait_for(client.run(), timeout=5)
        assert fake.signaling_signature_ok is False
        await fake.stop()

    asyncio.run(scenario())


def test_session_stopped_by_zoom_ends_the_run():
    async def scenario() -> None:
        fake = FakeZoomRTMS(
            client_id="test-client", client_secret="test-secret",
            meeting_uuid="mtg-stop-1", rtms_stream_id="s1",
            keepalive_interval_s=0.1,
        )
        await fake.start()
        collected: list[bytes] = []
        client = _client(fake, collected=collected)
        run_task = asyncio.create_task(client.run())
        await wait_until(lambda: len(fake.ready_acks) == 1, timeout=5, what="media ready")
        await fake.send_session_stopped(stop_reason=6)  # STOP_BC_MEETING_ENDED
        with pytest.raises(RtmsError, match="session stopped"):
            await asyncio.wait_for(run_task, timeout=5)
        await fake.stop()

    asyncio.run(scenario())


def test_audio_content_decode_variants():
    """base64 (reference), {"data": …} wrapper, binary, junk — only sane bytes flow."""

    async def scenario() -> None:
        fake = FakeZoomRTMS(
            client_id="test-client", client_secret="test-secret",
            meeting_uuid="mtg-dec-1", rtms_stream_id="s1",
            keepalive_interval_s=0.1,
        )
        await fake.start()
        collected: list[bytes] = []
        client = _client(fake, collected=collected)
        run_task = asyncio.create_task(client.run())
        await wait_until(lambda: len(fake.ready_acks) == 1, timeout=5, what="media ready")

        good = _tone(0.05)
        await fake.send_dict_content_audio(good)                       # {"data": b64}
        await fake.send_binary_media(good[:100])                       # raw binary frame
        await fake.send_raw_media({"msg_type": 14, "content": "!!!not-base64!!!"})
        await fake.send_raw_media({"msg_type": 14, "content": "AAAA"})  # odd → dropped
        await fake.send_raw_media({"msg_type": 14, "content": None})    # nothing
        await fake.send_raw_media({"msg_type": 14, "content": 12345})   # wrong type

        await wait_until(lambda: len(collected) >= 2, timeout=5, what="decoded audio frames")
        assert good in collected
        assert good[:100] in collected
        # Nothing garbage flowed: every collected frame is even-length.
        assert all(len(frame) % 2 == 0 for frame in collected)
        # The client did NOT die from the junk frames — still answering.
        await fake.send_session_stopped()
        with pytest.raises(RtmsError, match="session stopped"):
            await asyncio.wait_for(run_task, timeout=5)
        await fake.stop()

    asyncio.run(scenario())


def test_keepalives_answered_on_both_sockets():
    async def scenario() -> None:
        fake = FakeZoomRTMS(
            client_id="test-client", client_secret="test-secret",
            meeting_uuid="mtg-ka-1", rtms_stream_id="s1",
            keepalive_interval_s=0.1,
        )
        await fake.start()
        collected: list[bytes] = []
        client = _client(fake, collected=collected)
        run_task = asyncio.create_task(client.run())
        await wait_until(lambda: len(fake.ready_acks) == 1, timeout=5, what="media ready")
        await asyncio.sleep(0.5)  # several keep-alive cycles
        assert fake.keepalive_responses >= 2
        await fake.send_session_stopped()
        with pytest.raises(RtmsError):
            await asyncio.wait_for(run_task, timeout=5)
        await fake.stop()

    asyncio.run(scenario())
