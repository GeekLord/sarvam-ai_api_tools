from __future__ import annotations

"""
Sarvam AI Standalone Text Translator
====================================
A robust, portable CLI tool that translates text files (and literal
strings) across Indic languages and English using the Sarvam AI
Translation API (model ``mayura:v1`` by default).

Features:
- Completely portable: operates in the current working directory or any
  specified path; a single positional argument selects a file or folder.
- Batch translation: discovers ``.txt`` / ``.md`` files in a directory
  (non-recursive) or translates a single file or literal ``--text``.
- Automatic long-text chunking: inputs above the model character limit are
  split on paragraph/sentence boundaries so nothing is silently truncated.
- Automatic source-language detection via ``auto`` (mayura:v1).
- Persistent resumable caching: ``.sarvam_translate_cache.json`` protects
  API quota; atomic writes (``.tmp`` then ``os.replace``) keep it safe.
- Automatic rate-limit management: polite cooldowns, exponential backoff
  with jitter, and 429 detection; continues past per-item failures.
- Graceful shutdown: SIGINT/SIGTERM flushes the cache before exiting.
- Non-destructive: never overwrites input files; writes into a dedicated
  ``sarvam_translations`` folder.
- Multi-format export: per-file ``.txt`` and ``.json`` plus an aggregate
  JSON manifest, CSV spreadsheet, and Markdown catalog.
"""

import argparse
import csv
import hashlib
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
MODEL = "mayura:v1"
MODELS = ("mayura:v1", "sarvam-translate:v1")

# BCP-47 language codes supported across the mayura and sarvam-translate sets.
SUPPORTED_LANGUAGES = {
    "as-IN": "Assamese",
    "bn-IN": "Bengali",
    "brx-IN": "Bodo",
    "doi-IN": "Dogri",
    "en-IN": "English",
    "gu-IN": "Gujarati",
    "hi-IN": "Hindi",
    "kn-IN": "Kannada",
    "kok-IN": "Konkani",
    "ks-IN": "Kashmiri",
    "mai-IN": "Maithili",
    "ml-IN": "Malayalam",
    "mni-IN": "Manipuri",
    "mr-IN": "Marathi",
    "ne-IN": "Nepali",
    "od-IN": "Odia",
    "pa-IN": "Punjabi",
    "sa-IN": "Sanskrit",
    "sat-IN": "Santali",
    "sd-IN": "Sindhi",
    "ta-IN": "Tamil",
    "te-IN": "Telugu",
    "ur-IN": "Urdu",
}

TEXT_EXTENSIONS = {".txt", ".md"}

MAYURA_CHAR_LIMIT = 1000
SARVAM_TRANSLATE_CHAR_LIMIT = 2000

DEFAULT_DELAY = 1.0
DEFAULT_MAX_RETRIES = 5
INITIAL_BACKOFF = 5.0

OUTPUT_DIR = Path("sarvam_translations")
CACHE_FILE = ".sarvam_translate_cache.json"

MODE_CHOICES = ("formal", "modern-colloquial", "classic-colloquial", "code-mixed")
OUTPUT_SCRIPT_CHOICES = ("roman", "fully-native", "spoken-form-in-native")
NUMERALS_FORMAT_CHOICES = ("international", "native")
SPEAKER_GENDER_CHOICES = ("Male", "Female")


class TranslatorState:
    """Manages the resumable cache and graceful exit on interrupt."""

    def __init__(self) -> None:
        self.interrupted = False
        self.cache: dict[str, Any] = {}
        self.cache_path: str | None = None

    def setup_signals(self) -> None:
        signal.signal(signal.SIGINT, self._handle_interrupt)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, self._handle_interrupt)

    def _handle_interrupt(self, sig: int, frame: Any) -> None:
        print("\n\n[!] Interrupt received. Flushing cache and shutting down...")
        self.interrupted = True
        self.flush_cache()
        sys.exit(130)

    def flush_cache(self) -> None:
        if self.cache_path and self.cache:
            try:
                temp_file = f"{self.cache_path}.tmp"
                with open(temp_file, "w", encoding="utf-8") as f:
                    json.dump(self.cache, f, indent=2, ensure_ascii=False)
                if os.path.exists(temp_file):
                    os.replace(temp_file, self.cache_path)
            except Exception as e:
                print(f"[!] Warning: Could not flush cache: {e}")


STATE = TranslatorState()


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
    if model == "sarvam-translate:v1":
        return SARVAM_TRANSLATE_CHAR_LIMIT
    return MAYURA_CHAR_LIMIT


def find_text_files(folder: Path) -> list[Path]:
    """Discover translatable text files in a folder (non-recursive, sorted)."""
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


def cache_key(
    text: str,
    source: str,
    target: str,
    model: str,
    mode: str | None,
    output_script: str | None,
    numerals_format: str | None,
) -> str:
    """MD5 key over the translation-defining parameters."""
    payload = "\x1f".join(
        [
            text,
            source,
            target,
            model,
            mode or "",
            output_script or "",
            numerals_format or "",
        ]
    )
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


def response_to_dict(response: Any) -> dict[str, Any]:
    """
    Convert a Sarvam translation response into a plain dict, reading fields
    defensively across SDK versions.
    """
    if isinstance(response, dict):
        data = dict(response)
    else:
        for attr in ("model_dump", "dict"):
            method = getattr(response, attr, None)
            if callable(method):
                try:
                    dumped = method()
                    if isinstance(dumped, dict):
                        return dumped
                except Exception:
                    pass
        data = {}
        for field in ("request_id", "translated_text", "source_language_code"):
            if hasattr(response, field):
                data[field] = getattr(response, field)
    return data


def get_field(data: dict[str, Any], response: Any, *names: str) -> Any:
    """Read a field from the dict payload or the raw response object."""
    for name in names:
        if isinstance(data, dict) and data.get(name) is not None:
            return data.get(name)
    for name in names:
        value = getattr(response, name, None)
        if value is not None:
            return value
    return None


def translate_chunk(
    client: SarvamAI,
    chunk: str,
    args: argparse.Namespace,
    label: str,
    max_retries: int,
    initial_backoff: float,
) -> dict[str, Any]:
    """
    Translate a single chunk with 429-aware exponential backoff.

    Returns the response payload as a dict. Raises on unrecoverable errors so
    the caller can count the failure and continue with the next unit.
    """
    kwargs: dict[str, Any] = {
        "input": chunk,
        "source_language_code": args.source,
        "target_language_code": args.target,
        "model": args.model,
    }
    for key, value in (
        ("mode", args.mode),
        ("output_script", args.output_script),
        ("numerals_format", args.numerals_format),
        ("speaker_gender", args.speaker_gender),
    ):
        if value is not None:
            kwargs[key] = value

    for attempt in range(max_retries):
        if STATE.interrupted:
            raise KeyboardInterrupt()

        try:
            response = client.text.translate(**kwargs)
            return response_to_dict(response)
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


def translate_unit(
    client: SarvamAI,
    text: str,
    label: str,
    args: argparse.Namespace,
    limit: int,
) -> dict[str, Any]:
    """
    Translate one input unit (a file's contents or a literal string),
    chunking when needed and re-joining the pieces. Uses the resumable cache.
    """
    chunks = split_text_into_chunks(text, limit)
    if not chunks:
        return {
            "translated_text": "",
            "source_language_code": args.source,
            "payloads": [],
            "chunk_count": 0,
        }

    if len(chunks) > 1:
        print(
            f"    [chunking] input exceeds {limit} chars; split into {len(chunks)} chunk(s)."
        )

    translated_parts: list[str] = []
    payloads: list[dict[str, Any]] = []
    detected_source: str | None = None

    for index, chunk in enumerate(chunks, start=1):
        key = cache_key(
            chunk,
            args.source,
            args.target,
            args.model,
            args.mode,
            args.output_script,
            args.numerals_format,
        )

        payload = STATE.cache.get(key)
        if payload is None:
            payload = translate_chunk(
                client,
                chunk,
                args,
                f"{label} [chunk {index}/{len(chunks)}]",
                args.max_retries,
                INITIAL_BACKOFF,
            )
            STATE.cache[key] = payload
            STATE.flush_cache()
            if args.delay > 0:
                time.sleep(args.delay)

        translated = get_field(payload, payload, "translated_text") or ""
        translated_parts.append(str(translated))
        source_code = get_field(payload, payload, "source_language_code")
        if source_code and detected_source is None:
            detected_source = str(source_code)
        payloads.append(payload)

    return {
        "translated_text": "\n\n".join(translated_parts).strip(),
        "source_language_code": detected_source or args.source,
        "payloads": payloads,
        "chunk_count": len(chunks),
    }


def write_unit_outputs(
    stem: str,
    source_name: str,
    result: dict[str, Any],
    args: argparse.Namespace,
    output_dir: Path,
) -> Path:
    """Write the human-readable .txt and raw .json for one translated unit."""
    txt_path = output_dir / f"{stem}.{args.target}.txt"
    json_path = output_dir / f"{stem}.{args.target}.json"

    src_lang = str(result.get("source_language_code") or args.source)
    src_label = SUPPORTED_LANGUAGES.get(src_lang, src_lang)
    tgt_label = SUPPORTED_LANGUAGES.get(args.target, args.target)

    lines = [
        f"Source File     : {source_name}",
        f"Source Language : {src_label} ({src_lang})",
        f"Target Language : {tgt_label} ({args.target})",
        f"Model           : {args.model}",
        f"Mode            : {args.mode or 'default'}",
        "",
        "TRANSLATION",
        "=" * 80,
        "",
        str(result.get("translated_text") or "[No translation returned]"),
    ]
    txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    raw = {
        "source_file": source_name,
        "source_language_code": src_lang,
        "target_language_code": args.target,
        "model": args.model,
        "mode": args.mode,
        "output_script": args.output_script,
        "numerals_format": args.numerals_format,
        "speaker_gender": args.speaker_gender,
        "translated_text": result.get("translated_text"),
        "chunk_count": result.get("chunk_count"),
        "payloads": result.get("payloads"),
    }
    json_path.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return txt_path


def write_aggregate_outputs(
    records: list[dict[str, Any]],
    args: argparse.Namespace,
    output_dir: Path,
) -> None:
    """Write the suite-consistent manifest JSON/CSV and Markdown catalog."""
    manifest_json = output_dir / "translations_manifest.json"
    manifest_csv = output_dir / "translations_manifest.csv"
    catalog_md = output_dir / "TRANSLATIONS.md"

    manifest_json.write_text(
        json.dumps(records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    fieldnames = [
        "source_file",
        "source_language",
        "target_language",
        "model",
        "mode",
        "char_count",
        "output_file",
        "status",
    ]
    with manifest_csv.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            writer.writerow({k: record.get(k, "") for k in fieldnames})

    md_lines = [
        "# Sarvam AI — Translation Catalog",
        "",
        "> Generated by **Sarvam AI Standalone Text Translator**.",
        "",
        f"* **Total Units:** {len(records)}",
        f"* **Model:** {args.model}",
        f"* **Target Language:** {SUPPORTED_LANGUAGES.get(args.target, args.target)} ({args.target})",
        f"* **Generated:** {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "| Source File | Source Lang | Target Lang | Model | Mode | Chars | Output | Status |",
        "| :--- | :--- | :--- | :--- | :--- | ---: | :--- | :--- |",
    ]
    for record in records:
        md_lines.append(
            "| `{source_file}` | {source_language} | {target_language} | "
            "{model} | {mode} | {char_count} | `{output_file}` | {status} |".format(
                source_file=record.get("source_file", ""),
                source_language=record.get("source_language", ""),
                target_language=record.get("target_language", ""),
                model=record.get("model", ""),
                mode=record.get("mode") or "default",
                char_count=record.get("char_count", 0),
                output_file=record.get("output_file", ""),
                status=record.get("status", ""),
            )
        )
    md_lines.append("")
    catalog_md.write_text("\n".join(md_lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Translate text files or literal strings across Indic languages "
            "and English with the Sarvam AI Translation API (mayura:v1)."
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
        help="Translate this literal text instead of reading files.",
    )
    parser.add_argument(
        "--source",
        "--source-language",
        dest="source",
        type=str,
        default="auto",
        help="Source language BCP-47 code, or 'auto' to detect (default: auto).",
    )
    parser.add_argument(
        "--target",
        "--target-language",
        dest="target",
        type=str,
        default=None,
        help="Target language BCP-47 code, e.g. hi-IN (REQUIRED).",
    )
    parser.add_argument(
        "--model",
        type=str,
        choices=MODELS,
        default=MODEL,
        help=f"Translation model (default: {MODEL}).",
    )
    parser.add_argument(
        "--mode",
        type=str,
        choices=MODE_CHOICES,
        default=None,
        help="Translation register/mode (default: model default).",
    )
    parser.add_argument(
        "--output-script",
        type=str,
        choices=OUTPUT_SCRIPT_CHOICES,
        default=None,
        help="Transliteration style of the output (default: model default).",
    )
    parser.add_argument(
        "--numerals-format",
        type=str,
        choices=NUMERALS_FORMAT_CHOICES,
        default=None,
        help="Numeral style: international or native (default: model default).",
    )
    parser.add_argument(
        "--speaker-gender",
        type=str,
        choices=SPEAKER_GENDER_CHOICES,
        default=None,
        help="Speaker gender hint for gendered translations (default: none).",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        type=str,
        default=None,
        help="Output directory for translations (default: <path>/sarvam_translations).",
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
    """Return an error message string if the args are invalid, else None."""
    if not args.target:
        return "--target (target language) is required."
    if args.target not in SUPPORTED_LANGUAGES:
        return (
            f"--target '{args.target}' is not a supported language code. "
            f"Supported: {', '.join(sorted(SUPPORTED_LANGUAGES))}."
        )
    if args.source != "auto" and args.source not in SUPPORTED_LANGUAGES:
        return (
            f"--source '{args.source}' is not a supported language code. "
            f"Use 'auto' or one of: {', '.join(sorted(SUPPORTED_LANGUAGES))}."
        )
    if args.max_retries <= 0:
        return "--max-retries must be greater than 0."
    if args.delay < 0:
        return "--delay must not be negative."
    return None


def main() -> int:
    args = parse_args()

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

    # Build the list of (stem, source_name, text) units to translate.
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
        print("No input text to translate.")
        return 0

    output_dir = (
        Path(args.output_dir).resolve() if args.output_dir else target_dir / OUTPUT_DIR
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    STATE.setup_signals()
    STATE.cache_path = str(output_dir / CACHE_FILE)
    if os.path.exists(STATE.cache_path):
        try:
            with open(STATE.cache_path, "r", encoding="utf-8") as f:
                STATE.cache = json.load(f)
            print(
                f"Loaded {len(STATE.cache)} cached translation(s) from '{CACHE_FILE}'."
            )
        except Exception as e:
            print(f"[!] Warning reading cache file: {e}")
            STATE.cache = {}

    limit = char_limit_for_model(args.model)

    print("=======================================================")
    print(" Sarvam AI Text Translator")
    print(f" Model           : {args.model}")
    print(f" Source Language : {args.source}")
    print(
        f" Target Language : {SUPPORTED_LANGUAGES.get(args.target, args.target)} ({args.target})"
    )
    print(f" Mode            : {args.mode or 'default'}")
    print(f" Output Dir      : {output_dir}")
    print(f" Input Units     : {len(units)}")
    print("=======================================================")
    print()

    client = SarvamAI(api_subscription_key=api_key)

    records: list[dict[str, Any]] = []
    successful = 0
    failed = 0

    for index, (stem, source_name, text) in enumerate(units, start=1):
        print(f"[{index}/{len(units)}] {source_name}")
        try:
            result = translate_unit(client, text, source_name, args, limit)
            out_path = write_unit_outputs(stem, source_name, result, args, output_dir)
            successful += 1
            print(f"  ✓ {source_name} -> {out_path.name}")
            records.append(
                {
                    "source_file": source_name,
                    "source_language": str(
                        result.get("source_language_code") or args.source
                    ),
                    "target_language": args.target,
                    "model": args.model,
                    "mode": args.mode,
                    "char_count": len(text),
                    "output_file": out_path.name,
                    "status": "ok",
                }
            )
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            failed += 1
            print(f"  ✗ {source_name}: {exc}")
            records.append(
                {
                    "source_file": source_name,
                    "source_language": args.source,
                    "target_language": args.target,
                    "model": args.model,
                    "mode": args.mode,
                    "char_count": len(text),
                    "output_file": "",
                    "status": f"failed: {exc}",
                }
            )

    write_aggregate_outputs(records, args, output_dir)

    print()
    print("=== Finished ===")
    print(f"Successful : {successful}")
    print(f"Failed     : {failed}")
    print(f"Output     : {output_dir}")

    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
