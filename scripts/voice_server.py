#!/usr/bin/env python3
"""Voice server for Claude Code (Linux/Ubuntu).
Native languages → proxy to Anthropic's server.
Unsupported languages → Vosk on-device STT.
"""

import asyncio
import json
import os
import struct
import tempfile
import logging

try:
    import websockets
    import websockets.client
except ImportError:
    print("[voice] ERROR: 'websockets' package not found. Run: pip install websockets")
    raise SystemExit(1)

try:
    from vosk import Model, KaldiRecognizer
except ImportError:
    print("[voice] WARNING: 'vosk' package not found. Local STT will be unavailable.")
    Model = None
    KaldiRecognizer = None

PORT = 19876
ANTHROPIC_WS = "wss://api.anthropic.com/api/ws/speech_to_text/voice_stream"
NATIVE_LANGS = {
    "en", "es", "fr", "ja", "de", "pt", "it", "ko", "hi", "id",
    "ru", "pl", "tr", "nl", "uk", "el", "cs", "da", "sv", "no",
}

LOCALE_MAP = {
    "he": "he-IL", "hebrew": "he-IL", "עברית": "he-IL",
    "en": "en-US", "english": "en-US",
    "es": "es-ES", "spanish": "es-ES", "español": "es-ES",
    "fr": "fr-FR", "french": "fr-FR", "français": "fr-FR",
    "de": "de-DE", "german": "de-DE", "deutsch": "de-DE",
    "ja": "ja-JP", "japanese": "ja-JP", "日本語": "ja-JP",
    "ko": "ko-KR", "korean": "ko-KR", "한국어": "ko-KR",
    "pt": "pt-BR", "portuguese": "pt-BR", "português": "pt-BR",
    "it": "it-IT", "italian": "it-IT", "italiano": "it-IT",
    "ru": "ru-RU", "russian": "ru-RU", "русский": "ru-RU",
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

# Vosk model language mapping (model directory names)
VOSK_LANG_MAP = {
    "he": "he", "ar": "ar", "zh": "cn", "en": "en-us",
    "es": "es", "fr": "fr", "de": "de", "ja": "ja",
    "ko": "ko", "pt": "pt", "it": "it", "ru": "ru",
    "hi": "hi", "tr": "tr", "nl": "nl", "pl": "pl",
    "uk": "uk", "el": "el", "cs": "cs",
}

logging.basicConfig(
    level=logging.INFO,
    format="[voice] %(message)s",
)
log = logging.getLogger("voice")

INSTALL_DIR = os.path.join(os.path.expanduser("~"), ".local", "share", "claude-code-voice")
MODELS_DIR = os.path.join(INSTALL_DIR, "models")

# Cache loaded Vosk models
_vosk_models: dict[str, "Model"] = {}


# ── Language helpers ──────────────────────────────────────────────

def read_language() -> str:
    settings_path = os.path.join(os.path.expanduser("~"), ".claude", "settings.json")
    try:
        with open(settings_path) as f:
            data = json.load(f)
        lang = data.get("language", "en")
        return lang.lower().strip()
    except (OSError, json.JSONDecodeError, AttributeError):
        return "en"


def lang_code(raw: str) -> str:
    if raw in NATIVE_LANGS:
        return raw
    mapped = LOCALE_MAP.get(raw)
    if mapped:
        return mapped[:2]
    return raw


def is_native_language(raw: str) -> bool:
    return lang_code(raw) in NATIVE_LANGS


# ── OAuth token ───────────────────────────────────────────────────

def read_oauth_token() -> str | None:
    """Read OAuth token from Claude Code's credential storage on Linux."""
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

    # Path 2: credentials.json (desktop Linux)
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

    # Path 3: Try secret-tool (GNOME Keyring / libsecret)
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
    except (OSError, json.JSONDecodeError, subprocess.TimeoutExpired):
        pass

    return None


# ── WAV creation ──────────────────────────────────────────────────

def create_wav(pcm: bytes) -> bytes:
    """Create WAV header for 16-bit mono 16kHz PCM data."""
    data_size = len(pcm)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,      # file size - 8
        b"WAVE",
        b"fmt ",
        16,                  # fmt chunk size
        1,                   # PCM format
        1,                   # mono
        16000,               # sample rate
        32000,               # byte rate (16000 * 2)
        2,                   # block align (channels * bytes per sample)
        16,                  # bits per sample
        b"data",
        data_size,
    )
    return header + pcm


# ── Vosk STT ─────────────────────────────────────────────────────

def get_vosk_model(lang: str) -> "Model | None":
    """Load or retrieve cached Vosk model for the given language."""
    if Model is None:
        return None

    vosk_lang = VOSK_LANG_MAP.get(lang, lang)

    if vosk_lang in _vosk_models:
        return _vosk_models[vosk_lang]

    # Search for model directory
    if os.path.isdir(MODELS_DIR):
        for entry in os.listdir(MODELS_DIR):
            entry_lower = entry.lower()
            if vosk_lang in entry_lower and os.path.isdir(os.path.join(MODELS_DIR, entry)):
                model_path = os.path.join(MODELS_DIR, entry)
                log.info(f"Loading Vosk model: {entry}")
                model = Model(model_path)
                _vosk_models[vosk_lang] = model
                return model

    # Try default model (any model in models dir)
    if os.path.isdir(MODELS_DIR):
        for entry in os.listdir(MODELS_DIR):
            model_path = os.path.join(MODELS_DIR, entry)
            if os.path.isdir(model_path):
                log.info(f"Using fallback Vosk model: {entry}")
                model = Model(model_path)
                _vosk_models[vosk_lang] = model
                return model

    log.warning("No Vosk model found. Local STT unavailable.")
    return None


def transcribe_vosk(pcm: bytes, lang: str) -> str:
    """Transcribe PCM audio using Vosk."""
    if not pcm:
        return ""

    duration = len(pcm) / 32000.0
    log.info(f"{duration:.1f}s → Vosk STT ({lang})")

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


# ── Proxy session (native languages → Anthropic) ─────────────────

async def proxy_session(ws_client, lang: str, token: str):
    """Proxy WebSocket traffic between Claude Code and Anthropic's STT server."""
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
        async with websockets.client.connect(url, additional_headers=headers) as upstream:
            async def client_to_upstream():
                try:
                    async for msg in ws_client:
                        if isinstance(msg, (bytes, bytearray)):
                            await upstream.send(msg)
                        else:
                            await upstream.send(msg)
                except websockets.exceptions.ConnectionClosed:
                    pass

            async def upstream_to_client():
                try:
                    async for msg in upstream:
                        if isinstance(msg, (bytes, bytearray)):
                            await ws_client.send(msg)
                        else:
                            await ws_client.send(msg)
                except websockets.exceptions.ConnectionClosed:
                    pass

            await asyncio.gather(
                client_to_upstream(),
                upstream_to_client(),
                return_exceptions=True,
            )
    except Exception as e:
        log.error(f"Proxy error: {e}")


# ── Local session (unsupported languages → Vosk) ─────────────────

async def local_session(ws_client, lang: str):
    """Buffer audio chunks and transcribe locally via Vosk."""
    chunks: list[bytes] = []
    closed = False
    locale = lang_code(lang)

    log.info(f"Connected ({locale} → Vosk STT)")

    try:
        async for msg in ws_client:
            if isinstance(msg, (bytes, bytearray)):
                if not closed:
                    chunks.append(bytes(msg))
            else:
                # Text message
                try:
                    data = json.loads(msg)
                    msg_type = data.get("type", "")
                except (json.JSONDecodeError, AttributeError):
                    continue

                if msg_type == "KeepAlive":
                    continue

                if msg_type == "CloseStream" and not closed:
                    closed = True
                    # Send empty transcript to signal processing started
                    await ws_client.send(json.dumps({"type": "TranscriptText", "data": ""}))

                    # Combine chunks and transcribe
                    pcm = b"".join(chunks)
                    chunks.clear()

                    # Run Vosk in thread pool to avoid blocking
                    loop = asyncio.get_event_loop()
                    text = await loop.run_in_executor(None, transcribe_vosk, pcm, locale)

                    if text:
                        await ws_client.send(json.dumps({"type": "TranscriptText", "data": text}))
                    await ws_client.send(json.dumps({"type": "TranscriptEndpoint"}))
    except websockets.exceptions.ConnectionClosed:
        pass


# ── Connection handler ────────────────────────────────────────────

async def handle_connection(ws_client):
    """Handle a new WebSocket connection from Claude Code."""
    raw = read_language()
    code = lang_code(raw)

    if is_native_language(raw):
        token = read_oauth_token()
        if token:
            log.info(f"Connected ({code} → Anthropic)")
            await proxy_session(ws_client, code, token)
            return

    # Fall through to local STT
    await local_session(ws_client, raw)


# ── Main server ───────────────────────────────────────────────────

async def main():
    log.info(f"Voice server on ws://127.0.0.1:{PORT}")
    log.info("Native languages → Anthropic | Others → Vosk STT")

    async with websockets.serve(handle_connection, "127.0.0.1", PORT):
        await asyncio.Future()  # Run forever


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Shutting down")
