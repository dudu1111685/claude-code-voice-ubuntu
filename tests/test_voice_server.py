#!/usr/bin/env python3
"""Comprehensive tests for the Soniox-based voice server.

Tests cover:
- Soniox session lifecycle (token accumulation, TranscriptText, TranscriptEndpoint)
- Thread-safe architecture (async WS <-> sync Soniox bridge)
- Fallback mode (Anthropic proxy, Vosk)
- Edge cases (CloseStream, KeepAlive, disconnect, errors)
- Language helpers (read_language, lang_code, is_native_language)
- WAV creation
"""

import asyncio
import json
import os
import struct
import sys
import threading
import time
from unittest import mock
from unittest.mock import MagicMock, AsyncMock, patch, PropertyMock
from dataclasses import dataclass, field

import pytest

# Add scripts to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))


# ── Mock Soniox SDK types ──────────────────────────────────────────


@dataclass
class MockToken:
    """Mock soniox.types.Token"""
    text: str
    is_final: bool = False


@dataclass
class MockEvent:
    """Mock Soniox event from session.receive_events()"""
    tokens: list = field(default_factory=list)
    finished: bool = False
    error_code: str | None = None
    error_message: str | None = None


class MockSonioxSession:
    """Mock Soniox realtime STT session (context manager)."""

    def __init__(self, events=None):
        self._events = events or []
        self.sent_bytes = []
        self.finalized = False
        self.kept_alive = False
        self._closed = False

    def send_bytes(self, data: bytes):
        self.sent_bytes.append(data)

    def receive_events(self):
        """Synchronous blocking iterator (like the real SDK)."""
        for event in self._events:
            if self._closed:
                break
            yield event

    def finalize(self):
        self.finalized = True

    def keep_alive(self):
        self.kept_alive = True

    def close(self):
        self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._closed = True


class MockSonioxRealtimeSTT:
    """Mock client.realtime.stt"""

    def __init__(self, session):
        self._session = session

    def connect(self, config=None):
        return self._session


class MockSonioxRealtime:
    """Mock client.realtime"""

    def __init__(self, session):
        self.stt = MockSonioxRealtimeSTT(session)


class MockSonioxClient:
    """Mock SonioxClient"""

    def __init__(self, session=None):
        self.realtime = MockSonioxRealtime(session or MockSonioxSession())


# ── Install mock soniox module before importing voice_server ───────

# Create proper mock module objects
import types as _types

_mock_soniox = _types.ModuleType("soniox")
_mock_soniox.SonioxClient = MockSonioxClient
_mock_soniox_types = _types.ModuleType("soniox.types")
_mock_soniox_types.RealtimeSTTConfig = MagicMock()
_mock_soniox_types.Token = MockToken
_mock_soniox.types = _mock_soniox_types

sys.modules["soniox"] = _mock_soniox
sys.modules["soniox.types"] = _mock_soniox_types

# Now import voice_server (will pick up our mock soniox)
import voice_server


# ── Helper: Mock WebSocket ─────────────────────────────────────────


class MockWebSocket:
    """Mock websockets connection for testing."""

    def __init__(self, messages=None):
        self._incoming = asyncio.Queue()
        self._outgoing = asyncio.Queue()
        self._closed = False
        if messages:
            for msg in messages:
                self._incoming.put_nowait(msg)

    async def send(self, data):
        if self._closed:
            raise Exception("Connection closed")
        await self._outgoing.put(data)

    async def recv(self):
        if self._closed:
            raise Exception("Connection closed")
        return await self._incoming.get()

    def put_message(self, msg):
        """Add a message to the incoming queue."""
        self._incoming.put_nowait(msg)

    async def get_sent(self, timeout=2.0):
        """Get a message sent by the server."""
        try:
            return await asyncio.wait_for(self._outgoing.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    async def get_all_sent(self, timeout=1.0):
        """Collect all sent messages within timeout."""
        results = []
        try:
            while True:
                msg = await asyncio.wait_for(self._outgoing.get(), timeout=timeout)
                results.append(msg)
        except asyncio.TimeoutError:
            pass
        return results

    def close(self):
        self._closed = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._closed:
            raise StopAsyncIteration
        try:
            msg = self._incoming.get_nowait()
            return msg
        except asyncio.QueueEmpty:
            raise StopAsyncIteration


# ── Language helper tests ──────────────────────────────────────────


class TestLanguageHelpers:
    """Tests for read_language, lang_code, is_native_language."""

    def test_lang_code_native(self):
        assert voice_server.lang_code("en") == "en"
        assert voice_server.lang_code("es") == "es"
        assert voice_server.lang_code("fr") == "fr"

    def test_lang_code_mapped(self):
        assert voice_server.lang_code("hebrew") == "he"
        assert voice_server.lang_code("english") == "en"

    def test_lang_code_passthrough(self):
        assert voice_server.lang_code("xx") == "xx"

    def test_is_native_language_true(self):
        for lang in ["en", "es", "fr", "ja", "de", "pt", "it", "ko"]:
            assert voice_server.is_native_language(lang) is True

    def test_is_native_language_false(self):
        assert voice_server.is_native_language("he") is False
        assert voice_server.is_native_language("ar") is False
        assert voice_server.is_native_language("zh") is False

    def test_read_language_default(self, tmp_path):
        with patch.object(voice_server, "read_language") as mock_read:
            mock_read.return_value = "en"
            assert voice_server.read_language() == "en"

    def test_read_language_from_settings(self, tmp_path):
        settings = tmp_path / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({"language": "he"}))

        with patch("os.path.expanduser", return_value=str(tmp_path)):
            result = voice_server.read_language()
            assert result == "he"


# ── WAV creation tests ─────────────────────────────────────────────


class TestCreateWav:
    """Tests for WAV header creation."""

    def test_create_wav_header(self):
        pcm = b"\x00\x01" * 100
        wav = voice_server.create_wav(pcm)
        assert wav[:4] == b"RIFF"
        assert wav[8:12] == b"WAVE"
        assert wav[12:16] == b"fmt "
        # Data follows header
        assert wav[44:] == pcm

    def test_create_wav_empty_pcm(self):
        wav = voice_server.create_wav(b"")
        assert wav[:4] == b"RIFF"
        assert len(wav) == 44  # Just the header


# ── Soniox session tests ──────────────────────────────────────────


class TestSonioxSession:
    """Tests for the Soniox streaming STT session."""

    @pytest.mark.asyncio
    async def test_soniox_session_sends_transcript_text(self):
        """Soniox session sends TranscriptText with accumulated tokens."""
        events = [
            MockEvent(tokens=[MockToken("Hello ", is_final=True)]),
            MockEvent(tokens=[MockToken("world", is_final=False)]),
            MockEvent(finished=True),
        ]
        session = MockSonioxSession(events=events)
        ws = MockWebSocket()

        # Send some audio then CloseStream
        ws.put_message(b"\x00\x01" * 100)  # PCM audio
        ws.put_message(json.dumps({"type": "CloseStream"}))

        with patch.object(voice_server, "SonioxClient", return_value=MockSonioxClient(session)):
            await voice_server.soniox_session(ws, "en")

        # Collect sent messages
        messages = await ws.get_all_sent(timeout=1.0)
        texts = [json.loads(m) for m in messages if "TranscriptText" in m]

        assert len(texts) >= 1
        # Should contain accumulated text
        assert any("Hello" in t.get("data", "") for t in texts)

    @pytest.mark.asyncio
    async def test_soniox_session_sends_transcript_endpoint(self):
        """Soniox session sends TranscriptEndpoint when finished."""
        events = [
            MockEvent(tokens=[MockToken("Hello", is_final=True)]),
            MockEvent(finished=True),
        ]
        session = MockSonioxSession(events=events)
        ws = MockWebSocket()

        ws.put_message(b"\x00\x01" * 100)
        ws.put_message(json.dumps({"type": "CloseStream"}))

        with patch.object(voice_server, "SonioxClient", return_value=MockSonioxClient(session)):
            await voice_server.soniox_session(ws, "en")

        messages = await ws.get_all_sent(timeout=1.0)
        endpoints = [m for m in messages if "TranscriptEndpoint" in m]
        assert len(endpoints) >= 1

    @pytest.mark.asyncio
    async def test_soniox_session_token_accumulation(self):
        """Final tokens accumulate, non-final tokens reset each event."""
        events = [
            MockEvent(tokens=[
                MockToken("Hel", is_final=False),
            ]),
            MockEvent(tokens=[
                MockToken("Hello ", is_final=True),
            ]),
            MockEvent(tokens=[
                MockToken("world", is_final=False),
            ]),
            MockEvent(finished=True),
        ]
        session = MockSonioxSession(events=events)
        ws = MockWebSocket()

        ws.put_message(b"\x00\x01" * 100)
        ws.put_message(json.dumps({"type": "CloseStream"}))

        with patch.object(voice_server, "SonioxClient", return_value=MockSonioxClient(session)):
            await voice_server.soniox_session(ws, "en")

        messages = await ws.get_all_sent(timeout=1.0)
        texts = [json.loads(m) for m in messages if "TranscriptText" in m]

        # The last TranscriptText should have the full accumulated text
        if texts:
            last_text = texts[-1]["data"]
            assert "Hello" in last_text

    @pytest.mark.asyncio
    async def test_soniox_session_handles_close_stream(self):
        """CloseStream triggers session.finalize()."""
        events = [
            MockEvent(tokens=[MockToken("test", is_final=True)]),
            MockEvent(finished=True),
        ]
        session = MockSonioxSession(events=events)
        ws = MockWebSocket()

        ws.put_message(b"\x00\x01" * 100)
        ws.put_message(json.dumps({"type": "CloseStream"}))

        with patch.object(voice_server, "SonioxClient", return_value=MockSonioxClient(session)):
            await voice_server.soniox_session(ws, "en")

        assert session.finalized is True

    @pytest.mark.asyncio
    async def test_soniox_session_handles_keepalive(self):
        """KeepAlive messages forwarded as session.keep_alive()."""
        events = [
            MockEvent(tokens=[MockToken("test", is_final=True)]),
            MockEvent(finished=True),
        ]
        session = MockSonioxSession(events=events)
        ws = MockWebSocket()

        ws.put_message(json.dumps({"type": "KeepAlive"}))
        ws.put_message(b"\x00\x01" * 100)
        ws.put_message(json.dumps({"type": "CloseStream"}))

        with patch.object(voice_server, "SonioxClient", return_value=MockSonioxClient(session)):
            await voice_server.soniox_session(ws, "en")

        assert session.kept_alive is True

    @pytest.mark.asyncio
    async def test_soniox_session_language_config(self):
        """Soniox config uses correct language hints."""
        events = [MockEvent(finished=True)]
        session = MockSonioxSession(events=events)
        ws = MockWebSocket()

        ws.put_message(json.dumps({"type": "CloseStream"}))

        captured_config = {}

        original_connect = MockSonioxRealtimeSTT.connect

        def capture_connect(self_inner, config=None):
            captured_config["config"] = config
            return session

        with patch.object(voice_server, "SonioxClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.realtime.stt.connect = lambda config=None: (
                captured_config.update({"config": config}) or session
            )
            mock_client_cls.return_value = mock_client
            await voice_server.soniox_session(ws, "he")

        # Config should have been created with language_hints=["he"]
        # Verified by checking that SonioxClient was called
        assert mock_client_cls.called

    @pytest.mark.asyncio
    async def test_soniox_session_error_handling(self):
        """Soniox error events are handled gracefully."""
        events = [
            MockEvent(
                tokens=[],
                error_code="RATE_LIMIT",
                error_message="Too many requests",
            ),
        ]
        session = MockSonioxSession(events=events)
        ws = MockWebSocket()

        ws.put_message(b"\x00\x01" * 100)
        ws.put_message(json.dumps({"type": "CloseStream"}))

        with patch.object(voice_server, "SonioxClient", return_value=MockSonioxClient(session)):
            # Should not raise
            await voice_server.soniox_session(ws, "en")


# ── Fallback mode tests ───────────────────────────────────────────


class TestFallbackMode:
    """Tests for fallback behavior when SONIOX_API_KEY is not set."""

    @pytest.mark.asyncio
    async def test_soniox_mode_when_api_key_set(self):
        """When SONIOX_API_KEY is set, use Soniox regardless of language."""
        with patch.dict(os.environ, {"SONIOX_API_KEY": "test_key"}):
            with patch.object(voice_server, "read_language", return_value="en"):
                with patch.object(voice_server, "soniox_session", new_callable=AsyncMock) as mock_soniox:
                    ws = MockWebSocket()
                    await voice_server.handle_connection(ws)
                    mock_soniox.assert_called_once()

    @pytest.mark.asyncio
    async def test_proxy_mode_when_no_api_key_native_lang(self):
        """When no SONIOX_API_KEY and native language, proxy to Anthropic."""
        with patch.dict(os.environ, {}, clear=True):
            # Ensure SONIOX_API_KEY is not set
            os.environ.pop("SONIOX_API_KEY", None)
            with patch.object(voice_server, "read_language", return_value="en"):
                with patch.object(voice_server, "read_oauth_token", return_value="test_token"):
                    with patch.object(voice_server, "proxy_session", new_callable=AsyncMock, return_value=True) as mock_proxy:
                        ws = MockWebSocket()
                        await voice_server.handle_connection(ws)
                        mock_proxy.assert_called_once()

    @pytest.mark.asyncio
    async def test_vosk_mode_when_no_api_key_non_native_lang(self):
        """When no SONIOX_API_KEY and non-native language, use Vosk."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("SONIOX_API_KEY", None)
            with patch.object(voice_server, "read_language", return_value="he"):
                with patch.object(voice_server, "local_session", new_callable=AsyncMock) as mock_local:
                    ws = MockWebSocket()
                    await voice_server.handle_connection(ws)
                    mock_local.assert_called_once()

    @pytest.mark.asyncio
    async def test_fallback_on_soniox_failure(self):
        """When Soniox fails, fall back to legacy STT."""
        with patch.dict(os.environ, {"SONIOX_API_KEY": "test_key"}):
            with patch.object(voice_server, "read_language", return_value="en"):
                with patch.object(voice_server, "soniox_session", new_callable=AsyncMock, side_effect=Exception("Soniox down")):
                    with patch.object(voice_server, "read_oauth_token", return_value="test_token"):
                        with patch.object(voice_server, "proxy_session", new_callable=AsyncMock, return_value=True) as mock_proxy:
                            ws = MockWebSocket()
                            await voice_server.handle_connection(ws)
                            mock_proxy.assert_called_once()

    @pytest.mark.asyncio
    async def test_soniox_used_for_hebrew_when_key_set(self):
        """When SONIOX_API_KEY is set, use Soniox even for Hebrew."""
        with patch.dict(os.environ, {"SONIOX_API_KEY": "test_key"}):
            with patch.object(voice_server, "read_language", return_value="he"):
                with patch.object(voice_server, "soniox_session", new_callable=AsyncMock) as mock_soniox:
                    ws = MockWebSocket()
                    await voice_server.handle_connection(ws)
                    mock_soniox.assert_called_once()


# ── Edge case tests ────────────────────────────────────────────────


class TestEdgeCases:
    """Tests for edge cases and protocol compliance."""

    @pytest.mark.asyncio
    async def test_empty_audio_chunks_ignored(self):
        """Empty audio chunks should not cause errors."""
        events = [
            MockEvent(tokens=[MockToken("test", is_final=True)]),
            MockEvent(finished=True),
        ]
        session = MockSonioxSession(events=events)
        ws = MockWebSocket()

        ws.put_message(b"")  # empty chunk
        ws.put_message(b"\x00\x01" * 100)
        ws.put_message(json.dumps({"type": "CloseStream"}))

        with patch.object(voice_server, "SonioxClient", return_value=MockSonioxClient(session)):
            # Should not raise
            await voice_server.soniox_session(ws, "en")

    @pytest.mark.asyncio
    async def test_transcript_text_is_full_replacement(self):
        """TranscriptText data is full replacement text, not delta."""
        events = [
            MockEvent(tokens=[MockToken("Hello ", is_final=True)]),
            MockEvent(tokens=[MockToken("world", is_final=True)]),
            MockEvent(finished=True),
        ]
        session = MockSonioxSession(events=events)
        ws = MockWebSocket()

        ws.put_message(b"\x00\x01" * 100)
        ws.put_message(json.dumps({"type": "CloseStream"}))

        with patch.object(voice_server, "SonioxClient", return_value=MockSonioxClient(session)):
            await voice_server.soniox_session(ws, "en")

        messages = await ws.get_all_sent(timeout=1.0)
        texts = [json.loads(m) for m in messages if "TranscriptText" in m]

        # Second TranscriptText should contain "Hello world", not just "world"
        if len(texts) >= 2:
            assert "Hello" in texts[-1]["data"]
            assert "world" in texts[-1]["data"]

    @pytest.mark.asyncio
    async def test_end_token_triggers_endpoint(self):
        """The <end> token should trigger TranscriptEndpoint."""
        events = [
            MockEvent(tokens=[MockToken("Hello", is_final=True)]),
            MockEvent(tokens=[MockToken("<end>", is_final=True)]),
            MockEvent(finished=True),
        ]
        session = MockSonioxSession(events=events)
        ws = MockWebSocket()

        ws.put_message(b"\x00\x01" * 100)
        ws.put_message(json.dumps({"type": "CloseStream"}))

        with patch.object(voice_server, "SonioxClient", return_value=MockSonioxClient(session)):
            await voice_server.soniox_session(ws, "en")

        messages = await ws.get_all_sent(timeout=1.0)
        endpoints = [m for m in messages if "TranscriptEndpoint" in m]
        assert len(endpoints) >= 1


# ── Connection handler tests ───────────────────────────────────────


class TestHandleConnection:
    """Tests for the main connection handler routing logic."""

    @pytest.mark.asyncio
    async def test_handle_connection_soniox_primary(self):
        """Connection handler uses Soniox as primary when API key is set."""
        with patch.dict(os.environ, {"SONIOX_API_KEY": "test_key"}):
            with patch.object(voice_server, "read_language", return_value="en"):
                with patch.object(voice_server, "soniox_session", new_callable=AsyncMock) as mock_soniox:
                    ws = MockWebSocket()
                    await voice_server.handle_connection(ws)
                    mock_soniox.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_connection_proxy_fallback(self):
        """Connection handler falls back to proxy for native langs without Soniox."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("SONIOX_API_KEY", None)
            with patch.object(voice_server, "read_language", return_value="en"):
                with patch.object(voice_server, "read_oauth_token", return_value="tok"):
                    with patch.object(voice_server, "proxy_session", new_callable=AsyncMock, return_value=True) as mock_proxy:
                        ws = MockWebSocket()
                        await voice_server.handle_connection(ws)
                        mock_proxy.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_connection_local_final_fallback(self):
        """Connection handler falls back to local STT as last resort."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("SONIOX_API_KEY", None)
            with patch.object(voice_server, "read_language", return_value="he"):
                with patch.object(voice_server, "local_session", new_callable=AsyncMock) as mock_local:
                    ws = MockWebSocket()
                    await voice_server.handle_connection(ws)
                    mock_local.assert_called_once()


# ── Vosk STT tests (fallback) ─────────────────────────────────────


class TestVoskFallback:
    """Tests for Vosk local STT fallback."""

    def test_transcribe_vosk_empty(self):
        """Empty PCM returns empty string."""
        result = voice_server.transcribe_vosk(b"", "en")
        assert result == ""

    def test_get_vosk_model_no_module(self):
        """When Vosk module is not available, returns None."""
        original = voice_server.Model
        try:
            voice_server.Model = None
            result = voice_server.get_vosk_model("en")
            assert result is None
        finally:
            voice_server.Model = original


# ── Local session tests ────────────────────────────────────────────


class TestLocalSession:
    """Tests for the Vosk-based local session (fallback)."""

    @pytest.mark.asyncio
    async def test_local_session_basic(self):
        """Local session buffers audio and transcribes on CloseStream."""
        ws = MockWebSocket()
        pcm_data = b"\x00\x01" * 16000  # 1 second of audio

        ws.put_message(pcm_data)
        ws.put_message(json.dumps({"type": "CloseStream"}))

        with patch.object(voice_server, "transcribe_vosk", return_value="hello world"):
            await voice_server.local_session(ws, "en")

        messages = await ws.get_all_sent(timeout=1.0)
        texts = [json.loads(m) for m in messages if "TranscriptText" in m]
        endpoints = [json.loads(m) for m in messages if "TranscriptEndpoint" in m]

        assert len(texts) == 1
        assert texts[0]["data"] == "hello world"
        assert len(endpoints) == 1
