#!/usr/bin/env python3
"""End-to-end tests for the Linux voice server.
Starts the server, runs WebSocket tests, then shuts down.
"""

import asyncio
import json
import math
import os
import struct
import sys
import tempfile

# Add scripts to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import voice_server
from websockets.asyncio.client import connect

# Override paths for testing
MODELS_DIR = os.environ.get("VOSK_MODELS_DIR", os.path.join(
    os.path.expanduser("~"), ".local", "share", "claude-code-voice", "models"
))
voice_server.MODELS_DIR = MODELS_DIR

passed = 0
failed = 0

# Create a temporary settings.json with non-native language for local STT testing
_test_settings_dir = None


def setup_test_settings(language="he"):
    """Create temporary settings.json so the server uses local STT."""
    global _test_settings_dir
    _test_settings_dir = tempfile.mkdtemp()
    settings_path = os.path.join(_test_settings_dir, "settings.json")
    with open(settings_path, "w") as f:
        json.dump({"language": language}, f)

    # Monkey-patch read_language to use our test settings
    original_read_language = voice_server.read_language

    def test_read_language():
        try:
            with open(settings_path) as f:
                data = json.load(f)
            return data.get("language", "en").lower().strip()
        except (OSError, json.JSONDecodeError):
            return "en"

    voice_server.read_language = test_read_language
    return settings_path, original_read_language


def report(name: str, ok: bool, detail: str = ""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS: {name}")
    else:
        failed += 1
        print(f"  FAIL: {name} — {detail}")


def generate_sine_pcm(freq: float = 440.0, duration: float = 1.0) -> bytes:
    """Generate 16-bit mono PCM sine wave at 16kHz."""
    sample_rate = 16000
    samples = int(sample_rate * duration)
    pcm = b""
    for i in range(samples):
        sample = int(32767 * 0.5 * math.sin(2 * math.pi * freq * i / sample_rate))
        pcm += struct.pack("<h", sample)
    return pcm


# ── Unit Tests ────────────────────────────────────────────────────

def test_unit():
    print("\n=== Unit Tests ===\n")

    # Language helpers
    report("lang_code('en') == 'en'", voice_server.lang_code("en") == "en")
    report("lang_code('he') == 'he'", voice_server.lang_code("he") == "he")
    report("lang_code('hebrew') == 'he'", voice_server.lang_code("hebrew") == "he")
    report("lang_code('español') == 'es'", voice_server.lang_code("español") == "es")
    report("lang_code('unknown') == 'unknown'", voice_server.lang_code("unknown") == "unknown")
    report("is_native('en') == True", voice_server.is_native_language("en") is True)
    report("is_native('fr') == True", voice_server.is_native_language("fr") is True)
    report("is_native('he') == False", voice_server.is_native_language("he") is False)
    report("is_native('ar') == False", voice_server.is_native_language("ar") is False)

    # WAV creation
    pcm = b"\x00\x01" * 16000
    wav = voice_server.create_wav(pcm)
    report("WAV starts with RIFF", wav[:4] == b"RIFF")
    report("WAV contains WAVE", wav[8:12] == b"WAVE")
    report("WAV correct size", len(wav) == 44 + len(pcm))

    # Vosk model loading
    model = voice_server.get_vosk_model("en")
    report("Vosk model loads", model is not None, "Model not found — set VOSK_MODELS_DIR")

    # Vosk transcription with silence
    if model:
        silence = b"\x00\x00" * 16000
        result = voice_server.transcribe_vosk(silence, "en")
        report("Silence transcribes to empty", result == "", f"got '{result}'")

    # Empty transcription
    result = voice_server.transcribe_vosk(b"", "en")
    report("Empty PCM → empty string", result == "")

    # OAuth token reading (should not crash, may return None)
    token = voice_server.read_oauth_token()
    report("read_oauth_token() doesn't crash", True)
    if token:
        print(f"    (found token: {token[:10]}...)")
    else:
        print("    (no token found — expected in test env)")


# ── E2E Tests ─────────────────────────────────────────────────────

async def receive_until_endpoint(ws, timeout=15):
    """Receive messages until TranscriptEndpoint or timeout."""
    responses = []
    try:
        while True:
            msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
            data = json.loads(msg)
            responses.append(data)
            if data.get("type") == "TranscriptEndpoint":
                break
    except (asyncio.TimeoutError, Exception):
        pass
    return responses


async def test_e2e():
    print("\n=== E2E Tests (Local STT) ===\n")

    # Use non-native language so all tests go through local Vosk STT
    settings_path, original_read = setup_test_settings("he")
    print(f"  Using test settings: language=he (local STT path)\n")

    # Start server as background task
    server_task = asyncio.create_task(voice_server.main())
    await asyncio.sleep(1)

    try:
        # Test 1: Basic connection
        print("  Test: WebSocket connection")
        async with connect("ws://127.0.0.1:19876") as ws:
            report("WebSocket connects", True)

        # Test 2: Full local STT flow
        print("  Test: Local STT flow (send audio + CloseStream)")
        async with connect("ws://127.0.0.1:19876") as ws:
            pcm = generate_sine_pcm(440, 0.5)
            chunk_size = 3200  # 100ms chunks
            for i in range(0, len(pcm), chunk_size):
                await ws.send(pcm[i:i + chunk_size])
            print(f"    Sent {len(pcm)} bytes in {len(pcm) // chunk_size} chunks")

            await ws.send(json.dumps({"type": "CloseStream"}))

            responses = await receive_until_endpoint(ws)
            for r in responses:
                print(f"    Received: {r}")

            has_text = any(r["type"] == "TranscriptText" for r in responses)
            has_end = any(r["type"] == "TranscriptEndpoint" for r in responses)
            report("Receives TranscriptText", has_text, f"responses: {responses}")
            report("Receives TranscriptEndpoint", has_end, f"responses: {responses}")
            report("At least 2 messages", len(responses) >= 2, f"got {len(responses)}")

        # Test 3: KeepAlive is ignored
        print("  Test: KeepAlive handling")
        async with connect("ws://127.0.0.1:19876") as ws:
            await ws.send(json.dumps({"type": "KeepAlive"}))
            await asyncio.sleep(0.3)
            report("KeepAlive ignored", True)

        # Test 4: Multiple sequential connections
        print("  Test: Multiple sequential connections")
        multi_ok = True
        for i in range(3):
            async with connect("ws://127.0.0.1:19876") as ws:
                await ws.send(b"\x00\x00" * 1600)
                await ws.send(json.dumps({"type": "CloseStream"}))
                responses = await receive_until_endpoint(ws, timeout=10)
                if not any(r["type"] == "TranscriptEndpoint" for r in responses):
                    multi_ok = False
        report("3 sequential connections", multi_ok)

        # Test 5: Graceful disconnect without CloseStream
        print("  Test: Graceful disconnect")
        async with connect("ws://127.0.0.1:19876") as ws:
            await ws.send(b"\x00\x01" * 800)
        await asyncio.sleep(0.3)
        async with connect("ws://127.0.0.1:19876") as ws:
            report("Server alive after client disconnect", True)

        # Test 6: Empty audio with CloseStream
        print("  Test: Empty audio + CloseStream")
        async with connect("ws://127.0.0.1:19876") as ws:
            await ws.send(json.dumps({"type": "CloseStream"}))
            responses = await receive_until_endpoint(ws, timeout=5)
            has_end = any(r["type"] == "TranscriptEndpoint" for r in responses)
            report("Empty audio → TranscriptEndpoint", has_end, f"responses: {responses}")

        # Test 7: Language switching mid-session
        print("  Test: Language switching")
        # Switch to English (native) — may proxy to Anthropic or fall back to local
        with open(settings_path, "w") as f:
            json.dump({"language": "en"}, f)
        async with connect("ws://127.0.0.1:19876") as ws:
            await ws.send(b"\x00\x00" * 1600)
            await ws.send(json.dumps({"type": "CloseStream"}))
            responses = await receive_until_endpoint(ws, timeout=10)
            # Accept either: local fallback (TranscriptEndpoint) or proxy response (any message)
            got_response = len(responses) > 0
            report("Native lang routes correctly", got_response, f"responses: {responses}")

        # Switch back to non-native for any remaining tests
        with open(settings_path, "w") as f:
            json.dump({"language": "he"}, f)

    finally:
        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass
        voice_server.read_language = original_read


# ── Main ──────────────────────────────────────────────────────────

def main():
    print("=" * 50)
    print("Claude Code Voice — Linux E2E Tests")
    print("=" * 50)

    if not os.path.isdir(MODELS_DIR):
        print(f"\nWARNING: Models directory not found: {MODELS_DIR}")
        print("Set VOSK_MODELS_DIR env var or install a model first.")

    test_unit()
    asyncio.run(test_e2e())

    print("\n" + "=" * 50)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 50)

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
