"""
Sarvam AI Tools - Gradio Web Front End
======================================
An interactive web UI for testing the Sarvam AI command-line tools in this
suite without touching the terminal. It is a NEW, additive artifact: it does
NOT modify or refactor the individual tool scripts. Instead it honors the
"one tool = one script" philosophy by IMPORTING each script's module-level
worker functions and constants and calling them in-process.

Wired tabs:
- Translate       -> sarvam_translate.translate_unit
- Text-to-Speech  -> sarvam_tts.synthesize_unit
- Document OCR    -> sarvam_doc_ocr.digitise_document

Transcription (transcribe_sarvam.py) and the Image Renamer
(sarvam_image_renamer.py) are deliberately NOT wired here: both are
batch/folder oriented and do not expose a clean single-item worker function,
so wiring them cleanly would require refactoring those scripts (which is
forbidden for this task). See the feature findings for details.

API key handling: the UI provides a password field. When it is left blank the
resolver falls back to get_api_key(None, target_dir), which reads
SARVAM_API_KEY from the environment or a local .env file. The key value is
never logged or echoed. A SarvamAI client is instantiated per call.

Outputs are written to temporary files; user inputs are never mutated.
"""

from __future__ import annotations

import os
import tempfile
import types
from pathlib import Path

# Guard the gradio import so the module can be inspected (and give a friendly
# hint) even when gradio is not installed. Mirrors the HAS_SARVAM pattern used
# by the tool scripts.
try:
    import gradio as gr

    HAS_GRADIO = True
except ImportError:
    gr = None  # type: ignore[assignment]
    HAS_GRADIO = False

# Sarvam AI SDK (needed to instantiate a client per call). Guarded like the
# tool scripts so importing app.py never crashes on a missing dependency.
try:
    from sarvamai import SarvamAI

    HAS_SARVAM = True
except ImportError:
    SarvamAI = None  # type: ignore[assignment]
    HAS_SARVAM = False

# Import each tool module under an alias to avoid colliding constant names
# (each defines its own SUPPORTED_LANGUAGES / MODELS / char_limit_for_model /
# get_api_key, etc.). Importing is safe: load_dotenv() and the sarvamai import
# are guarded in every module, and each main() is under `if __name__ ...`.
import sarvam_doc_ocr as ocr_tool
import sarvam_translate as translate_tool
import sarvam_tts as tts_tool

# Directory used for the .env fallback when the key field is blank.
TARGET_DIR = Path(__file__).resolve().parent

# UI-facing OCR poll timeout (seconds). The CLI default (ocr_tool.DEFAULT_TIMEOUT,
# 600s) is fine for a batch script but too long to hold a web request thread.
# Cap it lower so a single slow document cannot tie up a worker for ten minutes.
OCR_UI_TIMEOUT = 120.0

# Guidance shown when no API key can be resolved (mirrors the CLI wording,
# never echoes any key value).
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
    # translate_unit reads/writes the tool module's process-wide STATE.cache
    # singleton. In this long-lived server that dict would otherwise grow
    # unbounded and be shared across every request. Reset it per call so no
    # cross-request state accumulates (cache_path is None, so nothing is
    # flushed to disk and this is purely an in-memory reset). This reset
    # assumes serialized execution (the default demo.queue() concurrency);
    # if request concurrency is ever raised, key the cache per request
    # instead so concurrent calls do not clobber each other's chunk cache.
    translate_tool.STATE.cache = {}
    try:
        result = translate_tool.translate_unit(client, text, "gradio", args, limit)
    except Exception as exc:
        # Surface any failure in the UI rather than crashing the server.
        raise gr.Error(str(exc)) from exc
    finally:
        # Drop any entries populated during this call so memory does not grow.
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
        list_speakers=False,
        delay=0.0,
        max_retries=tts_tool.DEFAULT_MAX_RETRIES,
    )

    # validate_args normalizes speaker/model-specific options in-place and
    # returns an error string (or None). Convert any error into a gr.Error.
    error = tts_tool.validate_args(args)
    if error:
        raise gr.Error(error)

    limit = tts_tool.char_limit_for_model(model)
    try:
        result = tts_tool.synthesize_unit(client, text, "gradio", args, limit)
    except Exception as exc:
        # Surface any failure in the UI rather than crashing the server.
        raise gr.Error(str(exc)) from exc

    audio_bytes = result.get("audio_bytes") or b""
    if not audio_bytes:
        raise gr.Error("No audio was returned by the API.")

    # Write to a tempfile so playback/download works; user inputs are never
    # mutated. delete=False keeps the file around for Gradio to serve.
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

    # gr.File provides a real temp path on disk, which digitise_document needs.
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
        # Bound the blocking poll so a slow document does not hold the worker
        # thread for the full CLI DEFAULT_TIMEOUT (600s).
        timeout=OCR_UI_TIMEOUT,
    )

    try:
        payload = ocr_tool.digitise_document(client, path, args)
    except Exception as exc:
        # Surface any failure in the UI rather than crashing the server.
        raise gr.Error(str(exc)) from exc

    body = str(payload.get("body") or "[No text extracted]")
    page_count = int(payload.get("page_count") or 0)
    job_id = payload.get("job_id") or "n/a"
    summary = f"Pages: {page_count} | Format: {output_format} | Job ID: {job_id}"
    return body, summary


# ---------------------------------------------------------------------------
# UI construction
# ---------------------------------------------------------------------------
def build_ui():
    """
    Construct and return the Gradio Blocks for the Sarvam tools front end.

    Kept separate from launch() so tests can build the UI object without
    starting a server or needing an API key.
    """
    if not HAS_GRADIO:
        raise RuntimeError(
            "gradio is not installed. Install dependencies with "
            "pip install -r requirements.txt."
        )

    translate_langs = ["auto"] + sorted(translate_tool.SUPPORTED_LANGUAGES)
    translate_targets = sorted(translate_tool.SUPPORTED_LANGUAGES)
    tts_langs = sorted(tts_tool.SUPPORTED_LANGUAGES)

    with gr.Blocks(title="Sarvam AI Tools") as demo:
        gr.Markdown(
            "# Sarvam AI Tools\n"
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
                    tts_preprocess = gr.Checkbox(
                        label="Enable preprocessing (bulbul:v2 only)", value=False
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
            "_Note: Transcription and Image Renamer are batch/folder oriented "
            "CLI tools and are not exposed here; run them from the command "
            "line._"
        )

    return demo


def main() -> int:
    """Launch the local Gradio web UI. Returns a process exit code."""
    if not HAS_GRADIO:
        print("ERROR: 'gradio' package is required to run the web front end.")
        print("Please install dependencies: pip install -r requirements.txt")
        return 1

    demo = build_ui()
    # Enable the request queue so a slow, blocking call (e.g. an OCR poll) is
    # scheduled rather than starving other concurrent requests.
    demo.queue()
    demo.launch()
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
