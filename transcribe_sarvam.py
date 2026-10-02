from __future__ import annotations

"""
Sarvam AI Speech-to-Text Transcription, Diarization & Translation Suite
========================================================================
A robust CLI tool and Python module that transcribes audio files using
Sarvam AI's Speech-to-Text APIs (default model: ``saaras:v4``, with support
for ``saaras:v3``), automatically translates the transcribed text into
English (``en-IN``) using Sarvam AI Translation, and exports formatted
Microsoft Word (``.docx``) documents along with TXT, CSV, and JSON deliverables.

Features:
- Single-file or folder batch processing: pass an individual audio file or an
  entire folder of recordings.
- Multi-model ASR support:
  * ``saaras:v4`` (current default): state-of-the-art multilingual ASR supporting
    Indian & Global English and 22 scheduled Indian languages, with custom
    domain keyterms biasing.
  * ``saaras:v3``: high-performance streaming and batch ASR engine.
- Flexible output modes:
  * ``transcribe`` (default): standard transcription in the original language.
  * ``translate``: transcribe and translate Indic speech directly into English via ASR.
  * ``verbatim``: exact word-for-word transcript preserving filler words and numbers.
  * ``translit``: romanized transliteration into Latin/Roman script.
  * ``codemix``: code-mixed output (e.g. Hinglish) matching natural spoken style.
- Automatic Dual Word (.docx) Document Deliverables (Default):
  * Generates 2 separate Microsoft Word (.docx) files per input audio file:
    1. Original language transcript: ``<filename>_<Language>.docx``
    2. English translation: ``<filename>_English.docx`` (when source is Indic)
  * Clean, executive document design with no vendor watermarks or model attributions.
  * Preserves speaker diarization turns, colored speaker tags, and timestamps.
- Optional Extra Formats (disabled by default to prevent folder clutter):
  * ``--export-txt``: plain text transcript with speaker timestamps (.txt)
  * ``--export-csv``: chronological timeline spreadsheet (.csv)
  * ``--export-json``: raw API response payload (.json)
  * ``--all-formats``: export all formats (.docx, .txt, .csv, .json)
- Speaker Diarization: identify who spoke when, either with automatic detection
  or constrained to a known number of speakers (1-20 speakers).
- Domain Keyterms Biasing: provide up to 50 custom domain names, brand terms,
  or technical words to bias recognition in ``saaras:v4``.
- Reusable single-item worker: exposes ``transcribe_single_audio()`` for in-process
  import by web interfaces (e.g., Gradio in ``app.py``) or automated pipelines.
"""

import argparse
import csv
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

import requests

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

# python-docx for Word document generation
try:
    import docx
    from docx.shared import Inches, Pt, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.enum.table import WD_TABLE_ALIGNMENT

    HAS_DOCX = True
except ImportError:
    HAS_DOCX = False


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

TRANSLATE_MODELS = ("sarvam-translate:v1", "mayura:v1")
DEFAULT_TRANSLATE_MODEL = "sarvam-translate:v1"

TRANSLATORS = ("google_free", "gemini")
DEFAULT_TRANSLATOR = "google_free"

# Mapping from Sarvam Indic BCP-47 codes to Google Translate ISO codes
SARVAM_TO_GOOGLE_LANG = {
    "hi-IN": "hi",
    "bn-IN": "bn",
    "kn-IN": "kn",
    "ml-IN": "ml",
    "mr-IN": "mr",
    "od-IN": "or",  # Google Translate uses 'or' for Odia / Oriya
    "pa-IN": "pa",
    "ta-IN": "ta",
    "te-IN": "te",
    "gu-IN": "gu",
    "as-IN": "as",
    "ur-IN": "ur",
    "ne-IN": "ne",
    "kok-IN": "kok",
    "ks-IN": "ks",
    "sd-IN": "sd",
    "sa-IN": "sa",
    "sat-IN": "sat",
    "mni-IN": "mni-Mtei",
    "brx-IN": "brx",
    "mai-IN": "mai",
    "doi-IN": "doi",
    "en-IN": "en",
    "unknown": "auto",
}

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
# File Discovery & Formatting Helpers
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
# Result Parsing
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


# ---------------------------------------------------------------------------
# Audio Optimization & Silence Removal (Cost Reduction)
# ---------------------------------------------------------------------------

def get_ffmpeg_binary() -> str | None:
    """
    Locate a usable ffmpeg executable, checking system PATH first,
    then falling back to the bundled imageio-ffmpeg binary.
    """
    sys_ffmpeg = shutil.which("ffmpeg")
    if sys_ffmpeg:
        return sys_ffmpeg
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def get_audio_duration_seconds(audio_path: Path, ffmpeg_bin: str | None = None) -> float | None:
    """
    Inspect the duration of an audio file in seconds via ffmpeg stderr metadata.
    """
    if ffmpeg_bin is None:
        ffmpeg_bin = get_ffmpeg_binary()
    if not ffmpeg_bin or not audio_path.exists():
        return None

    try:
        cmd = [ffmpeg_bin, "-i", str(audio_path)]
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", proc.stderr)
        if match:
            h, m, s = match.groups()
            return int(h) * 3600 + int(m) * 60 + float(s)
    except Exception:
        pass
    return None


def optimize_audio_for_transcription(
    input_path: Path,
    output_path: Path,
    test_clip_seconds: float | None = None,
    remove_silence: bool = False,
    normalize_sample_rate: bool = True,
    silence_threshold_db: float = -35.0,
    silence_min_duration: float = 0.4,
    ffmpeg_bin: str | None = None,
) -> tuple[Path, dict[str, Any]]:
    """
    Preprocess and optimize an audio recording before uploading to Sarvam AI STT:
    1. Test Clipping: trims to the first `test_clip_seconds` (e.g. 60s or 120s) to keep
       API testing costs minimal.
    2. Silence Removal: removes silent intervals and dead pauses (below -35dB for >0.4s)
       to significantly reduce the billable duration.
    3. Normalization: resamples to 16kHz mono 16-bit WAV (standard optimal ASR format).

    Returns:
        (processed_audio_path, stats_dict)
    """
    if ffmpeg_bin is None:
        ffmpeg_bin = get_ffmpeg_binary()

    needs_optimization = bool(
        (test_clip_seconds and test_clip_seconds > 0)
        or remove_silence
        or normalize_sample_rate
    )

    if not needs_optimization or not ffmpeg_bin:
        return input_path, {"optimized": False}

    output_path.parent.mkdir(parents=True, exist_ok=True)
    orig_duration = get_audio_duration_seconds(input_path, ffmpeg_bin)

    cmd = [ffmpeg_bin, "-y"]
    # If test clipping is requested, apply -t before -i so ffmpeg only decodes the first N seconds
    if test_clip_seconds and test_clip_seconds > 0:
        cmd.extend(["-t", str(test_clip_seconds)])

    cmd.extend(["-i", str(input_path)])

    filters: list[str] = []
    if remove_silence:
        filters.append(
            f"silenceremove=start_periods=1:start_duration={silence_min_duration}:start_threshold={silence_threshold_db}dB:"
            f"stop_periods=-1:stop_duration={silence_min_duration}:stop_threshold={silence_threshold_db}dB"
        )
    if normalize_sample_rate:
        filters.append("aresample=16000,aformat=channel_layouts=mono")

    if filters:
        cmd.extend(["-af", ",".join(filters)])
    else:
        cmd.extend(["-ar", "16000", "-ac", "1"])

    cmd.extend(["-c:a", "pcm_s16le", str(output_path)])

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if proc.returncode != 0 or not output_path.exists():
            print(f"  [!] Audio preprocessing warning: ffmpeg returned code {proc.returncode}")
            return input_path, {"optimized": False, "error": proc.stderr}

        proc_duration = get_audio_duration_seconds(output_path, ffmpeg_bin)
        stats: dict[str, Any] = {
            "optimized": True,
            "original_path": input_path,
            "processed_path": output_path,
            "original_duration": orig_duration,
            "processed_duration": proc_duration,
        }

        if orig_duration and proc_duration:
            saved = max(0.0, orig_duration - proc_duration)
            stats["duration_saved"] = saved
            stats["savings_pct"] = (saved / orig_duration) * 100

        return output_path, stats

    except Exception as exc:
        print(f"  [!] Audio optimization failed ({exc}); using original audio.")
        return input_path, {"optimized": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# English Translation Helpers (Zero Sarvam API Cost)
# ---------------------------------------------------------------------------

def chunk_dialogue_lines(lines: list[str], max_chars: int = 1800) -> list[str]:
    """
    Group lines into chunks not exceeding max_chars, keeping individual
    lines and speaker turns intact.
    """
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for line in lines:
        line_len = len(line) + 1
        if current and (current_len + line_len > max_chars):
            chunks.append("\n".join(current))
            current = [line]
            current_len = line_len
        else:
            current.append(line)
            current_len += line_len

    if current:
        chunks.append("\n".join(current))

    return chunks


def translate_chunk_google_free(
    text: str,
    source_language_code: str | None = None,
    target_language: str = "en",
    timeout: float = 15.0,
) -> str:
    """
    Translate text using Google Free Translation GTX endpoint.
    Fast, reliable, zero-cost, no API key required.
    Preserves speaker turns and timestamp prefixes.
    """
    text = text.strip()
    if not text:
        return ""

    src = "auto"
    if source_language_code:
        src = SARVAM_TO_GOOGLE_LANG.get(
            source_language_code,
            source_language_code.split("-")[0].lower()
        )
        if src in ("unknown", "auto-detected", "none", ""):
            src = "auto"

    url = "https://translate.googleapis.com/translate_a/single"
    params = {
        "client": "gtx",
        "sl": src,
        "tl": target_language,
        "dt": "t",
        "q": text,
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        pieces: list[str] = []
        if data and isinstance(data, list) and len(data) > 0 and isinstance(data[0], list):
            for piece in data[0]:
                if piece and len(piece) > 0 and piece[0]:
                    pieces.append(piece[0])
        return "".join(pieces)
    except Exception as exc:
        # Fallback to deep-translator if installed
        try:
            from deep_translator import GoogleTranslator
            return GoogleTranslator(source=src, target=target_language).translate(text)
        except Exception:
            raise exc


def translate_chunk_gemini(
    text: str,
    gemini_api_key: str,
    source_language: str = "Indic",
    target_language: str = "English",
    model: str = "gemini-2.5-flash",
    timeout: float = 30.0,
) -> str:
    """
    Translate transcript using Google Gemini API.
    Used when a Gemini API key is provided for high-fidelity LLM conversational translation.
    """
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={gemini_api_key}"
    prompt = (
        f"You are a professional audio transcript translator. Translate the following {source_language} speech transcript "
        f"into natural, fluent {target_language}.\n\n"
        f"CRITICAL RULES:\n"
        f"1. Preserve all speaker labels, timestamps, and line breaks exactly as given (e.g. '[00:00:01.000 - 00:00:04.500] Speaker 1: ...').\n"
        f"2. Translate only the spoken dialogue itself into natural English.\n"
        f"3. Return ONLY the translated transcript without extra commentary or explanation.\n\n"
        f"Transcript:\n{text}"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2},
    }
    resp = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    candidates = data.get("candidates") or []
    if candidates:
        content = candidates[0].get("content") or {}
        parts = content.get("parts") or []
        if parts:
            return parts[0].get("text", "").strip()
    return text


def translate_to_english(
    text: str,
    source_language_code: str | None = None,
    client: SarvamAI | None = None,
    translator: str = DEFAULT_TRANSLATOR,
    gemini_api_key: str | None = None,
    model: str | None = None,
    max_retries: int = 3,
    initial_backoff: float = 2.0,
) -> tuple[str, str]:
    """
    Translate transcript into English (en-IN) using a free translation engine
    or Google Gemini. Does NOT use Sarvam API for translation, keeping translation costs at zero.

    Parameters:
        text: Raw transcript or diarized timeline text.
        source_language_code: BCP-47 language code (e.g. 'hi-IN', 'od-IN').
        client: Optional SarvamAI client (retained for backward compatibility, not used for translation).
        translator: Translation engine ('google_free' or 'gemini'). Default is 'google_free'.
        gemini_api_key: Optional Gemini API key if using translator='gemini'.
        model: Deprecated parameter retained for backward compatibility.

    Returns:
        (translated_text, source_language_code_used)
    """
    text = text.strip()
    if not text:
        return "", source_language_code or "en-IN"

    src_lang = source_language_code or "unknown"
    if src_lang.lower() in ("en-IN", "english", "en"):
        return text, "en-IN"

    # Resolve Gemini key from environment or .env if not passed directly
    if translator == "gemini" and not gemini_api_key:
        gemini_api_key = os.environ.get("GEMINI_API_KEY", "").strip() or None

    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    if not lines:
        lines = [text]

    chunks = chunk_dialogue_lines(lines, max_chars=1800)
    translated_parts: list[str] = []

    for index, chunk in enumerate(chunks, start=1):
        translated_chunk = None
        for attempt in range(max_retries):
            try:
                if translator == "gemini" and gemini_api_key:
                    translated_chunk = translate_chunk_gemini(
                        text=chunk,
                        gemini_api_key=gemini_api_key,
                        source_language=SUPPORTED_LANGUAGES.get(src_lang, src_lang),
                    )
                else:
                    translated_chunk = translate_chunk_google_free(
                        text=chunk,
                        source_language_code=src_lang,
                    )
                break
            except Exception as exc:
                if attempt < max_retries - 1:
                    wait_time = initial_backoff * (2 ** attempt) + random.uniform(0.5, 1.5)
                    time.sleep(wait_time)
                    continue
                print(f"    [!] Translation warning on chunk {index} ({translator}): {exc}")
                # Fallback to Google Free if Gemini failed, or preserve chunk
                if translator == "gemini":
                    try:
                        translated_chunk = translate_chunk_google_free(chunk, src_lang)
                        break
                    except Exception:
                        pass
                translated_chunk = chunk

        translated_parts.append(translated_chunk if translated_chunk is not None else chunk)

    full_translated = "\n".join(translated_parts)
    return full_translated, src_lang


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# DOCX Document Generation
# ---------------------------------------------------------------------------

def get_clean_language_name(code: str | None) -> str:
    """
    Return a clean, human-readable language name for filenames
    (e.g., 'Odia', 'Hindi', 'Bengali', 'English').
    """
    if not code:
        return "Original"
    code_str = str(code).strip()
    if code_str.lower() in ("auto", "unknown", "none", "auto-detected", ""):
        return "Original"
    raw_name = SUPPORTED_LANGUAGES.get(code_str, code_str)
    # Extract name before any parentheses (e.g. "English (Indian/Global)" -> "English")
    base = raw_name.split("(")[0].strip()
    cleaned = re.sub(r"[^\w\-]", "", base)
    return cleaned or "Original"


def generate_transcript_docx(
    audio_path: Path,
    output_docx_path: Path,
    title: str,
    meta_rows: list[tuple[str, str]],
    segments: list[dict[str, Any]] | None = None,
    dialogue_lines: list[str] | None = None,
    raw_text: str | None = None,
) -> Path:
    """
    Generate a clean, executive Microsoft Word (.docx) document.
    Does not include vendor watermarks, model attributions, or unwanted metadata.
    """
    if not HAS_DOCX:
        raise RuntimeError(
            "The 'python-docx' package is required to generate .docx files. "
            "Please install dependencies: pip install -r requirements.txt"
        )

    doc = docx.Document()

    # Configure clean standard 1-inch margins
    for sec in doc.sections:
        sec.top_margin = Inches(1)
        sec.bottom_margin = Inches(1)
        sec.left_margin = Inches(1)
        sec.right_margin = Inches(1)

    # Document Title
    title_p = doc.add_paragraph()
    title_p.paragraph_format.space_before = Pt(0)
    title_p.paragraph_format.space_after = Pt(4)
    title_run = title_p.add_run(title)
    title_run.font.name = "Calibri"
    title_run.font.size = Pt(20)
    title_run.font.bold = True
    title_run.font.color.rgb = RGBColor(16, 44, 87)  # Deep Navy

    # Metadata Table
    if meta_rows:
        tbl = doc.add_table(rows=len(meta_rows), cols=2)
        tbl.style = "Table Grid"
        tbl.alignment = WD_TABLE_ALIGNMENT.CENTER

        for idx, (label, val) in enumerate(meta_rows):
            row = tbl.rows[idx]
            cell_lbl = row.cells[0]
            cell_val = row.cells[1]

            p_lbl = cell_lbl.paragraphs[0]
            p_lbl.paragraph_format.space_after = Pt(2)
            r_lbl = p_lbl.add_run(label)
            r_lbl.bold = True
            r_lbl.font.size = Pt(9.5)
            r_lbl.font.color.rgb = RGBColor(50, 50, 50)

            p_val = cell_val.paragraphs[0]
            p_val.paragraph_format.space_after = Pt(2)
            r_val = p_val.add_run(val)
            r_val.font.size = Pt(9.5)

        doc.add_paragraph().paragraph_format.space_after = Pt(8)

    # Heading for content
    h = doc.add_heading("Transcript", level=1)
    h.style.font.color.rgb = RGBColor(16, 44, 87)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)

    speaker_colors = [
        RGBColor(27, 85, 155),   # Navy Blue
        RGBColor(38, 128, 90),   # Forest Green
        RGBColor(180, 85, 0),    # Warm Amber
        RGBColor(128, 40, 128),  # Plum Purple
        RGBColor(180, 40, 40),   # Crimson
    ]
    speaker_color_map: dict[str, RGBColor] = {}
    speaker_idx = 0

    turn_pattern = re.compile(
        r"^\[(?P<start>\d{2}:\d{2}:\d{2}\.\d{3})\s*-\s*(?P<end>\d{2}:\d{2}:\d{2}\.\d{3})\]\s*(?P<speaker>[^:]+):\s*(?P<text>.*)$"
    )

    if dialogue_lines:
        for line in dialogue_lines:
            line_str = line.strip()
            if not line_str:
                continue
            match = turn_pattern.match(line_str)
            p = doc.add_paragraph()
            p.paragraph_format.space_after = Pt(6)
            p.paragraph_format.line_spacing = 1.15

            if match:
                spk = match.group("speaker").strip()
                if spk not in speaker_color_map:
                    speaker_color_map[spk] = speaker_colors[speaker_idx % len(speaker_colors)]
                    speaker_idx += 1

                spk_run = p.add_run(f"{spk} ")
                spk_run.bold = True
                spk_run.font.size = Pt(10.5)
                spk_run.font.color.rgb = speaker_color_map[spk]

                ts_run = p.add_run(f"[{match.group('start')} - {match.group('end')}]\n")
                ts_run.font.size = Pt(9)
                ts_run.font.color.rgb = RGBColor(120, 120, 120)

                body_run = p.add_run(match.group("text").strip())
                body_run.font.size = Pt(11)
            else:
                body_run = p.add_run(line_str)
                body_run.font.size = Pt(11)

    elif segments:
        for seg in segments:
            p = doc.add_paragraph()
            p.paragraph_format.space_after = Pt(6)
            p.paragraph_format.line_spacing = 1.15

            spk = seg.get("speaker", "Speaker")
            if spk not in speaker_color_map:
                speaker_color_map[spk] = speaker_colors[speaker_idx % len(speaker_colors)]
                speaker_idx += 1

            spk_run = p.add_run(f"{spk} ")
            spk_run.bold = True
            spk_run.font.size = Pt(10.5)
            spk_run.font.color.rgb = speaker_color_map[spk]

            start_t = format_time(seg.get("start"))
            end_t = format_time(seg.get("end"))
            ts_run = p.add_run(f"[{start_t} - {end_t}]\n")
            ts_run.font.size = Pt(9)
            ts_run.font.color.rgb = RGBColor(120, 120, 120)

            body_run = p.add_run(seg.get("text", ""))
            body_run.font.size = Pt(11)

    elif raw_text:
        for para in raw_text.split("\n\n"):
            para_str = para.strip()
            if para_str:
                p = doc.add_paragraph()
                p.paragraph_format.space_after = Pt(6)
                p.paragraph_format.line_spacing = 1.15
                p.add_run(para_str)
    else:
        p = doc.add_paragraph()
        p.add_run("[No transcript content]").italic = True

    output_docx_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_docx_path))
    return output_docx_path


def generate_translation_docx(
    audio_path: Path,
    result_json: dict,
    translated_text: str,
    source_lang: str,
    output_docx_path: Path,
    asr_model: str = DEFAULT_MODEL,
    translation_model: str = DEFAULT_TRANSLATE_MODEL,
) -> Path:
    """
    Backwards-compatible helper: writes an English translation docx.
    """
    clean_lang = get_clean_language_name(source_lang)
    lang_display = SUPPORTED_LANGUAGES.get(source_lang, source_lang)
    meta_rows = [
        ("Source Audio File", audio_path.name),
        ("Original Language", f"{lang_display} ({source_lang})" if source_lang != clean_lang else lang_display),
        ("Translated Language", "English (en-IN)"),
        ("Export Date", time.strftime("%Y-%m-%d %H:%M:%S")),
    ]
    tr_lines = [l.strip() for l in translated_text.split("\n") if l.strip()]
    return generate_transcript_docx(
        audio_path=audio_path,
        output_docx_path=output_docx_path,
        title="Audio Transcript (English Translation)",
        meta_rows=meta_rows,
        dialogue_lines=tr_lines if tr_lines else None,
        raw_text=translated_text if not tr_lines else None,
    )


# ---------------------------------------------------------------------------
# Output Writing
# ---------------------------------------------------------------------------

def write_outputs(
    audio_path: Path,
    result_json: dict,
    output_dir: Path,
    model: str = DEFAULT_MODEL,
    mode: str = DEFAULT_MODE,
    client: SarvamAI | None = None,
    auto_translate: bool = True,
    translator: str = DEFAULT_TRANSLATOR,
    gemini_api_key: str | None = None,
    export_txt: bool = False,
    export_csv: bool = False,
    export_json: bool = False,
    translation_model: str = DEFAULT_TRANSLATE_MODEL,
) -> dict[str, Path]:
    """
    Write deliverables for a transcribed audio recording:
    Default deliverables (2 Word documents):
      1. Original language transcript docx: <audio_stem>_<Language>.docx
      2. English translation docx: <audio_stem>_English.docx
    Optional deliverables (disabled by default):
      3. Plain text transcript (.txt) via export_txt=True
      4. Timeline spreadsheet (.csv) via export_csv=True
      5. Raw JSON payload (.json) via export_json=True
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = audio_path.stem

    segments = extract_segments(result_json)
    lang_detected = result_json.get("language_code") or "auto-detected"
    clean_lang = get_clean_language_name(lang_detected)
    lang_display = SUPPORTED_LANGUAGES.get(lang_detected, lang_detected)

    # Format dialogue turns
    dialogue_lines: list[str] = []
    if segments:
        for segment in segments:
            start = format_time(segment["start"])
            end = format_time(segment["end"])
            formatted_turn = f"[{start} - {end}] {segment['speaker']}: {segment['text']}"
            dialogue_lines.append(formatted_turn)
    else:
        transcript = str(result_json.get("transcript", "")).strip()
        if transcript:
            dialogue_lines.append(transcript)

    raw_dialogue_text = "\n".join(dialogue_lines)
    generated_files: dict[str, Path] = {}

    # ---- 1. Original Language Word (.docx) Document ----
    # Naming: original file name + language + .docx
    if clean_lang.lower() == "english" or lang_detected == "en-IN":
        orig_docx_name = f"{stem}_English.docx"
    else:
        orig_docx_name = f"{stem}_{clean_lang}.docx"

    orig_docx_path = output_dir / orig_docx_name

    if HAS_DOCX:
        try:
            generate_transcript_docx(
                audio_path=audio_path,
                output_docx_path=orig_docx_path,
                title=f"Audio Transcript ({clean_lang})",
                meta_rows=[
                    ("Source Audio File", audio_path.name),
                    ("Language", f"{lang_display} ({lang_detected})" if lang_detected not in ("auto-detected", "unknown", clean_lang) else lang_display),
                    ("Export Date", time.strftime("%Y-%m-%d %H:%M:%S")),
                ],
                segments=segments if segments else None,
                raw_text=raw_dialogue_text if not segments else None,
            )
            generated_files["original_docx"] = orig_docx_path
        except Exception as docx_err:
            print(f"  [!] Original DOCX generation error for {audio_path.name}: {docx_err}")

    # ---- 2. English Translation Word (.docx) Document ----
    # Naming: original file name + English + .docx
    eng_docx_path = output_dir / f"{stem}_English.docx"
    translated_text = ""

    if auto_translate and (clean_lang.lower() != "english" and lang_detected != "en-IN"):
        if mode == "translate":
            translated_text = raw_dialogue_text
        elif raw_dialogue_text:
            try:
                translated_text, _ = translate_to_english(
                    text=raw_dialogue_text,
                    source_language_code=lang_detected,
                    translator=translator,
                    gemini_api_key=gemini_api_key,
                )
            except Exception as tr_err:
                print(f"  [!] English translation error on {audio_path.name}: {tr_err}")
                translated_text = raw_dialogue_text

        if HAS_DOCX and translated_text:
            try:
                tr_lines = [ln.strip() for ln in translated_text.split("\n") if ln.strip()]
                generate_transcript_docx(
                    audio_path=audio_path,
                    output_docx_path=eng_docx_path,
                    title="Audio Transcript (English Translation)",
                    meta_rows=[
                        ("Source Audio File", audio_path.name),
                        ("Original Language", f"{lang_display} ({lang_detected})" if lang_detected not in ("auto-detected", "unknown", clean_lang) else lang_display),
                        ("Translated Language", "English (en-IN)"),
                        ("Export Date", time.strftime("%Y-%m-%d %H:%M:%S")),
                    ],
                    dialogue_lines=tr_lines if segments else None,
                    raw_text=translated_text if not segments else None,
                )
                generated_files["english_docx"] = eng_docx_path
            except Exception as docx_err:
                print(f"  [!] English DOCX generation error for {audio_path.name}: {docx_err}")

    # ---- 3. Optional Plain Text (.txt) ----
    if export_txt:
        txt_path = output_dir / f"{stem}.txt"
        lines: list[str] = [
            f"File     : {audio_path.name}",
            f"Language : {lang_detected}",
            "",
            "TRANSCRIPT",
            "=" * 80,
            "",
        ]
        lines.extend(dialogue_lines or [str(result_json.get("transcript", ""))])
        txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        generated_files["txt"] = txt_path

    # ---- 4. Optional Spreadsheet Timeline (.csv) ----
    if export_csv:
        csv_path = output_dir / f"{stem}.csv"
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
        generated_files["csv"] = csv_path

    # ---- 5. Optional Raw JSON (.json) ----
    if export_json:
        json_path = output_dir / f"{stem}.json"
        if translated_text:
            result_json["english_translation"] = translated_text
        json_path.write_text(
            json.dumps(result_json, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        generated_files["json"] = json_path

    # Console status summary
    print(f"  ✓ {audio_path.name}")
    if "original_docx" in generated_files:
        print(f"    DOCX ({clean_lang}) : {generated_files['original_docx']}")
    if "english_docx" in generated_files:
        print(f"    DOCX (English) : {generated_files['english_docx']}")
    if "txt" in generated_files:
        print(f"    TXT            : {generated_files['txt']}")
    if "csv" in generated_files:
        print(f"    CSV            : {generated_files['csv']}")
    if "json" in generated_files:
        print(f"    JSON           : {generated_files['json']}")

    return generated_files


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
    auto_translate: bool = True,
    translator: str = DEFAULT_TRANSLATOR,
    gemini_api_key: str | None = None,
    test_clip_seconds: float | None = None,
    remove_silence: bool = False,
    preprocess: bool = False,
    output_dir: Path | None = None,
    export_txt: bool = False,
    export_csv: bool = False,
    export_json: bool = False,
    translation_model: str = DEFAULT_TRANSLATE_MODEL,
) -> dict[str, Any]:
    """
    Transcribe a single audio file with Sarvam AI.

    If diarization is disabled and the file is short, utilizes the synchronous
    REST endpoint for immediate low-latency results.
    If diarization is requested, submits a dedicated batch job to obtain
    speaker-separated dialogue turns.
    Optionally pre-processes input audio (silence removal, test clipping) to minimize costs.
    Optionally translates the transcript into English using zero-cost free translation.

    Returns the parsed result dictionary enriched with English translation.
    """
    orig_audio_path = Path(audio_path).resolve()
    if not orig_audio_path.is_file():
        raise FileNotFoundError(f"Audio file not found: {orig_audio_path}")

    # Audio optimization (clipping and/or silence removal)
    upload_audio_path = orig_audio_path
    temp_dir_obj = None

    if (test_clip_seconds and test_clip_seconds > 0) or remove_silence or preprocess:
        temp_dir_obj = tempfile.TemporaryDirectory(prefix="sarvam_stt_opt_")
        target_opt = Path(temp_dir_obj.name) / f"{orig_audio_path.stem}_opt.wav"
        proc_p, stats = optimize_audio_for_transcription(
            input_path=orig_audio_path,
            output_path=target_opt,
            test_clip_seconds=test_clip_seconds,
            remove_silence=remove_silence,
            normalize_sample_rate=True,
        )
        upload_audio_path = proc_p
        if stats.get("optimized") and stats.get("original_duration"):
            orig_d = stats.get("original_duration", 0)
            proc_d = stats.get("processed_duration", 0)
            saved_s = stats.get("duration_saved", 0)
            saved_pct = stats.get("savings_pct", 0)
            print(f"  [Cost Optimization] {orig_audio_path.name}: {orig_d:.1f}s -> {proc_d:.1f}s (Saved {saved_s:.1f}s / {saved_pct:.1f}%)")

    try:
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

        result_json: dict[str, Any] = {}

        # Path A: Fast REST API when diarization is not requested
        if not with_diarization:
            with open(upload_audio_path, "rb") as fh:
                kwargs: dict[str, Any] = {
                    "file": (orig_audio_path.name, fh),
                    "model": model,
                    "mode": mode,
                    "with_timestamps": with_timestamps,
                }
                if norm_lang:
                    kwargs["language_code"] = norm_lang
                if clean_keyterms and model == "saaras:v4":
                    kwargs["keyterms"] = clean_keyterms

                response = client.speech_to_text.transcribe(**kwargs)
                result_json = to_dict(response)

        # Path B: Batch API with Speaker Diarization
        else:
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
                job.upload_files(file_paths=[str(upload_audio_path)])
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
                    raise RuntimeError(f"Could not locate output JSON for {orig_audio_path.name}")

                result_json = json.loads(json_candidates[0].read_text(encoding="utf-8"))

        # Write deliverables to output directory if specified
        if output_dir:
            write_outputs(
                audio_path=orig_audio_path,
                result_json=result_json,
                output_dir=output_dir,
                model=model,
                mode=mode,
                client=client,
                auto_translate=auto_translate,
                translator=translator,
                gemini_api_key=gemini_api_key,
                export_txt=export_txt,
                export_csv=export_csv,
                export_json=export_json,
            )
        elif auto_translate:
            # Perform in-memory translation for callers like the Gradio web UI
            segments = extract_segments(result_json)
            if segments:
                lines = [f"[{format_time(s['start'])} - {format_time(s['end'])}] {s['speaker']}: {s['text']}" for s in segments]
                raw_text = "\n".join(lines)
            else:
                raw_text = str(result_json.get("transcript", "")).strip()

            if raw_text:
                if mode == "translate" or result_json.get("language_code") == "en-IN":
                    result_json["english_translation"] = raw_text
                else:
                    try:
                        tr_text, _ = translate_to_english(
                            text=raw_text,
                            source_language_code=result_json.get("language_code"),
                            translator=translator,
                            gemini_api_key=gemini_api_key,
                        )
                        result_json["english_translation"] = tr_text
                    except Exception:
                        result_json["english_translation"] = raw_text

        return result_json

    finally:
        if temp_dir_obj is not None:
            try:
                temp_dir_obj.cleanup()
            except Exception:
                pass


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
    auto_translate: bool = True,
    translator: str = DEFAULT_TRANSLATOR,
    gemini_api_key: str | None = None,
    test_clip_seconds: float | None = None,
    remove_silence: bool = False,
    preprocess: bool = False,
    export_txt: bool = False,
    export_csv: bool = False,
    export_json: bool = False,
    translation_model: str = DEFAULT_TRANSLATE_MODEL,
) -> tuple[int, int]:
    """
    Upload and process a batch of audio files using Sarvam's bulk job API.
    Supports audio pre-processing (silence removal, test clipping) and zero-cost translation.
    Returns (successful_count, failed_count).
    """
    print(f"=== Batch {batch_number}: {len(batch)} file(s) ===")

    successful = 0
    failed = 0

    with tempfile.TemporaryDirectory(prefix="sarvam_stt_batch_") as tmp:
        tmp_dir = Path(tmp)

        try:
            # Audio optimization if clipping or silence removal is requested
            upload_files: list[Path] = []
            upload_to_original: dict[str, Path] = {}

            if (test_clip_seconds and test_clip_seconds > 0) or remove_silence or preprocess:
                opt_dir = tmp_dir / "optimized_audio"
                opt_dir.mkdir(parents=True, exist_ok=True)
                for orig_p in batch:
                    target_p = opt_dir / f"{orig_p.stem}_opt.wav"
                    proc_p, stats = optimize_audio_for_transcription(
                        input_path=orig_p,
                        output_path=target_p,
                        test_clip_seconds=test_clip_seconds,
                        remove_silence=remove_silence,
                        normalize_sample_rate=True,
                    )
                    upload_files.append(proc_p)
                    upload_to_original[proc_p.name] = orig_p
                    upload_to_original[proc_p.stem] = orig_p
                    upload_to_original[orig_p.name] = orig_p
                    upload_to_original[orig_p.stem] = orig_p
                    if stats.get("optimized") and stats.get("original_duration"):
                        orig_d = stats.get("original_duration", 0)
                        proc_d = stats.get("processed_duration", 0)
                        saved_s = stats.get("duration_saved", 0)
                        saved_pct = stats.get("savings_pct", 0)
                        print(f"  [Cost Optimization] {orig_p.name}: {orig_d:.1f}s -> {proc_d:.1f}s (Saved {saved_s:.1f}s / {saved_pct:.1f}%)")
            else:
                upload_files = batch
                for orig_p in batch:
                    upload_to_original[orig_p.name] = orig_p
                    upload_to_original[orig_p.stem] = orig_p

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
            job.upload_files(file_paths=[str(p) for p in upload_files])

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
                matched_path = match_result_to_input(entry, upload_files, index)
                if matched_path is not None:
                    audio_path = upload_to_original.get(
                        matched_path.name,
                        upload_to_original.get(matched_path.stem, matched_path),
                    )
                else:
                    audio_path = batch[index] if index < len(batch) else None

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
                    client=client,
                    auto_translate=auto_translate,
                    translator=translator,
                    gemini_api_key=gemini_api_key,
                    export_txt=export_txt,
                    export_csv=export_csv,
                    export_json=export_json,
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
            "with speaker diarization, automatic English (en-IN) translation, "
            "and Microsoft Word (.docx) export alongside TXT, CSV, and JSON."
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
        "--no-translate",
        action="store_true",
        help="Disable automatic translation to English (en-IN) and .docx export.",
    )

    parser.add_argument(
        "--translator",
        type=str,
        choices=TRANSLATORS,
        default=DEFAULT_TRANSLATOR,
        help=(
            f"Translation engine to translate transcripts to English: "
            f"'google_free' (default; zero cost, no key required) or 'gemini' (requires API key)."
        ),
    )

    parser.add_argument(
        "--gemini-api-key",
        type=str,
        default=None,
        help="Google Gemini API key (or set GEMINI_API_KEY environment variable) when using --translator gemini.",
    )

    parser.add_argument(
        "--test-clip",
        "--sample-duration",
        dest="test_clip",
        type=float,
        default=None,
        metavar="SECONDS",
        help=(
            "Clip audio to the first N seconds (e.g. 60 or 120) before processing, "
            "allowing low-cost testing without uploading large files."
        ),
    )

    parser.add_argument(
        "--remove-silence",
        action="store_true",
        help=(
            "Pre-process input audio to strip silent pauses (below -35dB for >0.4s), "
            "reducing billable audio duration and transcription costs."
        ),
    )

    parser.add_argument(
        "--preprocess",
        action="store_true",
        help=(
            "Pre-process input audio by normalizing to standard 16kHz mono WAV "
            "and removing dead pauses."
        ),
    )

    parser.add_argument(
        "--translation-model",
        type=str,
        choices=TRANSLATE_MODELS,
        default=DEFAULT_TRANSLATE_MODEL,
        help=f"Deprecated model flag (retained for backward compatibility).",
    )

    parser.add_argument(
        "--export-txt",
        action="store_true",
        help="Also export formatted text transcript (.txt).",
    )

    parser.add_argument(
        "--export-csv",
        action="store_true",
        help="Also export chronological timeline spreadsheet (.csv).",
    )

    parser.add_argument(
        "--export-json",
        action="store_true",
        help="Also export raw JSON response payload (.json).",
    )

    parser.add_argument(
        "--all-formats",
        action="store_true",
        help="Export all formats (.docx, .txt, .csv, .json).",
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
    translate_to_english = not args.no_translate
    export_txt = args.export_txt or args.all_formats
    export_csv = args.export_csv or args.all_formats
    export_json = args.export_json or args.all_formats

    print("=======================================================")
    print(" Sarvam AI Speech-to-Text Transcription & Translation")
    print(f" ASR Model    : {args.model}")
    print(f" ASR Mode     : {args.mode}")
    print(f" Language     : {args.language_code or 'auto-detection'}")
    print(f" Diarization  : {'enabled' if with_diarization else 'disabled'}")
    if with_diarization:
        spk_label = str(args.speakers) if args.speakers is not None else "automatic detection"
        print(f" Speakers     : {spk_label}")
    if keyterm_list:
        print(f" Keyterms     : {len(keyterm_list)} term(s) loaded")
    if args.test_clip:
        print(f" Test Clip    : First {args.test_clip:.1f}s (Low-cost testing mode)")
    if args.remove_silence:
        print(" Silence Cut  : Enabled (Dead pauses stripped for cost reduction)")
    if args.preprocess:
        print(" Preprocess   : Enabled (16kHz mono audio normalization)")
    print(" Output DOCX  : 2 files (<name>_<Lang>.docx & <name>_English.docx)")
    if translate_to_english:
        tr_label = "Google Free (Zero API cost)" if args.translator == "google_free" else "Google Gemini"
        print(f" Translator   : {tr_label}")
    extra_fmts = []
    if export_txt:
        extra_fmts.append("TXT")
    if export_csv:
        extra_fmts.append("CSV")
    if export_json:
        extra_fmts.append("JSON")
    if extra_fmts:
        print(f" Extra Output : {', '.join(extra_fmts)}")
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
            auto_translate=translate_to_english,
            translator=args.translator,
            gemini_api_key=args.gemini_api_key,
            test_clip_seconds=args.test_clip,
            remove_silence=args.remove_silence,
            preprocess=args.preprocess,
            export_txt=export_txt,
            export_csv=export_csv,
            export_json=export_json,
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