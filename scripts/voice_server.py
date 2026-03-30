#!/usr/bin/env python3
"""Voice server for Claude Code — Soniox streaming STT with fallback.

Primary: Soniox streaming STT (when SONIOX_API_KEY is set)
Fallback: Anthropic proxy (native languages) or Vosk local STT (others)

Architecture (Soniox mode):
  async handle_connection
    +-- asyncio.Queue (audio_queue): WS -> thread
    +-- asyncio.Queue (transcript_queue): thread -> WS
    +-- async ws_reader: reads WS messages, puts on audio_queue
    +-- async ws_writer: reads transcript_queue, sends to WS
    +-- executor thread: soniox_worker(audio_queue, transcript_queue)
"""

import asyncio
import json
import logging
import os
import struct
import threading
from concurrent.futures import ThreadPoolExecutor

try:
    import websockets
    import websockets.exceptions
    # websockets >= 13 uses websockets.asyncio, older versions use top-level
    try:
        from websockets.asyncio.client import connect as ws_connect
        from websockets.asyncio.server import serve as ws_serve
    except (ImportError, ModuleNotFoundError):
        from websockets import connect as ws_connect
        from websockets import serve as ws_serve
except ImportError:
    print("[voice] ERROR: 'websockets' package not found. Run: pip install websockets")
    raise SystemExit(1)

try:
    from vosk import Model, KaldiRecognizer
except ImportError:
    Model = None
    KaldiRecognizer = None

# Soniox SDK (optional — only needed when SONIOX_API_KEY is set)
try:
    from soniox import SonioxClient
    from soniox.types import RealtimeSTTConfig
except ImportError:
    SonioxClient = None
    RealtimeSTTConfig = None


# ── Constants ────────────────────────────────────────────────────────

PORT = 19876
ANTHROPIC_WS = "wss://api.anthropic.com/api/ws/speech_to_text/voice_stream"
INSTALL_DIR = os.path.join(os.path.expanduser("~"), ".local", "share", "claude-code-voice")
MODELS_DIR = os.path.join(INSTALL_DIR, "models")

NATIVE_LANGS = frozenset({
    "en", "es", "fr", "ja", "de", "pt", "it", "ko", "hi", "id",
    "ru", "pl", "tr", "nl", "uk", "el", "cs", "da", "sv", "no",
})

LOCALE_MAP = {
    "he": "he-IL", "hebrew": "he-IL",
    "en": "en-US", "english": "en-US",
    "es": "es-ES", "spanish": "es-ES",
    "fr": "fr-FR", "french": "fr-FR",
    "de": "de-DE", "german": "de-DE",
    "ja": "ja-JP", "japanese": "ja-JP",
    "ko": "ko-KR", "korean": "ko-KR",
    "pt": "pt-BR", "portuguese": "pt-BR",
    "it": "it-IT", "italian": "it-IT",
    "ru": "ru-RU", "russian": "ru-RU",
    "zh": "zh-CN", "chinese": "zh-CN",
    "ar": "ar-SA", "arabic": "ar-SA",
    "hi": "hi-IN", "hindi": "hi-IN",
    "id": "id-ID", "indonesian": "id-ID",
    "tr": "tr-TR", "turkish": "tr-TR",
    "nl": "nl-NL", "dutch": "nl-NL",
    "pl": "pl-PL", "polish": "pl-PL",
    "uk": "uk-UA", "ukrainian": "uk-UA",
    "el": "el-GR", "greek": "el-GR",
    "cs": "cs-CZ", "czech": "cs-CZ",
    "da": "da-DK", "danish": "da-DK",
    "sv": "sv-SE", "swedish": "sv-SE",
    "no": "nb-NO", "norwegian": "nb-NO",
}

VOSK_LANG_MAP = {
    "he": "he", "ar": "ar", "zh": "cn", "en": "en-us",
    "es": "es", "fr": "fr", "de": "de", "ja": "ja",
    "ko": "ko", "pt": "pt", "it": "it", "ru": "ru",
    "hi": "hi", "tr": "tr", "nl": "nl", "pl": "pl",
    "uk": "uk", "el": "el", "cs": "cs",
}

FINALIZE_TIMEOUT = 5.0  # seconds to wait for Soniox finalize

# ── Logging ──────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="[voice] %(message)s")
log = logging.getLogger("voice")

# Cache loaded Vosk models
_vosk_models: dict = {}

# Thread pool for Soniox sync operations
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="soniox")


# ── Language helpers ─────────────────────────────────────────────────

def _read_settings() -> dict:
    """Read Claude Code settings.json."""
    settings_path = os.path.join(os.path.expanduser("~"), ".claude", "settings.json")
    try:
        with open(settings_path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError, AttributeError):
        return {}


def read_language() -> str:
    """Read configured language from Claude Code settings."""
    data = _read_settings()
    lang = data.get("language", "en")
    return lang.lower().strip()


def read_soniox_api_key() -> str:
    """Read Soniox API key from env var or settings.json."""
    key = os.environ.get("SONIOX_API_KEY", "").strip()
    if key:
        return key
    data = _read_settings()
    return data.get("env", {}).get("SONIOX_API_KEY", "").strip()


def lang_code(raw: str) -> str:
    """Convert raw language string to 2-letter code."""
    if raw in NATIVE_LANGS:
        return raw
    mapped = LOCALE_MAP.get(raw)
    if mapped:
        return mapped[:2]
    return raw


def is_native_language(raw: str) -> bool:
    """Check if language is natively supported by Anthropic."""
    return lang_code(raw) in NATIVE_LANGS


# ── OAuth token ──────────────────────────────────────────────────────

def read_oauth_token() -> str | None:
    """Read OAuth token from Claude Code's credential storage."""
    # Path 1: Remote/web mode token file
    for base in [os.path.expanduser("~"), "/home/claude"]:
        token_path = os.path.join(base, ".claude", "remote", ".oauth_token")
        try:
            with open(token_path) as f:
                token = f.read().strip()
            if token:
                return token
        except OSError:
            pass

    # Path 2: credentials.json
    cred_path = os.path.join(os.path.expanduser("~"), ".claude", "credentials.json")
    try:
        with open(cred_path) as f:
            data = json.load(f)
        oauth = data.get("claudeAiOauth", {})
        token = oauth.get("accessToken")
        if token:
            return token
    except (OSError, json.JSONDecodeError, AttributeError):
        pass

    # Path 3: secret-tool (GNOME Keyring)
    try:
        import subprocess
        result = subprocess.run(
            ["secret-tool", "lookup", "service", "Claude Code-credentials"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout.strip())
            oauth = data.get("claudeAiOauth", {})
            token = oauth.get("accessToken")
            if token:
                return token
    except (OSError, json.JSONDecodeError, Exception):
        pass

    return None


# ── WAV creation ─────────────────────────────────────────────────────

def create_wav(pcm: bytes) -> bytes:
    """Create WAV header for 16-bit mono 16kHz PCM data."""
    data_size = len(pcm)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,      # fmt chunk size
        1,       # PCM format
        1,       # mono
        16000,   # sample rate
        32000,   # byte rate
        2,       # block align
        16,      # bits per sample
        b"data",
        data_size,
    )
    return header + pcm


# ── Vosk STT (fallback) ─────────────────────────────────────────────

def get_vosk_model(lang: str):
    """Load or retrieve cached Vosk model."""
    if Model is None:
        return None

    vosk_lang = VOSK_LANG_MAP.get(lang, lang)

    if vosk_lang in _vosk_models:
        return _vosk_models[vosk_lang]

    if os.path.isdir(MODELS_DIR):
        for entry in os.listdir(MODELS_DIR):
            if vosk_lang in entry.lower() and os.path.isdir(os.path.join(MODELS_DIR, entry)):
                model_path = os.path.join(MODELS_DIR, entry)
                log.info(f"Loading Vosk model: {entry}")
                model = Model(model_path)
                _vosk_models[vosk_lang] = model
                return model

        # Try any available model as fallback
        for entry in os.listdir(MODELS_DIR):
            model_path = os.path.join(MODELS_DIR, entry)
            if os.path.isdir(model_path):
                log.info(f"Using fallback Vosk model: {entry}")
                model = Model(model_path)
                _vosk_models[vosk_lang] = model
                return model

    log.warning("No Vosk model found")
    return None


def transcribe_vosk(pcm: bytes, lang: str) -> str:
    """Transcribe PCM audio using Vosk."""
    if not pcm:
        return ""

    duration = len(pcm) / 32000.0
    log.info(f"{duration:.1f}s -> Vosk STT ({lang})")

    model = get_vosk_model(lang)
    if model is None:
        log.warning("No Vosk model available")
        return ""

    rec = KaldiRecognizer(model, 16000)
    rec.AcceptWaveform(pcm)
    result = json.loads(rec.FinalResult())
    text = result.get("text", "")

    if text:
        log.info(f'"{text}"')
    return text


# ── Soniox streaming STT ────────────────────────────────────────────

# Sentinel values for queue communication
_CLOSE_STREAM = object()
_KEEP_ALIVE = object()
_DISCONNECT = object()


def _soniox_worker(
    audio_queue: asyncio.Queue,
    transcript_queue: asyncio.Queue,
    loop: asyncio.AbstractEventLoop,
    lang: str,
):
    """Synchronous worker that runs in a thread.

    Reads audio from audio_queue, sends to Soniox,
    puts transcript messages on transcript_queue.
    """
    code = lang_code(lang)
    # Ensure the env var is set for the SDK (it may come from settings.json)
    key = read_soniox_api_key()
    if key:
        os.environ["SONIOX_API_KEY"] = key
    client = SonioxClient()
    config = RealtimeSTTConfig(
        model="stt-rt-v4",
        audio_format="pcm_s16le",
        sample_rate=16000,
        num_channels=1,
        language_hints=[code],
        enable_endpoint_detection=True,
    )

    final_tokens = []

    def put_transcript(msg):
        """Thread-safe put to transcript_queue."""
        loop.call_soon_threadsafe(transcript_queue.put_nowait, msg)

    try:
        with client.realtime.stt.connect(config=config) as session:

            # Sender thread: reads audio_queue, sends to Soniox
            def audio_sender():
                while True:
                    # Block waiting for audio from the async WS reader
                    future = asyncio.run_coroutine_threadsafe(
                        audio_queue.get(), loop
                    )
                    try:
                        msg = future.result(timeout=30)
                    except Exception:
                        break

                    if msg is _DISCONNECT:
                        break
                    elif msg is _CLOSE_STREAM:
                        session.finalize()
                        break
                    elif msg is _KEEP_ALIVE:
                        session.keep_alive()
                    elif isinstance(msg, (bytes, bytearray)):
                        if msg:  # skip empty chunks
                            session.send_bytes(bytes(msg))

            sender_thread = threading.Thread(
                target=audio_sender, daemon=True, name="soniox-sender"
            )
            sender_thread.start()

            # Main: receive events from Soniox (blocking iterator)
            for event in session.receive_events():
                if event.error_code:
                    log.error(f"Soniox error: {event.error_code} - {event.error_message}")
                    break

                non_final_tokens = []
                for token in event.tokens:
                    if token.text in ("<end>", "<fin>"):
                        if token.text == "<end>":
                            put_transcript(json.dumps({"type": "TranscriptEndpoint"}))
                        continue
                    if token.is_final:
                        final_tokens.append(token)
                    else:
                        non_final_tokens.append(token)

                # Build full replacement text
                full_text = "".join(
                    t.text for t in final_tokens + non_final_tokens
                )
                if full_text.strip():
                    put_transcript(json.dumps({
                        "type": "TranscriptText",
                        "data": full_text.strip(),
                    }))

                if event.finished:
                    put_transcript(json.dumps({"type": "TranscriptEndpoint"}))
                    break

            sender_thread.join(timeout=5)

    except Exception as e:
        log.error(f"Soniox worker error: {e}")
    finally:
        # Signal the ws_writer to stop
        put_transcript(None)


async def soniox_session(ws_client, lang: str):
    """Stream audio from Claude Code to Soniox, relay transcripts back.

    Uses thread pool for sync Soniox SDK, asyncio.Queue for communication.
    """
    loop = asyncio.get_event_loop()
    audio_queue = asyncio.Queue()
    transcript_queue = asyncio.Queue()

    # Start Soniox worker in thread pool
    worker_future = loop.run_in_executor(
        _executor, _soniox_worker, audio_queue, transcript_queue, loop, lang
    )

    async def ws_reader():
        """Read from WebSocket, put on audio_queue."""
        try:
            async for msg in ws_client:
                if isinstance(msg, (bytes, bytearray)):
                    await audio_queue.put(msg)
                else:
                    try:
                        data = json.loads(msg)
                        msg_type = data.get("type", "")
                    except (json.JSONDecodeError, AttributeError):
                        continue

                    if msg_type == "CloseStream":
                        await audio_queue.put(_CLOSE_STREAM)
                    elif msg_type == "KeepAlive":
                        await audio_queue.put(_KEEP_ALIVE)
        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception:
            pass
        finally:
            # Ensure worker knows to stop
            await audio_queue.put(_DISCONNECT)

    async def ws_writer():
        """Read from transcript_queue, send to WebSocket."""
        try:
            while True:
                msg = await asyncio.wait_for(
                    transcript_queue.get(), timeout=FINALIZE_TIMEOUT + 5
                )
                if msg is None:
                    break
                await ws_client.send(msg)
        except asyncio.TimeoutError:
            log.warning("Transcript queue timeout")
        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception as e:
            log.error(f"WS writer error: {e}")

    try:
        await asyncio.gather(ws_reader(), ws_writer(), worker_future)
    except Exception as e:
        log.error(f"Soniox session error: {e}")


# ── Proxy session (native languages -> Anthropic) ────────────────────

async def proxy_session(ws_client, lang: str, token: str) -> bool:
    """Proxy WebSocket traffic between Claude Code and Anthropic's STT server.
    Returns True on success, False on failure (caller should fall back).
    """
    params = (
        f"encoding=linear16&sample_rate=16000&channels=1"
        f"&endpointing_ms=300&utterance_end_ms=1000&language={lang}"
    )
    url = f"{ANTHROPIC_WS}?{params}"
    headers = {
        "Authorization": f"Bearer {token}",
        "x-app": "cli",
    }

    log.info(f"Proxying to Anthropic ({lang})")

    try:
        async with ws_connect(url, additional_headers=headers, open_timeout=5) as upstream:
            async def client_to_upstream():
                try:
                    async for msg in ws_client:
                        await upstream.send(msg)
                except websockets.exceptions.ConnectionClosed:
                    pass

            async def upstream_to_client():
                try:
                    async for msg in upstream:
                        await ws_client.send(msg)
                except websockets.exceptions.ConnectionClosed:
                    pass

            await asyncio.gather(
                client_to_upstream(),
                upstream_to_client(),
                return_exceptions=True,
            )
            return True
    except Exception as e:
        log.error(f"Proxy error: {e}")
        return False


# ── Local session (Vosk batch fallback) ──────────────────────────────

async def local_session(ws_client, lang: str):
    """Buffer audio, transcribe with Vosk on CloseStream.
    Simplified batch mode (no streaming).
    """
    chunks: list[bytes] = []
    closed = False
    locale = lang_code(lang)

    log.info(f"Connected ({locale} -> Vosk STT)")

    try:
        async for msg in ws_client:
            if isinstance(msg, (bytes, bytearray)):
                if not closed:
                    chunks.append(bytes(msg))
            else:
                try:
                    data = json.loads(msg)
                    msg_type = data.get("type", "")
                except (json.JSONDecodeError, AttributeError):
                    continue

                if msg_type == "KeepAlive":
                    continue

                if msg_type == "CloseStream" and not closed:
                    closed = True
                    pcm = b"".join(chunks)
                    chunks.clear()
                    log.info(f"CloseStream ({len(pcm) / 32000.0:.1f}s audio)")

                    loop = asyncio.get_event_loop()
                    text = await loop.run_in_executor(
                        None, transcribe_vosk, pcm, locale
                    )

                    try:
                        if text:
                            await ws_client.send(json.dumps({
                                "type": "TranscriptText",
                                "data": text,
                            }))
                        await ws_client.send(json.dumps({"type": "TranscriptEndpoint"}))
                    except websockets.exceptions.ConnectionClosed:
                        log.warning("Client disconnected before transcription sent")
                        return
    except websockets.exceptions.ConnectionClosed:
        log.info("Client disconnected")


# ── Connection handler ───────────────────────────────────────────────

async def handle_connection(ws_client):
    """Handle a new WebSocket connection from Claude Code."""
    raw = read_language()
    code = lang_code(raw)

    # Primary: Soniox streaming (when API key is configured)
    soniox_key = read_soniox_api_key()
    if soniox_key:
        log.info(f"Connected ({code} -> Soniox streaming)")
        try:
            await soniox_session(ws_client, raw)
            return
        except Exception as e:
            log.error(f"Soniox session failed: {e}")
            log.info("Falling back to legacy STT")

    # Fallback: Anthropic proxy for native languages
    if is_native_language(raw):
        token = read_oauth_token()
        if token:
            log.info(f"Connected ({code} -> Anthropic proxy)")
            ok = await proxy_session(ws_client, code, token)
            if ok:
                return
            log.info("Proxy failed, falling back to local STT")

    # Final fallback: Vosk local STT
    await local_session(ws_client, raw)


# ── Main server ──────────────────────────────────────────────────────

async def main():
    soniox_key = read_soniox_api_key()
    if soniox_key:
        log.info(f"Voice server on ws://127.0.0.1:{PORT} (Soniox streaming)")
        log.info("All languages -> Soniox | Fallback -> Anthropic/Vosk")
    else:
        log.info(f"Voice server on ws://127.0.0.1:{PORT} (fallback mode)")
        log.info("Native languages -> Anthropic | Others -> Vosk")

    # Suppress noisy websockets library logs
    logging.getLogger("websockets").setLevel(logging.ERROR)

    async with ws_serve(handle_connection, "127.0.0.1", PORT):
        await asyncio.Future()  # Run forever


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Shutting down")
