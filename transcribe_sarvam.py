from __future__ import annotations

"""
Sarvam AI Speech-to-Text Transcription & Diarization Tool
=========================================================
A robust CLI tool and Python module that transcribes audio files using
Sarvam AI's Speech-to-Text APIs (default model: ``saaras:v4``, with support
for ``saaras:v3``).

Features:
- Single-file or folder batch processing: pass an individual audio file or an
  entire folder of recordings.
- Multi-model support:
  * ``saaras:v4`` (current default): state-of-the-art multilingual ASR supporting
    Indian & Global English and 22 scheduled Indian languages, with custom
    domain keyterms biasing.
  * ``saaras:v3``: high-performance streaming and batch ASR engine.
- Flexible output modes:
  * ``transcribe`` (default): standard transcription in the original language.
  * ``translate``: transcribe and translate Indic speech directly into English.
  * ``verbatim``: exact word-for-word transcript preserving filler words and numbers.
  * ``translit``: romanized transliteration into Latin/Roman script.
  * ``codemix``: code-mixed output (e.g. Hinglish) matching natural spoken style.
- Speaker Diarization: identify who spoke when, either with automatic detection
  or constrained to a known number of speakers (1-20 speakers).
- Domain Keyterms Biasing: provide up to 50 custom domain names, brand terms,
  or technical words to bias recognition in ``saaras:v4``.
- Multi-format exports: per audio recording, generates:
  1. Formatted human-readable transcript with speaker labels and millisecond timestamps (``.txt``).
  2. Chronological timeline spreadsheet (``.csv``).
  3. Raw API response payload (``.json``).
- Reusable single-item worker: exposes ``transcribe_single_audio()`` for in-process
  import by web interfaces (e.g., Gradio in ``app.py``) or automated pipelines.
"""

import argparse
import csv
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable, Sequence

# Optional python-dotenv for API key resolution
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

# Sarvam AI official Python SDK
try:
    from sarvamai import SarvamAI

    HAS_SARVAM = True
except ImportError:
    HAS_SARVAM = False


# ---------------------------------------------------------------------------
# Module Constants & Supported Formats
# ---------------------------------------------------------------------------

AUDIO_EXTENSIONS = {
    ".wav",
    ".mp3",
    ".aac",
    ".aiff",
    ".aif",
    ".ogg",
    ".opus",
    ".flac",
    ".mp4",
    ".m4a",
    ".amr",
    ".wma",
    ".webm",
}

MODELS = ("saaras:v4", "saaras:v3")
DEFAULT_MODEL = "saaras:v4"

MODES = ("transcribe", "translate", "verbatim", "translit", "codemix")
DEFAULT_MODE = "transcribe"

# Supported language codes in Sarvam STT APIs (22 scheduled Indic languages + English + unknown)
SUPPORTED_LANGUAGES = {
    "unknown": "Auto-detect (Unknown)",
    "en-IN": "English (Indian/Global)",
    "hi-IN": "Hindi",
    "bn-IN": "Bengali",
    "kn-IN": "Kannada",
    "ml-IN": "Malayalam",
    "mr-IN": "Marathi",
    "od-IN": "Odia",
    "pa-IN": "Punjabi",
    "ta-IN": "Tamil",
    "te-IN": "Telugu",
    "gu-IN": "Gujarati",
    "as-IN": "Assamese",
    "ur-IN": "Urdu",
    "ne-IN": "Nepali",
    "kok-IN": "Konkani",
    "ks-IN": "Kashmiri",
    "sd-IN": "Sindhi",
    "sa-IN": "Sanskrit",
    "sat-IN": "Santali",
    "mni-IN": "Manipuri",
    "brx-IN": "Bodo",
    "mai-IN": "Maithili",
    "doi-IN": "Dogri",
}

BATCH_SIZE = 20
OUTPUT_DIR = Path("sarvam_transcripts")


# ---------------------------------------------------------------------------
# File Discovery & Chunking Helpers
# ---------------------------------------------------------------------------

def find_audio_files(path: Path) -> list[Path]:
    """
    Find audio files to process.

    If ``path`` is an audio file, returns ``[path]``.
    If ``path`` is a folder, returns all supported audio files within it
    (sorted alphabetically).
    """
    path = path.resolve()
    if path.is_file():
        if path.suffix.lower() in AUDIO_EXTENSIONS:
            return [path]
        return []

    if path.is_dir():
        return sorted(
            [
                p for p in path.iterdir()
                if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS
            ],
            key=lambda p: p.name.lower(),
        )

    return []


def chunks(items: list[Path], size: int) -> Iterable[list[Path]]:
    """Yield successive chunks of items of maximum length ``size``."""
    for i in range(0, len(items), size):
        yield items[i:i + size]


def format_time(seconds: Any) -> str:
    """
    Convert seconds (float or int) to formatted string HH:MM:SS.mmm.

    Example: 125.45 -> '00:02:05.450'
    """
    try:
        total_ms = max(0, round(float(seconds) * 1000))
    except (TypeError, ValueError):
        return "00:00:00.000"

    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1_000)

    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def normalize_speaker_id(value: Any) -> str:
    """
    Convert raw speaker identifiers from Sarvam into clean, human-readable labels.

    Handles numeric IDs ('0' -> 'Speaker 1'), 'SPEAKER_00' -> 'Speaker 1', etc.
    """
    if value is None:
        return "Unknown Speaker"

    value = str(value).strip()

    # Sarvam commonly uses numeric indices "0", "1", ...
    if value.isdigit():
        return f"Speaker {int(value) + 1}"

    # Handle labels like SPEAKER_00 / SPEAKER_01
    upper = value.upper()
    if upper.startswith("SPEAKER_"):
        suffix = upper.split("_", 1)[1]
        if suffix.isdigit():
            return f"Speaker {int(suffix) + 1}"

    return value


def to_dict(obj: Any) -> dict[str, Any]:
    """
    Convert SDK response objects or dict-like objects into plain dictionaries,
    supporting Pydantic v1 / v2 model serialization methods.
    """
    if isinstance(obj, dict):
        return dict(obj)
    for method_name in ("model_dump", "dict"):
        method = getattr(obj, method_name, None)
        if callable(method):
            try:
                dumped = method()
                if isinstance(dumped, dict):
                    return dumped
            except Exception:
                pass
    if hasattr(obj, "__dict__"):
        return {k: v for k, v in vars(obj).items() if not k.startswith("_")}
    return {}


# ---------------------------------------------------------------------------
# Result Parsing & Formatted Export
# ---------------------------------------------------------------------------

def extract_segments(data: dict) -> list[dict[str, Any]]:
    """
    Extract diarized or timestamped speech segments from a Sarvam STT response.

    Handles:
    1. Diarized transcript: data['diarized_transcript']['entries']
    2. Segment entries: data['segments']
    3. REST timestamps model: data['timestamps']['words'] or sentence chunks
    """
    # 1. Primary: Diarized transcript entries from batch jobs
    diarized = data.get("diarized_transcript") or {}
    entries = diarized.get("entries") or []

    segments: list[dict[str, Any]] = []

    for entry in entries:
        text = str(entry.get("transcript", "")).strip()
        if not text:
            continue

        start = entry.get("start_time_seconds")
        end = entry.get("end_time_seconds")
        speaker = normalize_speaker_id(entry.get("speaker_id"))

        segments.append(
            {
                "speaker": speaker,
                "start": start,
                "end": end,
                "text": text,
            }
        )

    # 2. Defensive fallback for alternative 'segments' schema
    if not segments:
        for entry in data.get("segments") or []:
            text = str(entry.get("text", entry.get("transcript", ""))).strip()
            if not text:
                continue

            segments.append(
                {
                    "speaker": normalize_speaker_id(
                        entry.get("speaker", entry.get("speaker_id"))
                    ),
                    "start": entry.get("start", entry.get("start_time_seconds")),
                    "end": entry.get("end", entry.get("end_time_seconds")),
                    "text": text,
                }
            )

    # 3. Fallback for REST API 'timestamps' object (chunk/word timestamps without diarization)
    if not segments:
        timestamps_obj = data.get("timestamps") or {}
        words_list = timestamps_obj.get("words") or []
        if isinstance(words_list, list):
            for item in words_list:
                if isinstance(item, dict):
                    w_text = str(item.get("word", "")).strip()
                    if w_text:
                        segments.append(
                            {
                                "speaker": "Speaker",
                                "start": item.get("start_time_seconds"),
                                "end": item.get("end_time_seconds"),
                                "text": w_text,
                            }
                        )

    return segments


def write_outputs(
    audio_path: Path,
    result_json: dict,
    output_dir: Path,
    model: str = DEFAULT_MODEL,
    mode: str = DEFAULT_MODE,
) -> None:
    """
    Write three output files for a transcribed audio recording:
    1. Human-readable dialogue transcript with timestamps (.txt)
    2. Chronological timeline spreadsheet (.csv)
    3. Raw JSON response payload (.json)
    """
    base = output_dir / audio_path.stem
    txt_path = base.with_suffix(".txt")
    json_path = base.with_suffix(".json")
    csv_path = base.with_suffix(".csv")

    segments = extract_segments(result_json)
    lang_detected = result_json.get("language_code") or "auto-detected"

    # ---- 1. Human-readable transcript (.txt) ----
    lines: list[str] = [
        f"File     : {audio_path.name}",
        f"Model    : {model}",
        f"Mode     : {mode}",
        f"Language : {lang_detected}",
        "",
        "TRANSCRIPT",
        "=" * 80,
        "",
    ]

    if segments:
        for segment in segments:
            start = format_time(segment["start"])
            end = format_time(segment["end"])
            lines.append(
                f"[{start} - {end}] {segment['speaker']}: {segment['text']}"
            )
    else:
        # If no segments, preserve the full transcript string
        transcript = str(result_json.get("transcript", "")).strip()
        lines.append(transcript or "[No transcript returned]")

    txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ---- 2. Raw JSON (.json) ----
    # Enrich result JSON with execution metadata if missing
    if "model" not in result_json:
        result_json["model"] = model
    if "mode" not in result_json:
        result_json["mode"] = mode

    json_path.write_text(
        json.dumps(result_json, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # ---- 3. Spreadsheet Timeline (.csv) ----
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "speaker",
                "start_time_seconds",
                "end_time_seconds",
                "start_time",
                "end_time",
                "transcript",
            ]
        )

        for segment in segments:
            writer.writerow(
                [
                    segment["speaker"],
                    segment["start"],
                    segment["end"],
                    format_time(segment["start"]),
                    format_time(segment["end"]),
                    segment["text"],
                ]
            )

    print(f"  ✓ {audio_path.name}")
    print(f"    TXT : {txt_path}")
    print(f"    CSV : {csv_path}")
    print(f"    JSON: {json_path}")


# ---------------------------------------------------------------------------
# Single-Item Transcription Worker (Used by CLI and Web UI)
# ---------------------------------------------------------------------------

def transcribe_single_audio(
    client: SarvamAI,
    audio_path: Path,
    model: str = DEFAULT_MODEL,
    mode: str = DEFAULT_MODE,
    language_code: str | None = None,
    with_diarization: bool = True,
    num_speakers: int | None = None,
    keyterms: list[str] | None = None,
    with_timestamps: bool = True,
) -> dict[str, Any]:
    """
    Transcribe a single audio file with Sarvam AI.

    If diarization is disabled and the file is short, utilizes the synchronous
    REST endpoint for immediate low-latency results.
    If diarization is requested, submits a dedicated batch job to obtain
    speaker-separated dialogue turns.

    Returns the parsed result dictionary.
    """
    audio_path = Path(audio_path).resolve()
    if not audio_path.is_file():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    # Normalize language code: 'auto' is mapped to None or 'unknown'
    norm_lang = None
    if language_code and language_code.lower() not in ("auto", "none", ""):
        norm_lang = language_code

    # Clean keyterms: max 50 terms, stripped
    clean_keyterms: list[str] | None = None
    if keyterms:
        clean_keyterms = [k.strip() for k in keyterms if k.strip()][:50]
        if not clean_keyterms:
            clean_keyterms = None

    # Path A: Fast REST API when diarization is not requested
    if not with_diarization:
        with open(audio_path, "rb") as fh:
            kwargs: dict[str, Any] = {
                "file": (audio_path.name, fh),
                "model": model,
                "mode": mode,
                "with_timestamps": with_timestamps,
            }
            if norm_lang:
                kwargs["language_code"] = norm_lang
            if clean_keyterms and model == "saaras:v4":
                kwargs["keyterms"] = clean_keyterms

            response = client.speech_to_text.transcribe(**kwargs)
            return to_dict(response)

    # Path B: Batch API with Speaker Diarization
    with tempfile.TemporaryDirectory(prefix="sarvam_stt_single_") as tmp:
        tmp_dir = Path(tmp)

        job_kwargs: dict[str, Any] = {
            "model": model,
            "mode": mode,
            "with_diarization": True,
            "with_timestamps": with_timestamps,
        }
        if norm_lang:
            job_kwargs["language_code"] = norm_lang
        if num_speakers is not None:
            job_kwargs["num_speakers"] = num_speakers
        if clean_keyterms and model == "saaras:v4":
            job_kwargs["keyterms"] = clean_keyterms

        job = client.speech_to_text_job.create_job(**job_kwargs)
        job.upload_files(file_paths=[str(audio_path)])
        job.start()
        job.wait_until_complete()

        file_results = job.get_file_results()
        successful_entries = file_results.get("successful") or []
        failed_entries = file_results.get("failed") or []

        if failed_entries and not successful_entries:
            err_msg = failed_entries[0].get("error_message") or failed_entries[0].get("error") or "Job failed"
            raise RuntimeError(f"Transcription failed: {err_msg}")

        job.download_outputs(output_dir=str(tmp_dir))
        json_candidates = sorted(tmp_dir.glob("*.json"))
        if not json_candidates:
            raise RuntimeError(f"Could not locate output JSON for {audio_path.name}")

        result_json = json.loads(json_candidates[0].read_text(encoding="utf-8"))
        return result_json


# ---------------------------------------------------------------------------
# Batch Processing Implementation
# ---------------------------------------------------------------------------

def locate_downloaded_json(
    temp_dir: Path,
    entry: dict,
    entry_index: int,
    successful_count: int,
) -> Path | None:
    """
    Locate the JSON downloaded by the SDK in the temp directory.
    Uses file name properties from the response entry with fallback to indexed order.
    """
    possible_names = [
        entry.get("file_name"),
        entry.get("output_file_name"),
        entry.get("filename"),
    ]

    for name in possible_names:
        if name:
            candidate = temp_dir / str(name)
            if candidate.exists():
                return candidate

    json_files = sorted(temp_dir.glob("*.json"))

    if len(json_files) == successful_count and entry_index < len(json_files):
        return json_files[entry_index]

    if len(json_files) == 1:
        return json_files[0]

    return None


def match_result_to_input(
    entry: dict,
    batch: list[Path],
    fallback_index: int,
) -> Path | None:
    """Match a batch result entry back to its source input file path."""
    names = [
        entry.get("input_file_name"),
        entry.get("file_name"),
        entry.get("filename"),
    ]

    for name in names:
        if not name:
            continue

        name_str = Path(str(name)).name
        for audio_path in batch:
            if audio_path.name == name_str:
                return audio_path

    if 0 <= fallback_index < len(batch):
        return batch[fallback_index]

    return None


def process_batch(
    client: SarvamAI,
    batch: list[Path],
    batch_number: int,
    output_dir: Path,
    model: str,
    mode: str,
    language_code: str | None,
    with_diarization: bool,
    with_timestamps: bool,
    num_speakers: int | None,
    keyterms: list[str] | None,
) -> tuple[int, int]:
    """
    Upload and process a batch of audio files using Sarvam's bulk job API.
    Returns (successful_count, failed_count).
    """
    print(f"=== Batch {batch_number}: {len(batch)} file(s) ===")

    successful = 0
    failed = 0

    with tempfile.TemporaryDirectory(prefix="sarvam_stt_batch_") as tmp:
        tmp_dir = Path(tmp)

        try:
            job_kwargs: dict[str, Any] = {
                "model": model,
                "mode": mode,
                "with_diarization": with_diarization,
                "with_timestamps": with_timestamps,
            }

            if language_code and language_code.lower() not in ("auto", "none", ""):
                job_kwargs["language_code"] = language_code

            if with_diarization and num_speakers is not None:
                job_kwargs["num_speakers"] = num_speakers

            if keyterms and model == "saaras:v4":
                job_kwargs["keyterms"] = keyterms[:50]

            job = client.speech_to_text_job.create_job(**job_kwargs)
            job.upload_files(file_paths=[str(p) for p in batch])

            print(f"  Job created : {job.job_id}")
            print("  Starting job...")
            job.start()

            print("  Waiting for Sarvam to process...")
            job.wait_until_complete()

            file_results = job.get_file_results()
            successful_entries = file_results.get("successful") or []
            failed_entries = file_results.get("failed") or []

            if successful_entries:
                job.download_outputs(output_dir=str(tmp_dir))

            # Map successful results to original audio files and write outputs
            for index, entry in enumerate(successful_entries):
                audio_path = match_result_to_input(entry, batch, index)
                if audio_path is None:
                    print(
                        f"  ✗ Could not map result {entry.get('file_name', index)} to input file."
                    )
                    failed += 1
                    continue

                json_path = locate_downloaded_json(
                    tmp_dir,
                    entry,
                    index,
                    len(successful_entries),
                )

                if json_path is None:
                    print(f"  ✗ Could not locate downloaded JSON for {audio_path.name}")
                    failed += 1
                    continue

                try:
                    result_json = json.loads(json_path.read_text(encoding="utf-8"))
                except Exception as exc:
                    print(f"  ✗ Could not parse result for {audio_path.name}: {exc}")
                    failed += 1
                    continue

                write_outputs(
                    audio_path=audio_path,
                    result_json=result_json,
                    output_dir=output_dir,
                    model=model,
                    mode=mode,
                )
                successful += 1

            for entry in failed_entries:
                name = (
                    entry.get("file_name")
                    or entry.get("filename")
                    or "unknown file"
                )
                error = (
                    entry.get("error_message")
                    or entry.get("error")
                    or "Unknown error"
                )
                print(f"  ✗ {name}: {error}")
                failed += 1

        except Exception as exc:
            print(f"  ✗ Batch {batch_number} failed: {exc}")
            print("    Continuing with the next batch...")
            failed += len(batch)

    print()
    return successful, failed


# ---------------------------------------------------------------------------
# API Key Resolution
# ---------------------------------------------------------------------------

def get_api_key(cli_key: str | None = None, target_dir: Path | None = None) -> str | None:
    """
    Resolve Sarvam AI API subscription key with prioritized fallback:
    1. Explicit CLI argument (--api-key)
    2. Environment variable (SARVAM_API_KEY)
    3. .env file in target directory, current working directory, or script directory
    """
    if cli_key and cli_key.strip():
        return cli_key.strip()

    env_key = os.environ.get("SARVAM_API_KEY", "").strip()
    if env_key:
        return env_key

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


# ---------------------------------------------------------------------------
# Command-Line Interface (CLI)
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Transcribe audio files or folders using Sarvam Saaras v4/v3, "
            "with speaker diarization, multiple modes, and timestamp exports."
        )
    )

    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help=(
            "Path to an audio file or a folder containing audio recordings "
            "(default: current directory)."
        ),
    )

    parser.add_argument(
        "--model",
        type=str,
        choices=MODELS,
        default=DEFAULT_MODEL,
        help=f"Speech recognition model to use (default: {DEFAULT_MODEL}).",
    )

    parser.add_argument(
        "--mode",
        type=str,
        choices=MODES,
        default=DEFAULT_MODE,
        help=(
            "Transcription mode: 'transcribe' (native), 'translate' (speech-to-English), "
            "'verbatim' (unfiltered), 'translit' (Roman script), or 'codemix' (Hinglish/mixed) "
            f"(default: {DEFAULT_MODE})."
        ),
    )

    parser.add_argument(
        "--language-code",
        "--language",
        dest="language_code",
        type=str,
        default=None,
        help=(
            "Input audio language BCP-47 code (e.g. 'hi-IN', 'en-IN', or 'unknown' for auto-detection). "
            "If omitted, Sarvam auto-detects the spoken language."
        ),
    )

    parser.add_argument(
        "--speakers",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Expected number of distinct speakers (1-20). "
            "If omitted, Sarvam automatically detects speaker count."
        ),
    )

    parser.add_argument(
        "--no-diarization",
        action="store_true",
        help="Disable speaker diarization (segmentation by speaker).",
    )

    parser.add_argument(
        "--with-timestamps",
        action="store_true",
        default=True,
        help="Include phrase/chunk timestamps in the output (default: True).",
    )

    parser.add_argument(
        "--keyterms",
        type=str,
        default=None,
        help=(
            "Comma-separated list of up to 50 domain-specific terms (names, brands, technical words) "
            "to bias recognition toward (supported with model saaras:v4)."
        ),
    )

    parser.add_argument(
        "--batch-size",
        type=int,
        default=BATCH_SIZE,
        metavar="N",
        help=f"Number of audio files per processing batch (default: {BATCH_SIZE}).",
    )

    parser.add_argument(
        "--output-dir",
        "-o",
        type=str,
        default=None,
        help="Output directory for generated transcripts (default: <path>/sarvam_transcripts).",
    )

    parser.add_argument(
        "--api-key",
        type=str,
        default=None,
        help="Sarvam AI API subscription key (or set SARVAM_API_KEY in environment or .env file).",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not HAS_SARVAM:
        print("ERROR: 'sarvamai' package is required.")
        print("Please install dependencies: pip install -r requirements.txt")
        return 1

    if args.speakers is not None and not 1 <= args.speakers <= 20:
        print("ERROR: --speakers must be between 1 and 20.")
        return 1

    if args.batch_size <= 0:
        print("ERROR: --batch-size must be greater than 0.")
        return 1

    target_path = Path(args.path).resolve()
    if not target_path.exists():
        print(f"ERROR: Target path '{args.path}' does not exist.")
        return 1

    target_dir = target_path if target_path.is_dir() else target_path.parent

    api_key = get_api_key(cli_key=args.api_key, target_dir=target_dir)
    if not api_key:
        print("ERROR: Sarvam AI API subscription key was not found.")
        print()
        print("Please provide your key via one of the following methods:")
        print("  1. CLI argument:        --api-key 'your-api-key'")
        print("  2. Environment variable: export SARVAM_API_KEY='your-api-key' (or $env:SARVAM_API_KEY='your-api-key')")
        print("  3. .env file:            Copy .env.sample to .env and set SARVAM_API_KEY='your-api-key'")
        return 1

    output_dir = Path(args.output_dir).resolve() if args.output_dir else target_dir / OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    audio_files = find_audio_files(target_path)
    if not audio_files:
        print(f"No supported audio recordings found for: {target_path}")
        print("Supported audio extensions:")
        print(", ".join(sorted(AUDIO_EXTENSIONS)))
        return 0

    # Parse keyterms if provided
    keyterm_list: list[str] | None = None
    if args.keyterms:
        keyterm_list = [k.strip() for k in args.keyterms.split(",") if k.strip()][:50]

    with_diarization = not args.no_diarization

    print("=======================================================")
    print(" Sarvam AI Speech-to-Text Transcription & Diarization")
    print(f" Model        : {args.model}")
    print(f" Mode         : {args.mode}")
    print(f" Language     : {args.language_code or 'auto-detection'}")
    print(f" Diarization  : {'enabled' if with_diarization else 'disabled'}")
    if with_diarization:
        spk_label = str(args.speakers) if args.speakers is not None else "automatic detection"
        print(f" Speakers     : {spk_label}")
    if keyterm_list:
        print(f" Keyterms     : {len(keyterm_list)} term(s) loaded")
    print(f" Files Found  : {len(audio_files)}")
    print(f" Output Dir   : {output_dir}")
    print("=======================================================")
    print()

    client = SarvamAI(api_subscription_key=api_key)

    total_successful = 0
    total_failed = 0

    for batch_number, batch in enumerate(
        chunks(audio_files, args.batch_size),
        start=1,
    ):
        successful, failed = process_batch(
            client=client,
            batch=batch,
            batch_number=batch_number,
            output_dir=output_dir,
            model=args.model,
            mode=args.mode,
            language_code=args.language_code,
            with_diarization=with_diarization,
            with_timestamps=args.with_timestamps,
            num_speakers=args.speakers,
            keyterms=keyterm_list,
        )

        total_successful += successful
        total_failed += failed

    print("=== Finished ===")
    print(f"Successful : {total_successful}")
    print(f"Failed     : {total_failed}")
    print(f"Output     : {output_dir}")

    return 0 if total_failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())