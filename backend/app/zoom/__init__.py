"""Zoom bridge package (Phase 7, ADR-0019).

Public surface:
- `signing`      — RTMS join signature + webhook validation/verification;
- `rtms`         — the RTMS two-phase WebSocket client (audio in);
- `pipeline_client` — a protocol-v1 client of the app's own /ws/audio;
- `bridge`       — worker orchestration (one worker per meeting);
- `router`       — POST /zoom/webhook + GET /zoom/meetings (mounted only
                   when enabled).
"""
