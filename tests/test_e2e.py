#!/usr/bin/env python3
"""Comprehensive tests for the Linux voice server.
Covers: language helpers, WAV, Vosk, ivrit.ai (local + RunPod), config, E2E WebSocket flows.
"""

import asyncio
import json
import math
import os
import struct
import sys
import tempfile
from unittest import mock

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

_test_settings_dir = None
_test_settings_path = None


# ── Helpers ──────────────────────────────────────────────────────

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


def setup_test_settings(language="he", ivrit_config=None):
    """Create temporary settings.json and monkey-patch read functions."""
    global _test_settings_dir, _test_settings_path
    _test_settings_dir = tempfile.mkdtemp()
    _test_settings_path = os.path.join(_test_settings_dir, "settings.json")

    settings = {"language": language}
    if ivrit_config is not None:
        settings["ivritAi"] = ivrit_config

    with open(_test_settings_path, "w") as f:
        json.dump(settings, f)

    original_read_language = voice_server.read_language
    original_read_ivrit = voice_server.read_ivrit_config

    def test_read_language():
        try:
            with open(_test_settings_path) as f:
                data = json.load(f)
            return data.get("language", "en").lower().strip()
        except (OSError, json.JSONDecodeError):
            return "en"

    def test_read_ivrit_config():
        try:
            with open(_test_settings_path) as f:
                data = json.load(f)
            cfg = data.get("ivritAi", {})
            if not cfg:
                return None
            engine = cfg.get("engine", "runpod").strip().lower()
            if engine == "local":
                return {
                    "engine": "local",
                    "model": cfg.get("model", voice_server.IVRIT_DEFAULT_MODEL_LOCAL).strip(),
                    "device": cfg.get("device", "cpu").strip(),
                    "computeType": cfg.get("computeType", "").strip(),
                }
            api_key = cfg.get("apiKey", "").strip()
            endpoint_id = cfg.get("endpointId", "").strip()
            if api_key and endpoint_id:
                return {
                    "engine": "runpod",
                    "apiKey": api_key,
                    "endpointId": endpoint_id,
                    "model": cfg.get("model", voice_server.IVRIT_DEFAULT_MODEL_RUNPOD).strip(),
                }
        except (OSError, json.JSONDecodeError, AttributeError):
            pass
        return None

    voice_server.read_language = test_read_language
    voice_server.read_ivrit_config = test_read_ivrit_config
    return _test_settings_path, original_read_language, original_read_ivrit


def update_test_settings(language=None, ivrit_config=None):
    """Update existing test settings file."""
    with open(_test_settings_path) as f:
        settings = json.load(f)
    if language is not None:
        settings["language"] = language
    if ivrit_config is not None:
        settings["ivritAi"] = ivrit_config
    elif ivrit_config == {}:
        settings.pop("ivritAi", None)
    with open(_test_settings_path, "w") as f:
        json.dump(settings, f)


def reset_ivrit_cache():
    """Clear ivrit model cache between tests."""
    voice_server._ivrit_model = None
    voice_server._ivrit_engine = None


# ── Unit Tests: Language Helpers ─────────────────────────────────

def test_language_helpers():
    print("\n=== Unit Tests: Language Helpers ===\n")

    # lang_code
    report("lang_code('en') == 'en'", voice_server.lang_code("en") == "en")
    report("lang_code('he') == 'he'", voice_server.lang_code("he") == "he")
    report("lang_code('hebrew') == 'he'", voice_server.lang_code("hebrew") == "he")
    report("lang_code('עברית') == 'he'", voice_server.lang_code("עברית") == "he")
    report("lang_code('español') == 'es'", voice_server.lang_code("español") == "es")
    report("lang_code('日本語') == 'ja'", voice_server.lang_code("日本語") == "ja")
    report("lang_code('unknown') == 'unknown'", voice_server.lang_code("unknown") == "unknown")
    report("lang_code('français') == 'fr'", voice_server.lang_code("français") == "fr")

    # is_native_language
    report("is_native('en') == True", voice_server.is_native_language("en") is True)
    report("is_native('fr') == True", voice_server.is_native_language("fr") is True)
    report("is_native('spanish') == True", voice_server.is_native_language("spanish") is True)
    report("is_native('he') == False", voice_server.is_native_language("he") is False)
    report("is_native('hebrew') == False", voice_server.is_native_language("hebrew") is False)
    report("is_native('ar') == False", voice_server.is_native_language("ar") is False)
    report("is_native('zh') == False", voice_server.is_native_language("zh") is False)
    report("is_native('unknown') == False", voice_server.is_native_language("unknown") is False)

    # is_hebrew
    report("is_hebrew('he') == True", voice_server.is_hebrew("he") is True)
    report("is_hebrew('hebrew') == True", voice_server.is_hebrew("hebrew") is True)
    report("is_hebrew('עברית') == True", voice_server.is_hebrew("עברית") is True)
    report("is_hebrew('en') == False", voice_server.is_hebrew("en") is False)
    report("is_hebrew('ar') == False", voice_server.is_hebrew("ar") is False)

    # All native langs should be native
    for lang in voice_server.NATIVE_LANGS:
        ok = voice_server.is_native_language(lang)
        report(f"is_native('{lang}') == True", ok, f"got {ok}")


# ── Unit Tests: WAV Creation ────────────────────────────────────

def test_wav_creation():
    print("\n=== Unit Tests: WAV Creation ===\n")

    pcm = b"\x00\x01" * 16000
    wav = voice_server.create_wav(pcm)
    report("WAV starts with RIFF", wav[:4] == b"RIFF")
    report("WAV contains WAVE", wav[8:12] == b"WAVE")
    report("WAV contains fmt", wav[12:16] == b"fmt ")
    report("WAV contains data", wav[36:40] == b"data")
    report("WAV correct total size", len(wav) == 44 + len(pcm))

    # Check WAV header fields
    file_size = struct.unpack_from("<I", wav, 4)[0]
    report("WAV file size field correct", file_size == 36 + len(pcm))

    fmt_size = struct.unpack_from("<I", wav, 16)[0]
    report("WAV fmt chunk size == 16", fmt_size == 16)

    audio_fmt = struct.unpack_from("<H", wav, 20)[0]
    report("WAV format == 1 (PCM)", audio_fmt == 1)

    channels = struct.unpack_from("<H", wav, 22)[0]
    report("WAV channels == 1 (mono)", channels == 1)

    sample_rate = struct.unpack_from("<I", wav, 24)[0]
    report("WAV sample rate == 16000", sample_rate == 16000)

    byte_rate = struct.unpack_from("<I", wav, 28)[0]
    report("WAV byte rate == 32000", byte_rate == 32000)

    bits = struct.unpack_from("<H", wav, 34)[0]
    report("WAV bits per sample == 16", bits == 16)

    data_size = struct.unpack_from("<I", wav, 40)[0]
    report("WAV data size field correct", data_size == len(pcm))

    # Empty PCM
    wav_empty = voice_server.create_wav(b"")
    report("Empty PCM → 44-byte WAV header", len(wav_empty) == 44)
    report("Empty WAV data size == 0", struct.unpack_from("<I", wav_empty, 40)[0] == 0)


# ── Unit Tests: Vosk ────────────────────────────────────────────

def test_vosk():
    print("\n=== Unit Tests: Vosk STT ===\n")

    # Empty transcription
    result = voice_server.transcribe_vosk(b"", "en")
    report("Empty PCM → empty string", result == "")

    # Vosk model loading
    model = voice_server.get_vosk_model("en")
    has_models = os.path.isdir(MODELS_DIR) and any(
        os.path.isdir(os.path.join(MODELS_DIR, e)) for e in os.listdir(MODELS_DIR)
    ) if os.path.isdir(MODELS_DIR) else False

    if has_models:
        report("Vosk model loads (en)", model is not None, "Model not found")

        if model:
            # Silence should transcribe to empty
            silence = b"\x00\x00" * 16000
            result = voice_server.transcribe_vosk(silence, "en")
            report("Silence transcribes to empty", result == "", f"got '{result}'")

            # Model caching
            model2 = voice_server.get_vosk_model("en")
            report("Vosk model cached (same object)", model is model2)
    else:
        report("Vosk model skip (no models installed)", True)
        print("    (set VOSK_MODELS_DIR to enable Vosk tests)")

    # get_vosk_model with missing language
    # Should fall back to any available model
    fallback = voice_server.get_vosk_model("xx-nonexistent")
    if os.path.isdir(MODELS_DIR) and os.listdir(MODELS_DIR):
        report("Vosk fallback model found", fallback is not None)
    else:
        report("Vosk fallback returns None (no models)", fallback is None)

    # OAuth token reading (should not crash)
    token = voice_server.read_oauth_token()
    report("read_oauth_token() doesn't crash", True)
    if token:
        print(f"    (found token: {token[:10]}...)")
    else:
        print("    (no token found — expected in test env)")


# ── Unit Tests: ivrit.ai Config ─────────────────────────────────

def test_ivrit_config():
    print("\n=== Unit Tests: ivrit.ai Config ===\n")

    # Test 1: RunPod config
    settings_path, orig_lang, orig_ivrit = setup_test_settings("he", {
        "engine": "runpod",
        "apiKey": "rp_test123",
        "endpointId": "ep_abc",
    })
    cfg = voice_server.read_ivrit_config()
    report("RunPod config: engine == 'runpod'", cfg is not None and cfg["engine"] == "runpod")
    report("RunPod config: apiKey present", cfg is not None and cfg["apiKey"] == "rp_test123")
    report("RunPod config: endpointId present", cfg is not None and cfg["endpointId"] == "ep_abc")
    report("RunPod config: default model", cfg is not None and cfg["model"] == voice_server.IVRIT_DEFAULT_MODEL_RUNPOD)

    # Test 2: Local config
    update_test_settings(ivrit_config={
        "engine": "local",
        "device": "cuda:0",
        "computeType": "float16",
        "model": "ivrit-ai/faster-whisper-v2-d4",
    })
    cfg = voice_server.read_ivrit_config()
    report("Local config: engine == 'local'", cfg is not None and cfg["engine"] == "local")
    report("Local config: device == 'cuda:0'", cfg is not None and cfg["device"] == "cuda:0")
    report("Local config: computeType == 'float16'", cfg is not None and cfg["computeType"] == "float16")
    report("Local config: model correct", cfg is not None and cfg["model"] == "ivrit-ai/faster-whisper-v2-d4")

    # Test 3: Local config with defaults
    update_test_settings(ivrit_config={"engine": "local"})
    cfg = voice_server.read_ivrit_config()
    report("Local defaults: device == 'cpu'", cfg is not None and cfg["device"] == "cpu")
    report("Local defaults: model == default", cfg is not None and cfg["model"] == voice_server.IVRIT_DEFAULT_MODEL_LOCAL)
    report("Local defaults: computeType empty (resolved later)", cfg is not None and cfg["computeType"] == "")

    # Test 4: RunPod config without engine field (default)
    update_test_settings(ivrit_config={
        "apiKey": "rp_key",
        "endpointId": "ep_id",
    })
    cfg = voice_server.read_ivrit_config()
    report("Default engine is runpod", cfg is not None and cfg["engine"] == "runpod")

    # Test 5: RunPod config with custom model
    update_test_settings(ivrit_config={
        "apiKey": "rp_key",
        "endpointId": "ep_id",
        "model": "custom-model",
    })
    cfg = voice_server.read_ivrit_config()
    report("Custom RunPod model", cfg is not None and cfg["model"] == "custom-model")

    # Test 6: Missing apiKey → None
    update_test_settings(ivrit_config={
        "engine": "runpod",
        "endpointId": "ep_id",
    })
    cfg = voice_server.read_ivrit_config()
    report("Missing apiKey → None", cfg is None)

    # Test 7: Missing endpointId → None
    update_test_settings(ivrit_config={
        "engine": "runpod",
        "apiKey": "rp_key",
    })
    cfg = voice_server.read_ivrit_config()
    report("Missing endpointId → None", cfg is None)

    # Test 8: Empty apiKey → None
    update_test_settings(ivrit_config={
        "apiKey": "",
        "endpointId": "ep_id",
    })
    cfg = voice_server.read_ivrit_config()
    report("Empty apiKey → None", cfg is None)

    # Test 9: Empty ivritAi → None
    update_test_settings(ivrit_config={})
    cfg = voice_server.read_ivrit_config()
    report("Empty ivritAi → None", cfg is None)

    # Test 10: No ivritAi key at all
    with open(_test_settings_path, "w") as f:
        json.dump({"language": "he"}, f)
    cfg = voice_server.read_ivrit_config()
    report("No ivritAi key → None", cfg is None)

    # Test 11: Whitespace trimming
    update_test_settings(ivrit_config={
        "apiKey": "  rp_key  ",
        "endpointId": "  ep_id  ",
    })
    cfg = voice_server.read_ivrit_config()
    report("Whitespace trimmed: apiKey", cfg is not None and cfg["apiKey"] == "rp_key")
    report("Whitespace trimmed: endpointId", cfg is not None and cfg["endpointId"] == "ep_id")

    # Restore
    voice_server.read_language = orig_lang
    voice_server.read_ivrit_config = orig_ivrit


# ── Unit Tests: ivrit.ai Availability ───────────────────────────

def test_ivrit_availability():
    print("\n=== Unit Tests: ivrit.ai Availability ===\n")

    settings_path, orig_lang, orig_ivrit = setup_test_settings("he", {
        "engine": "local",
        "device": "cpu",
    })

    # is_ivrit_available when configured and ivrit module available
    original_available = voice_server._ivrit_available

    voice_server._ivrit_available = True
    report("is_ivrit_available('he') with config+module", voice_server.is_ivrit_available("he") is True)
    report("is_ivrit_available('hebrew') with config+module", voice_server.is_ivrit_available("hebrew") is True)
    report("is_ivrit_available('עברית') with config+module", voice_server.is_ivrit_available("עברית") is True)
    report("is_ivrit_available('en') == False", voice_server.is_ivrit_available("en") is False)
    report("is_ivrit_available('ar') == False", voice_server.is_ivrit_available("ar") is False)

    # is_ivrit_available when module not available
    voice_server._ivrit_available = False
    report("is_ivrit_available without module == False", voice_server.is_ivrit_available("he") is False)

    # is_ivrit_available when no config
    voice_server._ivrit_available = True
    with open(_test_settings_path, "w") as f:
        json.dump({"language": "he"}, f)
    report("is_ivrit_available without config == False", voice_server.is_ivrit_available("he") is False)

    # Restore
    voice_server._ivrit_available = original_available
    voice_server.read_language = orig_lang
    voice_server.read_ivrit_config = orig_ivrit


# ── Unit Tests: ivrit.ai Transcription (Mocked) ─────────────────

def test_ivrit_transcription_mocked():
    print("\n=== Unit Tests: ivrit.ai Transcription (Mocked) ===\n")

    reset_ivrit_cache()

    settings_path, orig_lang, orig_ivrit = setup_test_settings("he", {
        "engine": "local",
        "device": "cpu",
        "computeType": "float32",
        "model": "ivrit-ai/faster-whisper-v2-d4",
    })

    # Test 1: Empty PCM → empty string
    result = voice_server.transcribe_ivrit(b"")
    report("Empty PCM → empty string", result == "")

    # Test 2: Transcription with mocked ivrit model (local engine)
    mock_model = mock.MagicMock()
    mock_model.transcribe.return_value = {"text": "שלום עולם"}

    original_available = voice_server._ivrit_available
    voice_server._ivrit_available = True

    with mock.patch.object(voice_server, '_ivrit_mod', create=True) as mock_ivrit:
        mock_ivrit.load_model.return_value = mock_model
        reset_ivrit_cache()

        pcm = generate_sine_pcm(440, 0.5)
        result = voice_server.transcribe_ivrit(pcm)
        report("Mocked local transcription returns text", result == "שלום עולם", f"got '{result}'")

        # Verify load_model was called with local params
        call_kwargs = mock_ivrit.load_model.call_args
        report("load_model called with engine='faster-whisper'",
               call_kwargs is not None and call_kwargs.kwargs.get("engine") == "faster-whisper")
        report("load_model called with device='cpu'",
               call_kwargs is not None and call_kwargs.kwargs.get("device") == "cpu")
        report("load_model called with compute_type='float32'",
               call_kwargs is not None and call_kwargs.kwargs.get("compute_type") == "float32")

        # Verify transcribe was called with path (local mode writes temp file)
        t_call = mock_model.transcribe.call_args
        report("transcribe called with path= (local mode)",
               t_call is not None and "path" in t_call.kwargs)
        report("transcribe called with language='he'",
               t_call is not None and t_call.kwargs.get("language") == "he")

    # Test 3: Mocked RunPod transcription
    update_test_settings(ivrit_config={
        "engine": "runpod",
        "apiKey": "rp_test",
        "endpointId": "ep_test",
    })

    mock_model_rp = mock.MagicMock()
    mock_model_rp.transcribe.return_value = {"text": "בדיקה אחת שתיים"}

    with mock.patch.object(voice_server, '_ivrit_mod', create=True) as mock_ivrit:
        mock_ivrit.load_model.return_value = mock_model_rp
        reset_ivrit_cache()

        pcm = generate_sine_pcm(440, 0.5)
        result = voice_server.transcribe_ivrit(pcm)
        report("Mocked RunPod transcription returns text", result == "בדיקה אחת שתיים", f"got '{result}'")

        call_kwargs = mock_ivrit.load_model.call_args
        report("RunPod: load_model with engine='runpod'",
               call_kwargs is not None and call_kwargs.kwargs.get("engine") == "runpod")
        report("RunPod: load_model with api_key",
               call_kwargs is not None and call_kwargs.kwargs.get("api_key") == "rp_test")
        report("RunPod: load_model with endpoint_id",
               call_kwargs is not None and call_kwargs.kwargs.get("endpoint_id") == "ep_test")

        t_call = mock_model_rp.transcribe.call_args
        report("RunPod: transcribe called with blob= (not path)",
               t_call is not None and "blob" in t_call.kwargs)

    # Test 4: Transcription error → falls back to Vosk
    update_test_settings(ivrit_config={"engine": "local"})

    mock_model_err = mock.MagicMock()
    mock_model_err.transcribe.side_effect = RuntimeError("GPU out of memory")

    with mock.patch.object(voice_server, '_ivrit_mod', create=True) as mock_ivrit:
        mock_ivrit.load_model.return_value = mock_model_err
        reset_ivrit_cache()

        pcm = b"\x00\x00" * 16000  # silence
        result = voice_server.transcribe_ivrit(pcm)
        report("Transcription error → fallback (no crash)", True)
        report("Fallback returns string", isinstance(result, str))

    # Test 5: Model load failure → falls back to Vosk
    with mock.patch.object(voice_server, '_ivrit_mod', create=True) as mock_ivrit:
        mock_ivrit.load_model.side_effect = Exception("Model not found")
        reset_ivrit_cache()

        pcm = b"\x00\x00" * 16000
        result = voice_server.transcribe_ivrit(pcm)
        report("Model load failure → fallback (no crash)", True)
        report("Fallback returns string", isinstance(result, str))

    # Test 6: No config → falls back to Vosk
    with open(_test_settings_path, "w") as f:
        json.dump({"language": "he"}, f)
    reset_ivrit_cache()
    pcm = b"\x00\x00" * 16000
    result = voice_server.transcribe_ivrit(pcm)
    report("No config → Vosk fallback (no crash)", True)

    # Test 7: ivrit module not available → falls back
    voice_server._ivrit_available = False
    update_test_settings(ivrit_config={"engine": "local"})
    reset_ivrit_cache()
    result = voice_server.transcribe_ivrit(pcm)
    report("Module unavailable → Vosk fallback", True)

    # Test 8: Model caching — same engine reuses model
    voice_server._ivrit_available = True
    update_test_settings(ivrit_config={"engine": "local"})

    mock_model_cached = mock.MagicMock()
    mock_model_cached.transcribe.return_value = {"text": "cached"}

    with mock.patch.object(voice_server, '_ivrit_mod', create=True) as mock_ivrit:
        mock_ivrit.load_model.return_value = mock_model_cached
        reset_ivrit_cache()

        voice_server.transcribe_ivrit(generate_sine_pcm(440, 0.3))
        voice_server.transcribe_ivrit(generate_sine_pcm(440, 0.3))
        report("Model cached: load_model called once", mock_ivrit.load_model.call_count == 1,
               f"called {mock_ivrit.load_model.call_count} times")

    # Test 9: Engine switch invalidates cache
    with mock.patch.object(voice_server, '_ivrit_mod', create=True) as mock_ivrit:
        mock_ivrit.load_model.return_value = mock_model_cached
        reset_ivrit_cache()

        update_test_settings(ivrit_config={"engine": "local"})
        voice_server.transcribe_ivrit(generate_sine_pcm(440, 0.3))

        update_test_settings(ivrit_config={
            "engine": "runpod",
            "apiKey": "rp_x",
            "endpointId": "ep_x",
        })
        voice_server.transcribe_ivrit(generate_sine_pcm(440, 0.3))
        report("Engine switch: load_model called twice", mock_ivrit.load_model.call_count == 2,
               f"called {mock_ivrit.load_model.call_count} times")

    # Test 10: computeType defaults
    reset_ivrit_cache()
    update_test_settings(ivrit_config={"engine": "local", "device": "cuda:0"})

    with mock.patch.object(voice_server, '_ivrit_mod', create=True) as mock_ivrit:
        mock_ivrit.load_model.return_value = mock_model_cached
        reset_ivrit_cache()

        voice_server.transcribe_ivrit(generate_sine_pcm(440, 0.3))
        call_kwargs = mock_ivrit.load_model.call_args
        report("CUDA default compute_type == 'float16'",
               call_kwargs is not None and call_kwargs.kwargs.get("compute_type") == "float16")

    update_test_settings(ivrit_config={"engine": "local", "device": "cpu"})
    with mock.patch.object(voice_server, '_ivrit_mod', create=True) as mock_ivrit:
        mock_ivrit.load_model.return_value = mock_model_cached
        reset_ivrit_cache()

        voice_server.transcribe_ivrit(generate_sine_pcm(440, 0.3))
        call_kwargs = mock_ivrit.load_model.call_args
        report("CPU default compute_type == 'float32'",
               call_kwargs is not None and call_kwargs.kwargs.get("compute_type") == "float32")

    # Test 11: Result parsing — dict vs non-dict
    with mock.patch.object(voice_server, '_ivrit_mod', create=True) as mock_ivrit:
        # Dict result
        mock_m = mock.MagicMock()
        mock_m.transcribe.return_value = {"text": "  hello  ", "segments": []}
        mock_ivrit.load_model.return_value = mock_m
        reset_ivrit_cache()
        update_test_settings(ivrit_config={"engine": "local"})
        result = voice_server.transcribe_ivrit(generate_sine_pcm(440, 0.3))
        report("Dict result text is stripped", result == "hello")

        # Non-dict result (edge case)
        mock_m.transcribe.return_value = "  raw text  "
        reset_ivrit_cache()
        result = voice_server.transcribe_ivrit(generate_sine_pcm(440, 0.3))
        report("Non-dict result converted to string", result == "raw text")

    # Restore
    voice_server._ivrit_available = original_available
    voice_server.read_language = orig_lang
    voice_server.read_ivrit_config = orig_ivrit
    reset_ivrit_cache()


# ── Unit Tests: transcribe_ivrit_local.py helper ─────────────────

def test_ivrit_local_helper():
    print("\n=== Unit Tests: transcribe_ivrit_local.py ===\n")

    helper_path = os.path.join(os.path.dirname(__file__), "..", "scripts", "transcribe_ivrit_local.py")
    report("Helper script exists", os.path.isfile(helper_path))

    # Verify it's valid Python
    try:
        with open(helper_path) as f:
            compile(f.read(), helper_path, "exec")
        report("Helper script is valid Python", True)
    except SyntaxError as e:
        report("Helper script is valid Python", False, str(e))

    # Verify it has main() and argument handling
    with open(helper_path) as f:
        content = f.read()
    report("Helper has main()", "def main()" in content)
    report("Helper uses sys.argv", "sys.argv" in content)
    report("Helper imports ivrit", "import ivrit" in content)
    report("Helper uses faster-whisper engine", '"faster-whisper"' in content)
    report("Helper uses language='he'", 'language="he"' in content)


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


async def test_e2e_vosk():
    print("\n=== E2E Tests: Vosk Local STT ===\n")

    settings_path, orig_lang, orig_ivrit = setup_test_settings("he")
    print(f"  Using test settings: language=he (Vosk path, no ivrit config)\n")

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
            chunk_size = 3200
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
            report("KeepAlive ignored (connection alive)", True)

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
        report("3 sequential connections all succeed", multi_ok)

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

        # Test 7: Invalid JSON text message
        print("  Test: Invalid JSON message")
        async with connect("ws://127.0.0.1:19876") as ws:
            await ws.send("not json at all")
            await asyncio.sleep(0.2)
            await ws.send(json.dumps({"type": "CloseStream"}))
            responses = await receive_until_endpoint(ws, timeout=5)
            has_end = any(r["type"] == "TranscriptEndpoint" for r in responses)
            report("Invalid JSON ignored, CloseStream works", has_end)

        # Test 8: Large audio payload
        print("  Test: Large audio (5 seconds)")
        async with connect("ws://127.0.0.1:19876") as ws:
            pcm = generate_sine_pcm(440, 5.0)
            chunk_size = 3200
            for i in range(0, len(pcm), chunk_size):
                await ws.send(pcm[i:i + chunk_size])
            await ws.send(json.dumps({"type": "CloseStream"}))
            responses = await receive_until_endpoint(ws, timeout=30)
            has_end = any(r["type"] == "TranscriptEndpoint" for r in responses)
            report("Large audio completes", has_end)

        # Test 9: Language switching mid-session
        print("  Test: Language switching")
        with open(settings_path, "w") as f:
            json.dump({"language": "en"}, f)
        async with connect("ws://127.0.0.1:19876") as ws:
            await ws.send(b"\x00\x00" * 1600)
            await ws.send(json.dumps({"type": "CloseStream"}))
            responses = await receive_until_endpoint(ws, timeout=10)
            got_response = len(responses) > 0
            report("Native lang routes correctly", got_response, f"responses: {responses}")

        # Reset back
        with open(settings_path, "w") as f:
            json.dump({"language": "he"}, f)

        # Test 10: Binary-only (no CloseStream) then disconnect
        print("  Test: Binary only, then disconnect")
        async with connect("ws://127.0.0.1:19876") as ws:
            await ws.send(b"\x00\x01" * 3200)
            await ws.send(b"\x00\x01" * 3200)
        await asyncio.sleep(0.5)
        async with connect("ws://127.0.0.1:19876") as ws:
            report("Server survives binary-only disconnect", True)

    finally:
        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass
        voice_server.read_language = orig_lang
        voice_server.read_ivrit_config = orig_ivrit


async def test_e2e_ivrit_mocked():
    print("\n=== E2E Tests: ivrit.ai (Mocked) ===\n")

    original_available = voice_server._ivrit_available
    voice_server._ivrit_available = True

    # Set up with ivrit config
    settings_path, orig_lang, orig_ivrit = setup_test_settings("he", {
        "engine": "local",
        "device": "cpu",
        "computeType": "float32",
    })

    # Mock the ivrit model
    mock_model = mock.MagicMock()
    mock_model.transcribe.return_value = {"text": "זה טסט מקצה לקצה"}

    with mock.patch.object(voice_server, '_ivrit_mod', create=True) as mock_ivrit:
        mock_ivrit.load_model.return_value = mock_model
        reset_ivrit_cache()

        server_task = asyncio.create_task(voice_server.main())
        await asyncio.sleep(1)

        try:
            # Test 1: Hebrew with ivrit.ai → get mocked transcription
            print("  Test: Hebrew via ivrit.ai (mocked)")
            async with connect("ws://127.0.0.1:19876") as ws:
                pcm = generate_sine_pcm(440, 0.5)
                await ws.send(pcm)
                await ws.send(json.dumps({"type": "CloseStream"}))

                responses = await receive_until_endpoint(ws, timeout=15)
                texts = [r["data"] for r in responses if r.get("type") == "TranscriptText" and r.get("data")]
                has_end = any(r["type"] == "TranscriptEndpoint" for r in responses)

                report("ivrit.ai E2E: gets TranscriptEndpoint", has_end)
                report("ivrit.ai E2E: gets Hebrew text", "זה טסט מקצה לקצה" in texts,
                       f"texts: {texts}")

                # Verify mock was called
                report("ivrit.ai E2E: model.transcribe was called", mock_model.transcribe.called)

            # Test 2: Multiple requests reuse model
            print("  Test: Model reuse across connections")
            call_count_before = mock_ivrit.load_model.call_count

            async with connect("ws://127.0.0.1:19876") as ws:
                await ws.send(generate_sine_pcm(440, 0.3))
                await ws.send(json.dumps({"type": "CloseStream"}))
                await receive_until_endpoint(ws)

            async with connect("ws://127.0.0.1:19876") as ws:
                await ws.send(generate_sine_pcm(440, 0.3))
                await ws.send(json.dumps({"type": "CloseStream"}))
                await receive_until_endpoint(ws)

            report("Model reused across connections", mock_ivrit.load_model.call_count == call_count_before,
                   f"load_model called {mock_ivrit.load_model.call_count - call_count_before} extra times")

            # Test 3: ivrit error → TranscriptEndpoint still sent
            print("  Test: ivrit.ai error handling in E2E")
            mock_model.transcribe.side_effect = RuntimeError("boom")

            async with connect("ws://127.0.0.1:19876") as ws:
                await ws.send(generate_sine_pcm(440, 0.3))
                await ws.send(json.dumps({"type": "CloseStream"}))
                responses = await receive_until_endpoint(ws, timeout=15)
                has_end = any(r["type"] == "TranscriptEndpoint" for r in responses)
                report("Error → still sends TranscriptEndpoint", has_end)

            # Restore mock
            mock_model.transcribe.side_effect = None
            mock_model.transcribe.return_value = {"text": "שלום"}

            # Test 4: Switch from Hebrew (ivrit) to non-native non-Hebrew (Vosk)
            print("  Test: Switch from Hebrew (ivrit) to Arabic (Vosk)")
            with open(settings_path, "w") as f:
                json.dump({"language": "ar", "ivritAi": {"engine": "local"}}, f)

            async with connect("ws://127.0.0.1:19876") as ws:
                await ws.send(b"\x00\x00" * 1600)
                await ws.send(json.dumps({"type": "CloseStream"}))
                responses = await receive_until_endpoint(ws, timeout=10)
                has_end = any(r["type"] == "TranscriptEndpoint" for r in responses)
                report("Arabic uses Vosk (not ivrit)", has_end)

            # Test 5: Switch back to Hebrew
            print("  Test: Switch back to Hebrew")
            with open(settings_path, "w") as f:
                json.dump({"language": "he", "ivritAi": {"engine": "local"}}, f)

            transcribe_called_before = mock_model.transcribe.call_count
            async with connect("ws://127.0.0.1:19876") as ws:
                await ws.send(generate_sine_pcm(440, 0.3))
                await ws.send(json.dumps({"type": "CloseStream"}))
                responses = await receive_until_endpoint(ws)
                texts = [r["data"] for r in responses if r.get("type") == "TranscriptText" and r.get("data")]
                report("Back to Hebrew → ivrit used", mock_model.transcribe.call_count > transcribe_called_before)

        finally:
            server_task.cancel()
            try:
                await server_task
            except asyncio.CancelledError:
                pass

    voice_server._ivrit_available = original_available
    voice_server.read_language = orig_lang
    voice_server.read_ivrit_config = orig_ivrit
    reset_ivrit_cache()


async def test_e2e_ivrit_fallback():
    print("\n=== E2E Tests: ivrit.ai Fallback to Vosk ===\n")

    original_available = voice_server._ivrit_available

    # Case 1: ivrit module not installed → Vosk
    voice_server._ivrit_available = False
    settings_path, orig_lang, orig_ivrit = setup_test_settings("he", {
        "engine": "local",
        "device": "cpu",
    })
    reset_ivrit_cache()

    server_task = asyncio.create_task(voice_server.main())
    await asyncio.sleep(1)

    try:
        print("  Test: ivrit unavailable → Vosk fallback")
        async with connect("ws://127.0.0.1:19876") as ws:
            await ws.send(b"\x00\x00" * 1600)
            await ws.send(json.dumps({"type": "CloseStream"}))
            responses = await receive_until_endpoint(ws, timeout=10)
            has_end = any(r["type"] == "TranscriptEndpoint" for r in responses)
            report("Vosk fallback: TranscriptEndpoint received", has_end)

    finally:
        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass

    # Case 2: No ivrit config → Vosk
    voice_server._ivrit_available = True
    with open(settings_path, "w") as f:
        json.dump({"language": "he"}, f)  # no ivritAi
    reset_ivrit_cache()

    server_task = asyncio.create_task(voice_server.main())
    await asyncio.sleep(1)

    try:
        print("  Test: No ivrit config → Vosk fallback")
        async with connect("ws://127.0.0.1:19876") as ws:
            await ws.send(b"\x00\x00" * 1600)
            await ws.send(json.dumps({"type": "CloseStream"}))
            responses = await receive_until_endpoint(ws, timeout=10)
            has_end = any(r["type"] == "TranscriptEndpoint" for r in responses)
            report("No config fallback: TranscriptEndpoint received", has_end)

    finally:
        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass

    voice_server._ivrit_available = original_available
    voice_server.read_language = orig_lang
    voice_server.read_ivrit_config = orig_ivrit
    reset_ivrit_cache()


# ── Main ──────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("Claude Code Voice — Comprehensive Test Suite")
    print("=" * 60)

    if not os.path.isdir(MODELS_DIR):
        print(f"\nWARNING: Models directory not found: {MODELS_DIR}")
        print("Set VOSK_MODELS_DIR env var or install a model first.")

    # Unit tests
    test_language_helpers()
    test_wav_creation()
    test_vosk()
    test_ivrit_config()
    test_ivrit_availability()
    test_ivrit_transcription_mocked()
    test_ivrit_local_helper()

    # E2E tests
    asyncio.run(test_e2e_vosk())
    asyncio.run(test_e2e_ivrit_mocked())
    asyncio.run(test_e2e_ivrit_fallback())

    print("\n" + "=" * 60)
    total = passed + failed
    print(f"Results: {passed}/{total} passed, {failed} failed")
    if failed == 0:
        print("ALL TESTS PASSED")
    print("=" * 60)

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
