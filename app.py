"""
Sarvam AI Tools - Gradio Web Front End
======================================
An interactive web UI for testing the Sarvam AI command-line tools in this
suite without touching the terminal. It honors the "one tool = one script"
philosophy by IMPORTING each tool script's module-level worker functions and
constants and calling them in-process.

Wired tabs:
- Speech-to-Text  -> transcribe_sarvam.transcribe_single_audio
- Text-to-Speech  -> sarvam_tts.synthesize_unit
- Translate       -> sarvam_translate.translate_unit
- Document OCR    -> sarvam_doc_ocr.digitise_document

The Image Renamer (sarvam_image_renamer.py) is a batch/folder oriented CLI tool
with rollback history and runs directly from the terminal.

API key handling: the UI provides a password field. When it is left blank the
resolver falls back to get_api_key(None, target_dir), which reads
SARVAM_API_KEY from the environment or a local .env file. The key value is
never logged or echoed. A SarvamAI client is instantiated per call.

Outputs are written to temporary files; user inputs are never mutated.
"""

from __future__ import annotations

import os
import tempfile
import time
import types
from pathlib import Path

# Guard the gradio import so the module can be inspected (and give a friendly
# hint) even when gradio is not installed.
try:
    import gradio as gr

    HAS_GRADIO = True
    GRADIO_IMPORT_ERROR: ImportError | None = None
except ImportError as exc:
    gr = None  # type: ignore[assignment]
    HAS_GRADIO = False
    GRADIO_IMPORT_ERROR = exc


def _gradio_error_detail() -> str:
    """
    Build an actionable message describing why gradio could not be imported.

    Surfaces the real underlying ImportError instead of assuming gradio itself
    is simply not installed, so a transitive failure like a missing audioop backport
    on Python 3.13 is not masked.
    """
    base = (
        "The 'gradio' package (or one of its dependencies) failed to import. "
        "Install dependencies with: pip install -r requirements.txt"
    )
    exc = GRADIO_IMPORT_ERROR
    if exc is None:
        return base
    missing = getattr(exc, "name", None)
    offending = f" (missing module: {missing})" if missing else ""
    return f"{base}\nUnderlying import error{offending}: {exc}"


# Sarvam AI SDK (instantiated per call). Guarded like the tool scripts so
# importing app.py never crashes on a missing dependency.
try:
    from sarvamai import SarvamAI

    HAS_SARVAM = True
except ImportError:
    SarvamAI = None  # type: ignore[assignment]
    HAS_SARVAM = False

# Import each tool module under an alias to avoid colliding constant names
# (each defines its own SUPPORTED_LANGUAGES / MODELS / get_api_key, etc.).
import sarvam_doc_ocr as ocr_tool
import sarvam_translate as translate_tool
import sarvam_tts as tts_tool
import transcribe_sarvam as stt_tool

# Directory used for the .env fallback when the key field is blank.
TARGET_DIR = Path(__file__).resolve().parent

# UI-facing OCR poll timeout (seconds). Cap lower than CLI DEFAULT_TIMEOUT (600s)
# so a slow document does not tie up a web worker for 10 minutes.
OCR_UI_TIMEOUT = 120.0

# Guidance shown when no API key can be resolved (mirrors the CLI wording).
_MISSING_KEY_MESSAGE = (
    "Sarvam AI API subscription key was not found. Provide it via one of:\n"
    "  1. The 'Sarvam API key' field above.\n"
    "  2. Environment variable: export SARVAM_API_KEY='your-api-key'.\n"
    "  3. .env file: copy .env.sample to .env and set SARVAM_API_KEY."
)


def _blank_to_none(value: str | None) -> str | None:
    """Normalize an empty/whitespace UI string to None."""
    if value is None:
        return None
    value = value.strip()
    return value or None


def _resolve_client(api_key_field: str | None) -> SarvamAI:
    """
    Resolve the API key (UI field first, then env/.env via get_api_key) and
    return a fresh SarvamAI client. Raises gr.Error on any missing prerequisite
    so a bad key never crashes the server callback.
    """
    if not HAS_SARVAM:
        raise gr.Error(
            "The 'sarvamai' package is required. Install dependencies with "
            "pip install -r requirements.txt."
        )

    key = _blank_to_none(api_key_field)
    if not key:
        # Fall back to environment/.env. Never echo the resolved value.
        key = translate_tool.get_api_key(None, TARGET_DIR)
    if not key:
        raise gr.Error(_MISSING_KEY_MESSAGE)

    return SarvamAI(api_subscription_key=key)


# ---------------------------------------------------------------------------
# Speech-to-Text (Transcription & Diarization)
# ---------------------------------------------------------------------------
def run_stt(
    api_key: str | None,
    audio_file,
    model: str,
    mode: str,
    language_code: str,
    with_diarization: bool,
    num_speakers: float | None,
    keyterms: str | None,
    auto_translate: bool = True,
    translator: str = "google_free",
    gemini_api_key: str | None = None,
    test_clip_seconds: float = 0,
    remove_silence: bool = True,
    translation_model: str = stt_tool.DEFAULT_TRANSLATE_MODEL,
) -> tuple[str, str, str, str, list[str] | None]:
    """
    Callback for the Speech-to-Text tab.
    Transcribes audio, diarizes speakers, translates to English (en-IN) using
    zero-cost translation, and exports clean formatted Microsoft Word (.docx) deliverables.

    Returns:
        (transcript, english_translation, diarized_timeline, detected_language, docx_paths)
    """
    if not audio_file:
        raise gr.Error("Please upload or record an audio file to transcribe.")

    audio_path = getattr(audio_file, "name", audio_file)
    path = Path(audio_path)
    if not path.is_file():
        raise gr.Error("The provided audio file could not be read.")

    client = _resolve_client(api_key)

    # Parse keyterms (up to 50 terms, comma-separated)
    keyterm_list: list[str] | None = None
    if keyterms and keyterms.strip():
        keyterm_list = [k.strip() for k in keyterms.split(",") if k.strip()][:50]

    # Parse speaker count
    spk_count: int | None = None
    if with_diarization and num_speakers is not None and int(num_speakers) > 0:
        spk_count = int(num_speakers)

    # Language code normalization
    lang = None
    if language_code and language_code.lower() not in ("auto", "unknown", "none", ""):
        lang = language_code

    clip_sec = float(test_clip_seconds) if test_clip_seconds and float(test_clip_seconds) > 0 else None

    try:
        result = stt_tool.transcribe_single_audio(
            client=client,
            audio_path=path,
            model=model,
            mode=mode,
            language_code=lang,
            with_diarization=with_diarization,
            num_speakers=spk_count,
            keyterms=keyterm_list,
            with_timestamps=True,
            auto_translate=auto_translate,
            translator=translator,
            gemini_api_key=_blank_to_none(gemini_api_key),
            test_clip_seconds=clip_sec,
            remove_silence=remove_silence,
        )
    except Exception as exc:
        raise gr.Error(str(exc)) from exc

    transcript = str(result.get("transcript", "")).strip()
    detected_lang = str(result.get("language_code", "auto-detected"))

    # Format segments if diarization or timestamps are present
    segments = stt_tool.extract_segments(result)
    timeline_lines: list[str] = []
    if segments:
        for seg in segments:
            start_str = stt_tool.format_time(seg.get("start"))
            end_str = stt_tool.format_time(seg.get("end"))
            spk = seg.get("speaker", "Speaker")
            text_part = seg.get("text", "")
            timeline_lines.append(f"[{start_str} - {end_str}] {spk}: {text_part}")
        timeline_text = "\n".join(timeline_lines)
    else:
        timeline_text = "[Full transcript without speaker turns shown above]"

    # English translation & DOCX generation
    english_translation = ""
    docx_file_paths: list[str] = []

    if auto_translate:
        text_to_translate = timeline_text if (segments and timeline_lines) else transcript
        if mode == "translate":
            # ASR was run in translate mode directly yielding English
            english_translation = text_to_translate
        else:
            try:
                src_code = (
                    lang
                    if (lang and lang != "auto")
                    else (
                        detected_lang
                        if detected_lang not in ("auto-detected", "unknown")
                        else None
                    )
                )
                english_translation, _ = stt_tool.translate_to_english(
                    text=text_to_translate,
                    source_language_code=src_code,
                    translator=translator,
                    gemini_api_key=_blank_to_none(gemini_api_key),
                )
            except Exception as tr_err:
                english_translation = f"[Translation note: {tr_err}]"

        # Generate 2 .docx documents in a temporary directory
        if getattr(stt_tool, "HAS_DOCX", False):
            temp_dir = Path(tempfile.mkdtemp(prefix="sarvam_stt_"))
            clean_lang = stt_tool.get_clean_language_name(detected_lang)
            lang_display = str(stt_tool.SUPPORTED_LANGUAGES.get(detected_lang, detected_lang))

            # 1. Original Language DOCX: <stem>_<Language>.docx
            if clean_lang.lower() == "english" or detected_lang == "en-IN":
                orig_docx_name = f"{path.stem}_English.docx"
            else:
                orig_docx_name = f"{path.stem}_{clean_lang}.docx"

            orig_docx_path = temp_dir / orig_docx_name
            try:
                stt_tool.generate_transcript_docx(
                    audio_path=path,
                    output_docx_path=orig_docx_path,
                    title=f"Audio Transcript ({clean_lang})",
                    meta_rows=[
                        ("Source Audio File", path.name),
                        ("Language", f"{lang_display} ({detected_lang})" if detected_lang not in ("auto-detected", "unknown", clean_lang) else lang_display),
                        ("Export Date", time.strftime("%Y-%m-%d %H:%M:%S")),
                    ],
                    segments=segments if segments else None,
                    raw_text=transcript if not segments else None,
                )
                docx_file_paths.append(str(orig_docx_path))
            except Exception as docx_err:
                print(f"[!] Warning: failed to generate original .docx in web UI: {docx_err}")

            # 2. English Translation DOCX: <stem>_English.docx (if original is not English)
            if clean_lang.lower() != "english" and detected_lang != "en-IN" and english_translation:
                eng_docx_name = f"{path.stem}_English.docx"
                eng_docx_path = temp_dir / eng_docx_name
                try:
                    tr_lines = [ln.strip() for ln in english_translation.split("\n") if ln.strip()]
                    stt_tool.generate_transcript_docx(
                        audio_path=path,
                        output_docx_path=eng_docx_path,
                        title="Audio Transcript (English Translation)",
                        meta_rows=[
                            ("Source Audio File", path.name),
                            ("Original Language", f"{lang_display} ({detected_lang})" if detected_lang not in ("auto-detected", "unknown", clean_lang) else lang_display),
                            ("Translated Language", "English (en-IN)"),
                            ("Export Date", time.strftime("%Y-%m-%d %H:%M:%S")),
                        ],
                        dialogue_lines=tr_lines if segments else None,
                        raw_text=english_translation if not segments else None,
                    )
                    docx_file_paths.append(str(eng_docx_path))
                except Exception as docx_err:
                    print(f"[!] Warning: failed to generate english .docx in web UI: {docx_err}")
    else:
        english_translation = "[Translation disabled via option]"

    return (
        transcript or "[No transcript returned]",
        english_translation,
        timeline_text,
        detected_lang,
        docx_file_paths if docx_file_paths else None,
    )


# ---------------------------------------------------------------------------
# Translate
# ---------------------------------------------------------------------------
def run_translate(
    api_key: str | None,
    text: str,
    source: str,
    target: str,
    model: str,
    mode: str | None,
    output_script: str | None,
    numerals_format: str | None,
    speaker_gender: str | None,
) -> tuple[str, str]:
    """Callback for the Translate tab. Returns (translated_text, source_lang)."""
    if not _blank_to_none(text):
        raise gr.Error("Please enter some text to translate.")
    if not _blank_to_none(target):
        raise gr.Error("Please choose a target language.")

    client = _resolve_client(api_key)

    args = types.SimpleNamespace(
        source=source or "auto",
        target=target,
        model=model,
        mode=_blank_to_none(mode),
        output_script=_blank_to_none(output_script),
        numerals_format=_blank_to_none(numerals_format),
        speaker_gender=_blank_to_none(speaker_gender),
        delay=0.0,
        max_retries=translate_tool.DEFAULT_MAX_RETRIES,
    )

    limit = translate_tool.char_limit_for_model(model)
    # Reset in-memory cache per call to avoid cross-request accumulation
    translate_tool.STATE.cache = {}
    try:
        result = translate_tool.translate_unit(client, text, "gradio", args, limit)
    except Exception as exc:
        raise gr.Error(str(exc)) from exc
    finally:
        translate_tool.STATE.cache = {}

    detected = str(result.get("source_language_code") or args.source)
    detected_label = translate_tool.SUPPORTED_LANGUAGES.get(detected, detected)
    return (
        str(result.get("translated_text") or ""),
        f"{detected_label} ({detected})",
    )


# ---------------------------------------------------------------------------
# Text-to-Speech
# ---------------------------------------------------------------------------
def _speaker_choices_for(model: str) -> list[str]:
    return list(tts_tool.speakers_for_model(model))


def update_tts_speakers(model: str):
    """Refresh the speaker dropdown when the model changes."""
    choices = _speaker_choices_for(model)
    default = tts_tool.default_speaker_for_model(model)
    return gr.update(choices=choices, value=default)


def run_tts(
    api_key: str | None,
    text: str,
    language: str,
    model: str,
    speaker: str | None,
    sample_rate: int,
    codec: str,
    pace: float,
    temperature: float | None,
    pitch: float | None,
    loudness: float | None,
    enable_preprocessing: bool,
    dict_id: str | None,
    enable_cached_responses: bool,
) -> str:
    """Callback for the TTS tab. Returns a path to the generated audio file."""
    if not _blank_to_none(text):
        raise gr.Error("Please enter some text to synthesize.")

    client = _resolve_client(api_key)

    args = types.SimpleNamespace(
        language=language,
        speaker=_blank_to_none(speaker),
        model=model,
        pace=pace,
        pitch=pitch,
        loudness=loudness,
        temperature=temperature,
        sample_rate=int(sample_rate),
        codec=codec,
        enable_preprocessing=bool(enable_preprocessing),
        dict_id=_blank_to_none(dict_id),
        enable_cached_responses=bool(enable_cached_responses),
        list_speakers=False,
        delay=0.0,
        max_retries=tts_tool.DEFAULT_MAX_RETRIES,
    )

    # validate_args normalizes speaker/model-specific options in-place
    error = tts_tool.validate_args(args)
    if error:
        raise gr.Error(error)

    limit = tts_tool.char_limit_for_model(model)
    try:
        result = tts_tool.synthesize_unit(client, text, "gradio", args, limit)
    except Exception as exc:
        raise gr.Error(str(exc)) from exc

    audio_bytes = result.get("audio_bytes") or b""
    if not audio_bytes:
        raise gr.Error("No audio was returned by the API.")

    # Write to a tempfile so playback/download works; delete=False keeps it for Gradio
    ext = tts_tool.CODEC_EXTENSIONS.get(args.codec, ".wav")
    fd, tmp_path = tempfile.mkstemp(prefix="sarvam_tts_", suffix=ext)
    with os.fdopen(fd, "wb") as fh:
        fh.write(audio_bytes)
    return tmp_path


# ---------------------------------------------------------------------------
# Document OCR
# ---------------------------------------------------------------------------
def run_ocr(
    api_key: str | None,
    file_obj,
    language: str,
    output_format: str,
    content_type: str,
    model: str | None,
) -> tuple[str, str]:
    """Callback for the OCR tab. Returns (rendered_body, summary)."""
    if not file_obj:
        raise gr.Error("Please upload a document to digitize.")

    upload_path = getattr(file_obj, "name", file_obj)
    path = Path(upload_path)
    if not path.is_file():
        raise gr.Error("The uploaded file could not be read.")

    client = _resolve_client(api_key)

    args = types.SimpleNamespace(
        language=_blank_to_none(language) or ocr_tool.DEFAULT_LANGUAGE,
        output_format=output_format,
        content_type=content_type,
        model=_blank_to_none(model),
        delay=ocr_tool.DEFAULT_DELAY,
        max_retries=ocr_tool.DEFAULT_MAX_RETRIES,
        timeout=OCR_UI_TIMEOUT,
    )

    try:
        payload = ocr_tool.digitise_document(client, path, args)
    except Exception as exc:
        raise gr.Error(str(exc)) from exc

    body = str(payload.get("body") or "[No text extracted]")
    page_count = int(payload.get("page_count") or 0)
    job_id = payload.get("job_id") or "n/a"
    summary = f"Pages: {page_count} | Format: {output_format} | Job ID: {job_id}"
    return body, summary


# ---------------------------------------------------------------------------
# UI Construction
# ---------------------------------------------------------------------------
def build_ui():
    """Construct and return the Gradio Blocks application."""
    if not HAS_GRADIO:
        raise RuntimeError(_gradio_error_detail()) from GRADIO_IMPORT_ERROR

    stt_langs = ["auto"] + [k for k in sorted(stt_tool.SUPPORTED_LANGUAGES) if k != "unknown"]
    translate_langs = ["auto"] + sorted(translate_tool.SUPPORTED_LANGUAGES)
    translate_targets = sorted(translate_tool.SUPPORTED_LANGUAGES)
    tts_langs = sorted(tts_tool.SUPPORTED_LANGUAGES)

    with gr.Blocks(title="Sarvam AI Tools") as demo:
        gr.Markdown(
            "# Sarvam AI Tools Suite\n"
            "Interactively test the Sarvam AI command-line tools. Enter your "
            "Sarvam API key below (or leave it blank to use `SARVAM_API_KEY` "
            "from your environment or a local `.env` file)."
        )
        api_key = gr.Textbox(
            label="Sarvam API key",
            placeholder="Leave blank to use SARVAM_API_KEY from env / .env",
            type="password",
        )

        with gr.Tabs():
            # -------------------------- Speech-to-Text ---------------------
            with gr.Tab("Speech-to-Text"):
                with gr.Row():
                    stt_audio = gr.Audio(
                        label="Audio recording (Upload file or Record microphone)",
                        sources=["upload", "microphone"],
                        type="filepath",
                    )
                with gr.Row():
                    stt_model = gr.Dropdown(
                        label="Model",
                        choices=list(stt_tool.MODELS),
                        value=stt_tool.DEFAULT_MODEL,
                    )
                    stt_mode = gr.Dropdown(
                        label="Mode",
                        choices=list(stt_tool.MODES),
                        value=stt_tool.DEFAULT_MODE,
                    )
                    stt_language = gr.Dropdown(
                        label="Audio Language",
                        choices=stt_langs,
                        value="auto",
                    )
                with gr.Row():
                    stt_diarize = gr.Checkbox(
                        label="Enable Speaker Diarization",
                        value=True,
                    )
                    stt_speakers = gr.Number(
                        label="Expected Speakers (optional, 1-20)",
                        value=None,
                        precision=0,
                    )
                    stt_keyterms = gr.Textbox(
                        label="Custom Keyterms (saaras:v4 only, comma-separated)",
                        placeholder="e.g. Sarvam, New Delhi, Vistaar",
                    )
                with gr.Row():
                    stt_test_clip = gr.Number(
                        label="Test Clip Duration (sec, 0 = Full Audio)",
                        value=0,
                        precision=0,
                        minimum=0,
                        info="Clip audio to first N seconds (e.g. 60 or 120s) for low-cost testing.",
                    )
                    stt_remove_silence = gr.Checkbox(
                        label="Remove Silences & Pauses",
                        value=True,
                        info="Strips silence to reduce billable audio duration.",
                    )
                with gr.Row():
                    stt_auto_translate = gr.Checkbox(
                        label="Automatically translate to English (en-IN) & export .docx",
                        value=True,
                    )
                    stt_translator = gr.Dropdown(
                        label="Translation Engine (Zero Sarvam Cost)",
                        choices=[
                            ("Google Free (Zero Cost / No Key)", "google_free"),
                            ("Google Gemini (Requires API Key)", "gemini"),
                        ],
                        value="google_free",
                    )
                    stt_gemini_key = gr.Textbox(
                        label="Gemini API Key (optional, if using Gemini)",
                        type="password",
                        placeholder="Leave blank to use GEMINI_API_KEY from env",
                    )
                stt_button = gr.Button("Transcribe & Translate Audio", variant="primary")
                with gr.Row():
                    stt_transcript = gr.Textbox(label="Original Transcript (Native)", lines=6)
                    stt_english = gr.Textbox(label="English Translation (en-IN)", lines=6)
                with gr.Row():
                    stt_timeline = gr.Textbox(label="Speaker Diarization / Dialogue Timeline", lines=6)
                    stt_docx_file = gr.File(label="Download Formatted Word Documents (.docx)", file_count="multiple")
                stt_detected = gr.Textbox(label="Detected Language Code")

                stt_button.click(
                    run_stt,
                    inputs=[
                        api_key,
                        stt_audio,
                        stt_model,
                        stt_mode,
                        stt_language,
                        stt_diarize,
                        stt_speakers,
                        stt_keyterms,
                        stt_auto_translate,
                        stt_translator,
                        stt_gemini_key,
                        stt_test_clip,
                        stt_remove_silence,
                    ],
                    outputs=[
                        stt_transcript,
                        stt_english,
                        stt_timeline,
                        stt_detected,
                        stt_docx_file,
                    ],
                )

            # ----------------------------- Translate -----------------------
            with gr.Tab("Translate"):
                tr_text = gr.Textbox(
                    label="Text to translate", lines=6, placeholder="Enter text..."
                )
                with gr.Row():
                    tr_source = gr.Dropdown(
                        label="Source language",
                        choices=translate_langs,
                        value="auto",
                    )
                    tr_target = gr.Dropdown(
                        label="Target language",
                        choices=translate_targets,
                        value="hi-IN",
                    )
                with gr.Row():
                    tr_model = gr.Dropdown(
                        label="Model",
                        choices=list(translate_tool.MODELS),
                        value=translate_tool.MODEL,
                    )
                    tr_mode = gr.Dropdown(
                        label="Mode (optional)",
                        choices=[""] + list(translate_tool.MODE_CHOICES),
                        value="",
                    )
                with gr.Row():
                    tr_output_script = gr.Dropdown(
                        label="Output script (optional)",
                        choices=[""] + list(translate_tool.OUTPUT_SCRIPT_CHOICES),
                        value="",
                    )
                    tr_numerals = gr.Dropdown(
                        label="Numerals format (optional)",
                        choices=[""] + list(translate_tool.NUMERALS_FORMAT_CHOICES),
                        value="",
                    )
                    tr_gender = gr.Dropdown(
                        label="Speaker gender (optional)",
                        choices=[""] + list(translate_tool.SPEAKER_GENDER_CHOICES),
                        value="",
                    )
                tr_button = gr.Button("Translate", variant="primary")
                tr_output = gr.Textbox(label="Translation", lines=6)
                tr_detected = gr.Textbox(label="Detected source language")

                tr_button.click(
                    run_translate,
                    inputs=[
                        api_key,
                        tr_text,
                        tr_source,
                        tr_target,
                        tr_model,
                        tr_mode,
                        tr_output_script,
                        tr_numerals,
                        tr_gender,
                    ],
                    outputs=[tr_output, tr_detected],
                )

            # -------------------------- Text-to-Speech ---------------------
            with gr.Tab("Text-to-Speech"):
                tts_text = gr.Textbox(
                    label="Text to synthesize", lines=6, placeholder="Enter text..."
                )
                with gr.Row():
                    tts_language = gr.Dropdown(
                        label="Language", choices=tts_langs, value="hi-IN"
                    )
                    tts_model = gr.Dropdown(
                        label="Model",
                        choices=list(tts_tool.MODELS),
                        value=tts_tool.DEFAULT_MODEL,
                    )
                    tts_speaker = gr.Dropdown(
                        label="Speaker",
                        choices=_speaker_choices_for(tts_tool.DEFAULT_MODEL),
                        value=tts_tool.default_speaker_for_model(
                            tts_tool.DEFAULT_MODEL
                        ),
                    )
                with gr.Row():
                    tts_sample_rate = gr.Dropdown(
                        label="Sample rate (Hz)",
                        choices=list(tts_tool.SUPPORTED_SAMPLE_RATES),
                        value=tts_tool.DEFAULT_SAMPLE_RATE,
                    )
                    tts_codec = gr.Dropdown(
                        label="Codec",
                        choices=list(tts_tool.CODEC_CHOICES),
                        value=tts_tool.DEFAULT_CODEC,
                    )
                    tts_pace = gr.Slider(
                        label="Pace", minimum=0.3, maximum=3.0, step=0.1, value=1.0
                    )
                with gr.Row():
                    tts_temperature = gr.Number(
                        label="Temperature (bulbul:v3 only, optional)", value=None
                    )
                    tts_pitch = gr.Number(
                        label="Pitch (bulbul:v2 only, optional)", value=None
                    )
                    tts_loudness = gr.Number(
                        label="Loudness (bulbul:v2 only, optional)", value=None
                    )
                with gr.Row():
                    tts_preprocess = gr.Checkbox(
                        label="Enable preprocessing (bulbul:v2 only)", value=False
                    )
                    tts_cached = gr.Checkbox(
                        label="Enable cached responses", value=False
                    )
                    tts_dict_id = gr.Textbox(
                        label="Pronunciation Dictionary ID (optional)",
                        placeholder="e.g. dict_xyz",
                    )
                tts_button = gr.Button("Synthesize", variant="primary")
                tts_audio = gr.Audio(label="Synthesized audio", type="filepath")

                # Refresh the speaker choices when the model changes.
                tts_model.change(
                    update_tts_speakers, inputs=[tts_model], outputs=[tts_speaker]
                )

                tts_button.click(
                    run_tts,
                    inputs=[
                        api_key,
                        tts_text,
                        tts_language,
                        tts_model,
                        tts_speaker,
                        tts_sample_rate,
                        tts_codec,
                        tts_pace,
                        tts_temperature,
                        tts_pitch,
                        tts_loudness,
                        tts_preprocess,
                        tts_dict_id,
                        tts_cached,
                    ],
                    outputs=[tts_audio],
                )

            # --------------------------- Document OCR ----------------------
            with gr.Tab("Document OCR"):
                ocr_file = gr.File(
                    label="Document (PDF or image)",
                    file_types=sorted(ocr_tool.DOC_EXTENSIONS),
                )
                with gr.Row():
                    ocr_language = gr.Textbox(
                        label="Language", value=ocr_tool.DEFAULT_LANGUAGE
                    )
                    ocr_output_format = gr.Dropdown(
                        label="Output format",
                        choices=list(ocr_tool.OUTPUT_FORMATS),
                        value=ocr_tool.DEFAULT_OUTPUT_FORMAT,
                    )
                    ocr_content_type = gr.Dropdown(
                        label="Content type",
                        choices=list(ocr_tool.CONTENT_TYPES),
                        value=ocr_tool.DEFAULT_CONTENT_TYPE,
                    )
                ocr_model = gr.Textbox(label="Model (optional)", value="")
                ocr_button = gr.Button("Digitize", variant="primary")
                ocr_summary = gr.Textbox(label="Summary")
                ocr_output = gr.Markdown(label="Extracted content")

                ocr_button.click(
                    run_ocr,
                    inputs=[
                        api_key,
                        ocr_file,
                        ocr_language,
                        ocr_output_format,
                        ocr_content_type,
                        ocr_model,
                    ],
                    outputs=[ocr_output, ocr_summary],
                )

        gr.Markdown(
            "_Note: Image Renamer is a batch/folder oriented CLI tool and is run "
            "directly from the command line (`python sarvam_image_renamer.py`)._"
        )

    return demo


def main() -> int:
    """Launch the local Gradio web UI. Returns a process exit code."""
    if not HAS_GRADIO:
        print("ERROR: the web front end could not start.")
        print(_gradio_error_detail())
        return 1

    demo = build_ui()
    demo.queue()
    demo.launch()
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
