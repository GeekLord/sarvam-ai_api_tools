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
- Automatic English Translation & Word (.docx) Generation:
  * Translates the transcribed text into English (``en-IN``) using Sarvam Translation
    (model ``sarvam-translate:v1`` or ``mayura:v1``).
  * Automatically creates a Microsoft Word (``.docx``) document with the exact same
    file stem as the input audio file in the output folder.
  * Includes clean document layout: executive metadata table, English translation with
    distinct colored speaker dialogue turns & timestamps, and the original native transcript.
- Speaker Diarization: identify who spoke when, either with automatic detection
  or constrained to a known number of speakers (1-20 speakers).
- Domain Keyterms Biasing: provide up to 50 custom domain names, brand terms,
  or technical words to bias recognition in ``saaras:v4``.
- Multi-format deliverables per audio recording:
  1. Microsoft Word Document (``.docx``) with English translation and original transcript.
  2. Formatted human-readable dialogue transcript with timestamps (``.txt``).
  3. Chronological timeline spreadsheet (``.csv``).
  4. Raw API response payload with English translation metadata (``.json``).
- Reusable single-item worker: exposes ``transcribe_single_audio()`` for in-process
  import by web interfaces (e.g., Gradio in ``app.py``) or automated pipelines.
"""

import argparse
import csv
import json
import os
import random
import re
import sys
import tempfile
import time
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
# English Translation Helpers
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


def translate_to_english(
    client: SarvamAI,
    text: str,
    source_language_code: str | None = None,
    model: str = DEFAULT_TRANSLATE_MODEL,
    max_retries: int = 5,
    initial_backoff: float = 3.0,
) -> tuple[str, str]:
    """
    Translate text into English (en-IN) using Sarvam AI Translation API.
    Handles text chunking to respect model character limits and retries on 429.

    Returns: (translated_text, source_language_code_used)
    """
    text = text.strip()
    if not text:
        return "", source_language_code or "en-IN"

    # Resolve source language
    src_lang = source_language_code
    if not src_lang or src_lang.lower() in ("auto", "unknown", "none"):
        # Attempt to identify the language using Sarvam language identification
        try:
            sample = text[:500]
            ident = client.text.identify_language(input=sample)
            src_lang = getattr(ident, "language_code", None) or "hi-IN"
        except Exception:
            src_lang = None

    # If already English, return directly
    if src_lang == "en-IN":
        return text, "en-IN"

    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    if not lines:
        lines = [text]

    chunks = chunk_dialogue_lines(lines, max_chars=1800)
    translated_parts: list[str] = []

    for index, chunk in enumerate(chunks, start=1):
        for attempt in range(max_retries):
            try:
                kwargs: dict[str, Any] = {
                    "input": chunk,
                    "target_language_code": "en-IN",
                }
                if src_lang:
                    kwargs["source_language_code"] = src_lang
                    kwargs["model"] = model
                else:
                    kwargs["source_language_code"] = "auto"
                    kwargs["model"] = "mayura:v1"

                res = client.text.translate(**kwargs)
                tr_text = getattr(res, "translated_text", None)
                if tr_text is None and isinstance(res, dict):
                    tr_text = res.get("translated_text")
                translated_parts.append(str(tr_text or chunk))
                break
            except Exception as exc:
                err_str = str(exc)
                is_rate_limit = "429" in err_str or "rate limit" in err_str.lower()
                if is_rate_limit and attempt < max_retries - 1:
                    wait_time = initial_backoff * (2 ** attempt) + random.uniform(0.5, 2.0)
                    time.sleep(wait_time)
                    continue

                # If sarvam-translate failed due to language code, try mayura auto
                if "Invalid language code" in err_str and kwargs.get("model") != "mayura:v1":
                    try:
                        res = client.text.translate(
                            input=chunk,
                            source_language_code="auto",
                            target_language_code="en-IN",
                            model="mayura:v1",
                        )
                        tr_text = getattr(res, "translated_text", None) or ""
                        translated_parts.append(str(tr_text))
                        src_lang = getattr(res, "source_language_code", None) or "auto"
                        break
                    except Exception:
                        pass

                print(f"    [!] Translation warning on chunk {index}: {exc}")
                translated_parts.append(chunk)  # preserve original text on failure
                break

    full_translated = "\n".join(translated_parts)
    return full_translated, src_lang or "unknown"


# ---------------------------------------------------------------------------
# DOCX Document Generation
# ---------------------------------------------------------------------------

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
    Generate a professional Microsoft Word (.docx) document containing:
    1. Header & Document Title
    2. Executive Metadata Summary Table
    3. English Translated Transcript (with distinct styled speaker dialogue & timestamps)
    4. Original Native Transcript (for reference)

    Saved with the same input file name in the output directory.
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
    title_p.paragraph_format.space_after = Pt(2)
    title_run = title_p.add_run("Audio Transcription & English Translation")
    title_run.font.name = "Calibri"
    title_run.font.size = Pt(22)
    title_run.font.bold = True
    title_run.font.color.rgb = RGBColor(16, 44, 87)  # Deep Navy

    # Subtitle
    sub_p = doc.add_paragraph()
    sub_p.paragraph_format.space_after = Pt(12)
    sub_run = sub_p.add_run("Generated by Sarvam AI Speech-to-Text Suite")
    sub_run.font.name = "Calibri"
    sub_run.font.size = Pt(10)
    sub_run.font.italic = True
    sub_run.font.color.rgb = RGBColor(110, 110, 110)

    # Metadata Table
    meta_rows = [
        ("Source Audio File", audio_path.name),
        ("Spoken Language", f"{SUPPORTED_LANGUAGES.get(source_lang, source_lang)} ({source_lang})"),
        ("Target Language", "English (en-IN)"),
        ("ASR Speech Model", asr_model),
        ("Translation Model", translation_model),
        ("Export Timestamp", time.strftime("%Y-%m-%d %H:%M:%S")),
    ]

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

    doc.add_paragraph().paragraph_format.space_after = Pt(10)

    # ---------------- Section 1: English Translation ----------------
    h1 = doc.add_heading("English Translation", level=1)
    h1.style.font.color.rgb = RGBColor(16, 44, 87)
    doc.add_paragraph().paragraph_format.space_after = Pt(4)

    # Palette of complementary speaker colors
    speaker_colors = [
        RGBColor(27, 85, 155),   # Navy Blue
        RGBColor(38, 128, 90),   # Forest Green
        RGBColor(180, 85, 0),    # Warm Amber
        RGBColor(128, 40, 128),  # Plum Purple
        RGBColor(180, 40, 40),   # Crimson
    ]

    turn_pattern = re.compile(
        r"^\[(?P<start>\d{2}:\d{2}:\d{2}\.\d{3})\s*-\s*(?P<end>\d{2}:\d{2}:\d{2}\.\d{3})\]\s*(?P<speaker>[^:]+):\s*(?P<text>.*)$"
    )

    tr_lines = [l.strip() for l in translated_text.split("\n") if l.strip()]
    speaker_color_map: dict[str, RGBColor] = {}
    speaker_idx = 0

    if tr_lines:
        for line in tr_lines:
            match = turn_pattern.match(line)
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
                body_run = p.add_run(line)
                body_run.font.size = Pt(11)
    else:
        p = doc.add_paragraph()
        p.add_run("[No translation content generated]").italic = True

    # ---------------- Section 2: Original Native Transcript ----------------
    doc.add_paragraph().paragraph_format.space_after = Pt(14)
    src_label = SUPPORTED_LANGUAGES.get(source_lang, source_lang)
    h2 = doc.add_heading(f"Original Transcript ({src_label})", level=1)
    h2.style.font.color.rgb = RGBColor(80, 80, 80)
    doc.add_paragraph().paragraph_format.space_after = Pt(4)

    segments = extract_segments(result_json)
    if segments:
        for seg in segments:
            p = doc.add_paragraph()
            p.paragraph_format.space_after = Pt(5)
            p.paragraph_format.line_spacing = 1.15

            spk = seg.get("speaker", "Speaker")
            if spk not in speaker_color_map:
                speaker_color_map[spk] = speaker_colors[speaker_idx % len(speaker_colors)]
                speaker_idx += 1

            spk_run = p.add_run(f"{spk} ")
            spk_run.bold = True
            spk_run.font.size = Pt(10)
            spk_run.font.color.rgb = speaker_color_map[spk]

            start_t = format_time(seg.get("start"))
            end_t = format_time(seg.get("end"))
            ts_run = p.add_run(f"[{start_t} - {end_t}]\n")
            ts_run.font.size = Pt(9)
            ts_run.font.color.rgb = RGBColor(120, 120, 120)

            body_run = p.add_run(seg.get("text", ""))
            body_run.font.size = Pt(10.5)
    else:
        orig_text = str(result_json.get("transcript", "")).strip()
        p = doc.add_paragraph()
        p.paragraph_format.line_spacing = 1.15
        p.add_run(orig_text or "[No original transcript returned]")

    # Ensure parent output directory exists and save document
    output_docx_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_docx_path))
    return output_docx_path


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
    translate_to_english: bool = True,
    translation_model: str = DEFAULT_TRANSLATE_MODEL,
) -> tuple[Path, Path, Path, Path | None]:
    """
    Write deliverables for a transcribed audio recording:
    1. Human-readable dialogue transcript with timestamps (.txt)
    2. Chronological timeline spreadsheet (.csv)
    3. Raw JSON response payload (.json)
    4. Automatically translated Microsoft Word document (.docx)
    """
    base = output_dir / audio_path.stem
    txt_path = base.with_suffix(".txt")
    json_path = base.with_suffix(".json")
    csv_path = base.with_suffix(".csv")
    docx_path = base.with_suffix(".docx")

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

    dialogue_lines: list[str] = []
    if segments:
        for segment in segments:
            start = format_time(segment["start"])
            end = format_time(segment["end"])
            formatted_turn = f"[{start} - {end}] {segment['speaker']}: {segment['text']}"
            lines.append(formatted_turn)
            dialogue_lines.append(formatted_turn)
    else:
        transcript = str(result_json.get("transcript", "")).strip()
        lines.append(transcript or "[No transcript returned]")
        if transcript:
            dialogue_lines.append(transcript)

    raw_dialogue_text = "\n".join(dialogue_lines)
    txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ---- 2. Automatic English Translation & Word (.docx) Export ----
    translated_text = ""
    saved_docx: Path | None = None

    if translate_to_english and client is not None and raw_dialogue_text:
        # If ASR mode was already 'translate', Saaras already translated the speech to English
        if mode == "translate" or lang_detected == "en-IN":
            translated_text = raw_dialogue_text
            src_lang_code = lang_detected
        else:
            try:
                translated_text, src_lang_code = translate_to_english(
                    client=client,
                    text=raw_dialogue_text,
                    source_language_code=lang_detected,
                    model=translation_model,
                )
            except Exception as tr_err:
                print(f"  [!] English translation error on {audio_path.name}: {tr_err}")
                translated_text = raw_dialogue_text
                src_lang_code = lang_detected

        if HAS_DOCX and translated_text:
            try:
                saved_docx = generate_translation_docx(
                    audio_path=audio_path,
                    result_json=result_json,
                    translated_text=translated_text,
                    source_lang=src_lang_code,
                    output_docx_path=docx_path,
                    asr_model=model,
                    translation_model=translation_model if mode != "translate" else "saaras-native-translate",
                )
            except Exception as docx_err:
                print(f"  [!] DOCX generation error for {audio_path.name}: {docx_err}")

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

    # ---- 4. Raw JSON (.json) enriched with translation metadata ----
    if "model" not in result_json:
        result_json["model"] = model
    if "mode" not in result_json:
        result_json["mode"] = mode
    if translated_text:
        result_json["english_translation"] = translated_text
    if saved_docx:
        result_json["docx_file"] = saved_docx.name

    json_path.write_text(
        json.dumps(result_json, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"  ✓ {audio_path.name}")
    print(f"    TXT  : {txt_path}")
    print(f"    CSV  : {csv_path}")
    print(f"    JSON : {json_path}")
    if saved_docx:
        print(f"    DOCX : {saved_docx}")

    return txt_path, csv_path, json_path, saved_docx


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
    translate_to_english: bool = True,
    translation_model: str = DEFAULT_TRANSLATE_MODEL,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """
    Transcribe a single audio file with Sarvam AI.

    If diarization is disabled and the file is short, utilizes the synchronous
    REST endpoint for immediate low-latency results.
    If diarization is requested, submits a dedicated batch job to obtain
    speaker-separated dialogue turns.
    Optionally translates the transcript into English and generates a .docx file.

    Returns the parsed result dictionary enriched with English translation.
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

    result_json: dict[str, Any] = {}

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

    # Write deliverables to output directory if specified
    if output_dir:
        write_outputs(
            audio_path=audio_path,
            result_json=result_json,
            output_dir=output_dir,
            model=model,
            mode=mode,
            client=client,
            translate_to_english=translate_to_english,
            translation_model=translation_model,
        )
    elif translate_to_english:
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
                        client=client,
                        text=raw_text,
                        source_language_code=result_json.get("language_code"),
                        model=translation_model,
                    )
                    result_json["english_translation"] = tr_text
                except Exception:
                    result_json["english_translation"] = raw_text

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
    translate_to_english: bool = True,
    translation_model: str = DEFAULT_TRANSLATE_MODEL,
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
                    client=client,
                    translate_to_english=translate_to_english,
                    translation_model=translation_model,
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
        "--translation-model",
        type=str,
        choices=TRANSLATE_MODELS,
        default=DEFAULT_TRANSLATE_MODEL,
        help=f"Model for English translation (default: {DEFAULT_TRANSLATE_MODEL}).",
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
    print(f" Translation  : {'English (en-IN) + .docx export' if translate_to_english else 'disabled'}")
    if translate_to_english:
        print(f" Trans. Model : {args.translation_model}")
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
            translate_to_english=translate_to_english,
            translation_model=args.translation_model,
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