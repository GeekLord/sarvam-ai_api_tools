from __future__ import annotations

"""
Sarvam AI Standalone Text-to-Speech (TTS) Synthesizer
=====================================================
A robust, portable CLI tool that converts text files (and literal
strings) into natural-sounding speech across Indic languages and English
using the Sarvam AI Text-to-Speech API (model ``bulbul:v3`` by default).

Features:
- Completely portable: operates in the current working directory or any
  specified path; a single positional argument selects a file or folder.
- Batch synthesis: discovers ``.txt`` / ``.md`` files in a directory
  (non-recursive) or synthesizes a single file or literal ``--text``.
- Automatic long-text chunking: inputs above the model character limit are
  split on paragraph/sentence boundaries and the resulting audio bytes are
  concatenated into one output file per unit, so nothing is truncated.
- Selectable speaker personas per model with model-vs-speaker validation.
- Model-aware options: ``temperature`` for bulbul:v3; ``pitch`` /
  ``loudness`` / ``enable_preprocessing`` for bulbul:v2 (validated and
  skipped with a warning when used against the wrong model).
- Automatic rate-limit management: polite cooldowns, exponential backoff
  with jitter, and 429 detection; continues past per-item failures.
- Graceful shutdown: SIGINT/SIGTERM flushes the run manifest before exit.
- Non-destructive: never overwrites input files; writes audio plus
  metadata into a dedicated ``sarvam_tts_audio`` folder.
- Multi-format export: per-unit audio file and ``.json`` metadata plus an
  aggregate JSON manifest, CSV spreadsheet, and Markdown catalog.
"""

import argparse
import base64
import csv
import json
import os
import random
import re
import signal
import sys
import time
from pathlib import Path
from typing import Any

# Optional python-dotenv
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

# Sarvam AI SDK
try:
    from sarvamai import SarvamAI

    HAS_SARVAM = True
except ImportError:
    HAS_SARVAM = False


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------
DEFAULT_MODEL = "bulbul:v3"
MODELS = ("bulbul:v2", "bulbul:v3")

SPEAKERS_V3 = (
    "shubh",
    "aditya",
    "ritu",
    "priya",
    "neha",
    "rahul",
    "pooja",
    "rohan",
    "simran",
    "kavya",
    "amit",
    "dev",
    "ishita",
    "shreya",
    "ratan",
    "varun",
    "manan",
    "sumit",
    "roopa",
    "kabir",
    "aayan",
    "ashutosh",
    "advait",
    "anand",
    "tanya",
    "tarun",
    "sunny",
    "mani",
    "gokul",
    "vijay",
    "shruti",
    "suhani",
    "mohit",
    "kavitha",
    "rehan",
    "soham",
    "rupali",
)

SPEAKERS_V2 = (
    "anushka",
    "manisha",
    "vidya",
    "arya",
    "abhilash",
    "karun",
    "hitesh",
)

DEFAULT_SPEAKER_V3 = "shubh"
DEFAULT_SPEAKER_V2 = "anushka"

# BCP-47 language codes supported by the Sarvam TTS (bulbul) models.
SUPPORTED_LANGUAGES = {
    "bn-IN": "Bengali",
    "en-IN": "English",
    "gu-IN": "Gujarati",
    "hi-IN": "Hindi",
    "kn-IN": "Kannada",
    "ml-IN": "Malayalam",
    "mr-IN": "Marathi",
    "od-IN": "Odia",
    "pa-IN": "Punjabi",
    "ta-IN": "Tamil",
    "te-IN": "Telugu",
}

CHAR_LIMIT_V3 = 2500
CHAR_LIMIT_V2 = 1500

SUPPORTED_SAMPLE_RATES = (8000, 16000, 22050, 24000, 32000, 44100, 48000)

# Pace ranges are model-dependent.
PACE_RANGE_V3 = (0.5, 2.0)
PACE_RANGE_V2 = (0.3, 3.0)

CODEC_CHOICES = ("wav", "mp3", "flac", "aac", "opus", "linear16", "mulaw", "alaw")

# Map audio codec to output file extension.
CODEC_EXTENSIONS = {
    "wav": ".wav",
    "mp3": ".mp3",
    "flac": ".flac",
    "aac": ".aac",
    "opus": ".opus",
    "linear16": ".wav",
    "mulaw": ".wav",
    "alaw": ".wav",
}

TEXT_EXTENSIONS = {".txt", ".md"}

DEFAULT_DELAY = 1.0
DEFAULT_MAX_RETRIES = 5
INITIAL_BACKOFF = 5.0

DEFAULT_CODEC = "wav"
DEFAULT_SAMPLE_RATE = 22050

OUTPUT_DIR = Path("sarvam_tts_audio")


class TTSState:
    """Manages the run manifest and graceful exit on interrupt."""

    def __init__(self) -> None:
        self.interrupted = False
        self.records: list[dict[str, Any]] = []
        self.manifest_path: str | None = None

    def setup_signals(self) -> None:
        signal.signal(signal.SIGINT, self._handle_interrupt)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, self._handle_interrupt)

    def _handle_interrupt(self, sig: int, frame: Any) -> None:
        print("\n\n[!] Interrupt received. Flushing manifest and shutting down...")
        self.interrupted = True
        self.flush_manifest()
        sys.exit(130)

    def flush_manifest(self) -> None:
        if self.manifest_path and self.records:
            try:
                temp_file = f"{self.manifest_path}.tmp"
                with open(temp_file, "w", encoding="utf-8") as f:
                    json.dump(self.records, f, indent=2, ensure_ascii=False)
                if os.path.exists(temp_file):
                    os.replace(temp_file, self.manifest_path)
            except Exception as e:
                print(f"[!] Warning: Could not flush manifest: {e}")


STATE = TTSState()


def get_api_key(
    cli_key: str | None = None, target_dir: Path | None = None
) -> str | None:
    """
    Resolve Sarvam AI API subscription key with prioritized fallback:
    1. Explicit CLI argument (--api-key)
    2. Environment variable (SARVAM_API_KEY)
    3. .env file in target directory, current directory, or script directory
    """
    if cli_key and cli_key.strip():
        return cli_key.strip()

    env_key = os.environ.get("SARVAM_API_KEY", "").strip()
    if env_key:
        return env_key

    # Check target_dir, current working directory, and script directory for .env
    candidates: list[Path] = []
    if target_dir:
        candidates.append(Path(target_dir))
    candidates.append(Path.cwd())
    candidates.append(Path(__file__).resolve().parent)

    for candidate in candidates:
        env_file = candidate / ".env"
        if env_file.is_file():
            try:
                with open(env_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("#") or not line:
                            continue
                        if line.startswith("SARVAM_API_KEY="):
                            key = line.split("=", 1)[1].strip().strip('"').strip("'")
                            if key:
                                return key
            except Exception:
                pass

    return None


def char_limit_for_model(model: str) -> int:
    """Return the per-request input character limit for the given model."""
    if model == "bulbul:v2":
        return CHAR_LIMIT_V2
    return CHAR_LIMIT_V3


def speakers_for_model(model: str) -> tuple[str, ...]:
    """Return the valid speaker tuple for the given model."""
    if model == "bulbul:v2":
        return SPEAKERS_V2
    return SPEAKERS_V3


def default_speaker_for_model(model: str) -> str:
    """Return the default speaker for the given model."""
    if model == "bulbul:v2":
        return DEFAULT_SPEAKER_V2
    return DEFAULT_SPEAKER_V3


def pace_range_for_model(model: str) -> tuple[float, float]:
    """Return the valid pace range for the given model."""
    if model == "bulbul:v2":
        return PACE_RANGE_V2
    return PACE_RANGE_V3


def find_text_files(folder: Path) -> list[Path]:
    """Discover synthesizable text files in a folder (non-recursive, sorted)."""
    return sorted(
        [
            p
            for p in folder.iterdir()
            if p.is_file() and p.suffix.lower() in TEXT_EXTENSIONS
        ],
        key=lambda p: p.name.lower(),
    )


def split_text_into_chunks(text: str, char_limit: int) -> list[str]:
    """
    Split text into chunks that each fit within ``char_limit`` characters.

    Prefers paragraph boundaries, then sentence boundaries, and finally a
    hard character split as a last resort so no input is ever truncated.
    """
    text = text.strip()
    if len(text) <= char_limit:
        return [text] if text else []

    chunks: list[str] = []
    current = ""

    paragraphs = re.split(r"\n\s*\n", text)
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) <= char_limit:
            current = candidate
            continue

        if current:
            chunks.append(current)
            current = ""

        if len(para) <= char_limit:
            current = para
            continue

        # Paragraph itself is too big; split on sentence boundaries.
        for sentence in _split_sentences(para, char_limit):
            candidate = f"{current} {sentence}".strip() if current else sentence
            if len(candidate) <= char_limit:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                current = sentence

    if current:
        chunks.append(current)

    return chunks


def _split_sentences(paragraph: str, char_limit: int) -> list[str]:
    """Break a long paragraph into sentence-sized pieces within the limit."""
    pieces: list[str] = []
    sentences = re.split(r"(?<=[.!?।॥])\s+", paragraph)
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) <= char_limit:
            pieces.append(sentence)
        else:
            # Hard split as a final fallback.
            for i in range(0, len(sentence), char_limit):
                pieces.append(sentence[i : i + char_limit])
    return pieces


def extract_audio_b64(response: Any) -> list[str]:
    """
    Read the base64-encoded audio strings from a Sarvam TTS response,
    defensively across SDK versions.

    Expected shape: ``TextToSpeechResponse.audios`` -> list[str] of
    base64-encoded audio; a dict payload uses the ``audios`` key.
    """
    audios = getattr(response, "audios", None)

    if audios is None and isinstance(response, dict):
        audios = response.get("audios")

    # Some SDK versions expose a model_dump()/dict() serialization.
    if audios is None:
        for attr in ("model_dump", "dict"):
            method = getattr(response, attr, None)
            if callable(method):
                try:
                    dumped = method()
                    if isinstance(dumped, dict):
                        audios = dumped.get("audios")
                        if audios is not None:
                            break
                except Exception:
                    pass

    if audios is None:
        return []
    if isinstance(audios, str):
        return [audios]
    return [str(a) for a in audios]


def extract_request_id(response: Any) -> str | None:
    """Read the request_id from a Sarvam TTS response, defensively."""
    request_id = getattr(response, "request_id", None)
    if request_id is None and isinstance(response, dict):
        request_id = response.get("request_id")
    return str(request_id) if request_id is not None else None


def synthesize_chunk(
    client: SarvamAI,
    chunk: str,
    args: argparse.Namespace,
    label: str,
    max_retries: int,
    initial_backoff: float,
) -> Any:
    """
    Synthesize a single chunk with 429-aware exponential backoff.

    Returns the raw Sarvam response. Raises on unrecoverable errors so the
    caller can count the failure and continue with the next unit.
    """
    kwargs: dict[str, Any] = {
        "text": chunk,
        "language_code": args.language,
        "speaker": args.speaker,
        "model": args.model,
        "speech_sample_rate": args.sample_rate,
        "output_audio_codec": args.codec,
    }

    # Common optional.
    if args.pace is not None:
        kwargs["pace"] = args.pace

    # Model-specific optionals (validated/filtered in validate_args()).
    if args.model == "bulbul:v3":
        if args.temperature is not None:
            kwargs["temperature"] = args.temperature
    else:  # bulbul:v2
        if args.pitch is not None:
            kwargs["pitch"] = args.pitch
        if args.loudness is not None:
            kwargs["loudness"] = args.loudness
        if args.enable_preprocessing:
            kwargs["enable_preprocessing"] = True

    for attempt in range(max_retries):
        if STATE.interrupted:
            raise KeyboardInterrupt()

        try:
            return client.text_to_speech.convert(**kwargs)
        except Exception as e:
            err_str = str(e)
            is_rate_limit = "429" in err_str or "rate limit" in err_str.lower()

            if is_rate_limit and attempt < max_retries - 1:
                backoff = (initial_backoff * (2**attempt)) + random.uniform(1.0, 4.0)
                print(
                    f"\n[Rate Limit 429] on {label}. Cooling down for "
                    f"{backoff:.1f}s (attempt {attempt + 1}/{max_retries})..."
                )
                time.sleep(backoff)
                continue
            raise

    raise RuntimeError(f"Exceeded max retries ({max_retries}) on {label}.")


def synthesize_unit(
    client: SarvamAI,
    text: str,
    label: str,
    args: argparse.Namespace,
    limit: int,
) -> dict[str, Any]:
    """
    Synthesize one input unit (a file's contents or a literal string),
    chunking when needed and concatenating the resulting audio bytes.
    """
    chunks = split_text_into_chunks(text, limit)
    if not chunks:
        return {
            "audio_bytes": b"",
            "request_ids": [],
            "chunk_count": 0,
        }

    if len(chunks) > 1:
        print(
            f"    [chunking] input exceeds {limit} chars; split into "
            f"{len(chunks)} chunk(s); audio will be concatenated."
        )

    audio_bytes = b""
    request_ids: list[str] = []

    for index, chunk in enumerate(chunks, start=1):
        response = synthesize_chunk(
            client,
            chunk,
            args,
            f"{label} [chunk {index}/{len(chunks)}]",
            args.max_retries,
            INITIAL_BACKOFF,
        )

        request_id = extract_request_id(response)
        if request_id:
            request_ids.append(request_id)

        audios = extract_audio_b64(response)
        if not audios:
            raise RuntimeError("no audio returned in response")

        for b64 in audios:
            audio_bytes += base64.b64decode(b64)

        if args.delay > 0 and index < len(chunks):
            time.sleep(args.delay)

    return {
        "audio_bytes": audio_bytes,
        "request_ids": request_ids,
        "chunk_count": len(chunks),
    }


def write_unit_outputs(
    stem: str,
    source_name: str,
    text: str,
    result: dict[str, Any],
    args: argparse.Namespace,
    output_dir: Path,
) -> Path:
    """Write the audio file and raw .json metadata for one synthesized unit."""
    ext = CODEC_EXTENSIONS.get(args.codec, ".wav")
    audio_path = output_dir / f"{stem}{ext}"
    json_path = output_dir / f"{stem}.json"

    audio_path.write_bytes(result.get("audio_bytes") or b"")

    # Human-readable metadata; never store the raw base64 audio here.
    raw = {
        "source_file": source_name,
        "language_code": args.language,
        "language": SUPPORTED_LANGUAGES.get(args.language, args.language),
        "speaker": args.speaker,
        "model": args.model,
        "pace": args.pace,
        "pitch": args.pitch,
        "loudness": args.loudness,
        "temperature": args.temperature,
        "enable_preprocessing": args.enable_preprocessing,
        "speech_sample_rate": args.sample_rate,
        "output_audio_codec": args.codec,
        "char_count": len(text),
        "chunk_count": result.get("chunk_count"),
        "request_ids": result.get("request_ids"),
        "audio_file": audio_path.name,
    }
    json_path.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return audio_path


def write_aggregate_outputs(
    records: list[dict[str, Any]],
    args: argparse.Namespace,
    output_dir: Path,
) -> None:
    """Write the suite-consistent manifest JSON/CSV and Markdown catalog."""
    manifest_json = output_dir / "tts_manifest.json"
    manifest_csv = output_dir / "tts_manifest.csv"
    catalog_md = output_dir / "TTS_CATALOG.md"

    manifest_json.write_text(
        json.dumps(records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    fieldnames = [
        "source",
        "language",
        "speaker",
        "model",
        "sample_rate",
        "codec",
        "char_count",
        "chunks",
        "audio_file",
        "status",
    ]
    with manifest_csv.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            writer.writerow({k: record.get(k, "") for k in fieldnames})

    md_lines = [
        "# Sarvam AI — Text-to-Speech Catalog",
        "",
        "> Generated by **Sarvam AI Standalone Text-to-Speech Synthesizer**.",
        "",
        f"* **Total Units:** {len(records)}",
        f"* **Model:** {args.model}",
        f"* **Language:** {SUPPORTED_LANGUAGES.get(args.language, args.language)} ({args.language})",
        f"* **Speaker:** {args.speaker}",
        f"* **Sample Rate:** {args.sample_rate} Hz",
        f"* **Codec:** {args.codec}",
        f"* **Generated:** {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "| Source | Language | Speaker | Model | Rate | Codec | Chars | Chunks | Audio | Status |",
        "| :--- | :--- | :--- | :--- | ---: | :--- | ---: | ---: | :--- | :--- |",
    ]
    for record in records:
        md_lines.append(
            "| `{source}` | {language} | {speaker} | {model} | {sample_rate} | "
            "{codec} | {char_count} | {chunks} | `{audio_file}` | {status} |".format(
                source=record.get("source", ""),
                language=record.get("language", ""),
                speaker=record.get("speaker", ""),
                model=record.get("model", ""),
                sample_rate=record.get("sample_rate", ""),
                codec=record.get("codec", ""),
                char_count=record.get("char_count", 0),
                chunks=record.get("chunks", 0),
                audio_file=record.get("audio_file", ""),
                status=record.get("status", ""),
            )
        )
    md_lines.append("")
    catalog_md.write_text("\n".join(md_lines), encoding="utf-8")


def print_speaker_lists() -> None:
    """Print the available speakers per model for --list-speakers."""
    print("Available Sarvam TTS speakers")
    print("=============================")
    print()
    print(f"bulbul:v3 (default: {DEFAULT_SPEAKER_V3})")
    print("  " + ", ".join(SPEAKERS_V3))
    print()
    print(f"bulbul:v2 (default: {DEFAULT_SPEAKER_V2})")
    print("  " + ", ".join(SPEAKERS_V2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Synthesize speech from text files or literal strings across "
            "Indic languages and English with the Sarvam AI Text-to-Speech "
            "API (bulbul:v3)."
        )
    )

    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Path to a text file or a folder of .txt/.md files (default: current directory).",
    )
    parser.add_argument(
        "--text",
        type=str,
        default=None,
        help="Synthesize this literal text instead of reading files.",
    )
    parser.add_argument(
        "--language",
        "--language-code",
        dest="language",
        type=str,
        default=None,
        help="Target language BCP-47 code, e.g. hi-IN (REQUIRED).",
    )
    parser.add_argument(
        "--speaker",
        type=str,
        default=None,
        help="Speaker persona (default: model default, shubh for bulbul:v3).",
    )
    parser.add_argument(
        "--model",
        type=str,
        choices=MODELS,
        default=DEFAULT_MODEL,
        help=f"TTS model (default: {DEFAULT_MODEL}).",
    )
    parser.add_argument(
        "--pace",
        type=float,
        default=1.0,
        help="Speaking pace; v3 range 0.5-2.0, v2 range 0.3-3.0 (default: 1.0).",
    )
    parser.add_argument(
        "--pitch",
        type=float,
        default=None,
        help="Voice pitch adjustment (bulbul:v2 only; default: model default).",
    )
    parser.add_argument(
        "--loudness",
        type=float,
        default=None,
        help="Loudness adjustment (bulbul:v2 only; default: model default).",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=None,
        help="Sampling temperature (bulbul:v3 only; default: model default).",
    )
    parser.add_argument(
        "--sample-rate",
        type=int,
        choices=SUPPORTED_SAMPLE_RATES,
        default=DEFAULT_SAMPLE_RATE,
        help=f"Audio sample rate in Hz (default: {DEFAULT_SAMPLE_RATE}).",
    )
    parser.add_argument(
        "--codec",
        "--output-audio-codec",
        dest="codec",
        type=str,
        choices=CODEC_CHOICES,
        default=DEFAULT_CODEC,
        help=f"Output audio codec (default: {DEFAULT_CODEC}).",
    )
    parser.add_argument(
        "--enable-preprocessing",
        action="store_true",
        help="Enable text normalization/preprocessing (bulbul:v2 only).",
    )
    parser.add_argument(
        "--list-speakers",
        action="store_true",
        help="List the available speakers per model and exit.",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        type=str,
        default=None,
        help="Output directory for generated audio (default: <path>/sarvam_tts_audio).",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY,
        help=f"Cooldown in seconds between API calls (default: {DEFAULT_DELAY}).",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=DEFAULT_MAX_RETRIES,
        help=f"Maximum retries on 429 rate limits (default: {DEFAULT_MAX_RETRIES}).",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default=None,
        help="Sarvam AI API subscription key (or set SARVAM_API_KEY in environment or .env file).",
    )

    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> str | None:
    """
    Validate config and normalize model-specific options in-place. Returns
    an error message string if the args are invalid, else None. Incompatible
    options for the chosen model are dropped with a warning.
    """
    if not args.language:
        return "--language (target language) is required."
    if args.language not in SUPPORTED_LANGUAGES:
        return (
            f"--language '{args.language}' is not a supported language code. "
            f"Supported: {', '.join(sorted(SUPPORTED_LANGUAGES))}."
        )

    valid_speakers = speakers_for_model(args.model)
    if args.speaker:
        args.speaker = args.speaker.lower()
        if args.speaker not in valid_speakers:
            return (
                f"--speaker '{args.speaker}' is not valid for model "
                f"{args.model}. Valid speakers: {', '.join(valid_speakers)}."
            )
    else:
        args.speaker = default_speaker_for_model(args.model)

    if args.sample_rate not in SUPPORTED_SAMPLE_RATES:
        return (
            f"--sample-rate {args.sample_rate} is not supported. "
            f"Supported: {', '.join(str(r) for r in SUPPORTED_SAMPLE_RATES)}."
        )

    low, high = pace_range_for_model(args.model)
    if args.pace is not None and not low <= args.pace <= high:
        return (
            f"--pace {args.pace} is out of range for {args.model} "
            f"(allowed {low}-{high})."
        )

    # Drop incompatible model-specific options with a warning.
    if args.model == "bulbul:v3":
        if args.pitch is not None:
            print("[!] Warning: --pitch is bulbul:v2 only; ignoring for bulbul:v3.")
            args.pitch = None
        if args.loudness is not None:
            print("[!] Warning: --loudness is bulbul:v2 only; ignoring for bulbul:v3.")
            args.loudness = None
        if args.enable_preprocessing:
            print(
                "[!] Warning: --enable-preprocessing is bulbul:v2 only; "
                "ignoring for bulbul:v3."
            )
            args.enable_preprocessing = False
    else:  # bulbul:v2
        if args.temperature is not None:
            print(
                "[!] Warning: --temperature is bulbul:v3 only; ignoring for bulbul:v2."
            )
            args.temperature = None

    if args.max_retries <= 0:
        return "--max-retries must be greater than 0."
    if args.delay < 0:
        return "--delay must not be negative."
    return None


def main() -> int:
    args = parse_args()

    if args.list_speakers:
        print_speaker_lists()
        return 0

    if not HAS_SARVAM:
        print("ERROR: 'sarvamai' package is required.")
        print("Please install dependencies: pip install -r requirements.txt")
        return 1

    error = validate_args(args)
    if error:
        print(f"ERROR: {error}")
        return 1

    # Determine input units: literal text, single file, or folder of files.
    input_path = Path(args.path).resolve()
    target_dir = input_path if input_path.is_dir() else input_path.parent

    api_key = get_api_key(cli_key=args.api_key, target_dir=target_dir)
    if not api_key:
        print("ERROR: Sarvam AI API subscription key was not found.")
        print()
        print("Please provide your key via one of the following methods:")
        print("  1. CLI argument:         --api-key 'your-api-key'")
        print(
            "  2. Environment variable: export SARVAM_API_KEY='your-api-key' (or $env:SARVAM_API_KEY='your-api-key')"
        )
        print(
            "  3. .env file:            Copy .env.sample to .env and set SARVAM_API_KEY='your-api-key'"
        )
        return 1

    # Build the list of (stem, source_name, text) units to synthesize.
    units: list[tuple[str, str, str]] = []
    if args.text is not None:
        units.append(("text", "literal --text", args.text))
    elif input_path.is_dir():
        text_files = find_text_files(input_path)
        if not text_files:
            print(f"No supported text files (.txt/.md) found in: {input_path}")
            return 0
        for fp in text_files:
            try:
                units.append((fp.stem, fp.name, fp.read_text(encoding="utf-8")))
            except Exception as exc:
                print(f"  ✗ {fp.name}: could not read file ({exc})")
    elif input_path.is_file():
        try:
            units.append(
                (
                    input_path.stem,
                    input_path.name,
                    input_path.read_text(encoding="utf-8"),
                )
            )
        except Exception as exc:
            print(f"ERROR: could not read file '{input_path}': {exc}")
            return 1
    else:
        print(f"ERROR: path '{args.path}' does not exist.")
        return 1

    if not units:
        print("No input text to synthesize.")
        return 0

    output_dir = (
        Path(args.output_dir).resolve() if args.output_dir else target_dir / OUTPUT_DIR
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    STATE.setup_signals()
    STATE.manifest_path = str(output_dir / "tts_manifest.json")

    limit = char_limit_for_model(args.model)

    print("=======================================================")
    print(" Sarvam AI Text-to-Speech Synthesizer")
    print(f" Model         : {args.model}")
    print(
        f" Language      : {SUPPORTED_LANGUAGES.get(args.language, args.language)} ({args.language})"
    )
    print(f" Speaker       : {args.speaker}")
    print(f" Sample Rate   : {args.sample_rate} Hz")
    print(f" Codec         : {args.codec}")
    print(f" Output Dir    : {output_dir}")
    print(f" Input Units   : {len(units)}")
    print("=======================================================")
    print()

    client = SarvamAI(api_subscription_key=api_key)

    successful = 0
    failed = 0

    for index, (stem, source_name, text) in enumerate(units, start=1):
        print(f"[{index}/{len(units)}] {source_name}")
        try:
            result = synthesize_unit(client, text, source_name, args, limit)
            audio_path = write_unit_outputs(
                stem, source_name, text, result, args, output_dir
            )
            successful += 1
            print(f"  ✓ {source_name} -> {audio_path.name}")
            STATE.records.append(
                {
                    "source": source_name,
                    "language": args.language,
                    "speaker": args.speaker,
                    "model": args.model,
                    "sample_rate": args.sample_rate,
                    "codec": args.codec,
                    "char_count": len(text),
                    "chunks": result.get("chunk_count"),
                    "audio_file": audio_path.name,
                    "status": "ok",
                }
            )
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            failed += 1
            print(f"  ✗ {source_name}: {exc}")
            STATE.records.append(
                {
                    "source": source_name,
                    "language": args.language,
                    "speaker": args.speaker,
                    "model": args.model,
                    "sample_rate": args.sample_rate,
                    "codec": args.codec,
                    "char_count": len(text),
                    "chunks": 0,
                    "audio_file": "",
                    "status": f"failed: {exc}",
                }
            )

    write_aggregate_outputs(STATE.records, args, output_dir)

    print()
    print("=== Finished ===")
    print(f"Successful : {successful}")
    print(f"Failed     : {failed}")
    print(f"Output     : {output_dir}")

    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
