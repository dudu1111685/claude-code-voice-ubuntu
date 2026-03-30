#!/usr/bin/env python3
"""Local ivrit.ai transcription helper for macOS.

Called by the Swift voice server when local ivrit.ai engine is configured.
Usage: transcribe_ivrit_local.py <wav_path> [device] [compute_type] [model]
Prints transcribed text to stdout.
"""

import json
import os
import sys


def main():
    if len(sys.argv) < 2:
        print("Usage: transcribe_ivrit_local.py <wav_path> [device] [compute_type] [model]", file=sys.stderr)
        sys.exit(1)

    wav_path = sys.argv[1]
    device = sys.argv[2] if len(sys.argv) > 2 else "cpu"
    compute_type = sys.argv[3] if len(sys.argv) > 3 else ("float16" if "cuda" in device or device == "mps" else "float32")
    model_name = sys.argv[4] if len(sys.argv) > 4 else "ivrit-ai/faster-whisper-v2-d4"

    try:
        import ivrit
    except ImportError:
        print("ERROR: ivrit package not installed. Run: pip3 install 'ivrit[faster-whisper]'", file=sys.stderr)
        sys.exit(1)

    try:
        model = ivrit.load_model(
            engine="faster-whisper",
            model=model_name,
            device=device,
            compute_type=compute_type,
        )
        result = model.transcribe(path=wav_path, language="he")
        text = result.get("text", "").strip() if isinstance(result, dict) else str(result).strip()
        print(text)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
