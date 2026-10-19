"""One-shot live voice socket check. Not part of the unit suite."""

from __future__ import annotations

import array
import asyncio
import json
import time

import websockets

VOICE = "ws://127.0.0.1:4702/live"
FRAME = 960


def pcm(amp: int) -> bytes:
    return array.array("h", [amp] * FRAME).tobytes()


async def recv_until(ws, kinds, timeout=20.0):
    deadline = time.monotonic() + timeout
    seen = []
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=min(1.0, remaining))
        except asyncio.TimeoutError:
            continue
        if isinstance(msg, bytes):
            seen.append(("bytes", len(msg)))
            continue
        data = json.loads(msg)
        kind = data.get("type")
        seen.append(kind)
        if kind in kinds:
            return seen, data
    return seen, None


async def main() -> None:
    t0 = time.monotonic()
    async with websockets.connect(VOICE, max_size=2**22) as ws:
        connect_ms = (time.monotonic() - t0) * 1000
        seen, ready = await recv_until(ws, {"ready"}, timeout=5)
        assert ready and ready["type"] == "ready", seen
        print(f"cold_connect_ms={connect_ms:.0f} ready=ok")

        await ws.send(json.dumps({"type": "listen"}))
        seen, first = await recv_until(ws, {"listening", "error"}, timeout=30)
        assert first is not None, seen
        print("listen_ready", seen, first)
        gemini_up = first["type"] == "listening"
        if first["type"] == "error":
            msg = str(first.get("message") or "")
            # Location blocks Gemini Live in this environment; the local
            # socket and listen arming still have to work.
            print("gemini_unavailable", msg)
            assert "voice server is not running" not in msg.lower()

        for _ in range(20):
            await ws.send(pcm(0))
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.5)
        drained = []
        while True:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=0.2)
            except asyncio.TimeoutError:
                break
            if isinstance(msg, bytes):
                drained.append("bytes")
            else:
                drained.append(json.loads(msg).get("type"))
        assert "thinking" not in drained, drained
        print("silence_stays_listening ok", drained)

        for _ in range(8):
            await ws.send(pcm(12000))
            await asyncio.sleep(0.01)
        for _ in range(20):
            await ws.send(pcm(0))
            await asyncio.sleep(0.01)

        if not gemini_up:
            seen, next_msg = await recv_until(
                ws, {"thinking", "error", "listening"}, timeout=12
            )
            print("after_speech_no_gemini", seen)
            assert "thinking" not in seen
            print("OK local_path_only")
            return

        seen, first = await recv_until(ws, {"thinking", "error", "listening"}, timeout=25)
        print("after_speech", seen)
        assert first is not None, seen
        if first["type"] == "error":
            print("ERROR", first)
            raise SystemExit(1)
        if first["type"] == "thinking":
            seen2, done = await recv_until(
                ws, {"turn_done", "listening", "error"}, timeout=45
            )
            print("turn_progress", seen2)
            assert done is not None and done["type"] != "error", seen2
            late_bytes = 0
            end = time.monotonic() + 1.0
            while time.monotonic() < end:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=0.2)
                except asyncio.TimeoutError:
                    continue
                if isinstance(msg, bytes):
                    late_bytes += 1
            print(f"late_server_bytes_after_done={late_bytes}")
        print("OK")


if __name__ == "__main__":
    asyncio.run(main())
