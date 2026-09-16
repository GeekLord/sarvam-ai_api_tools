from __future__ import annotations

"""
Sarvam AI Standalone Document OCR & Parser
==========================================
A robust, portable CLI tool that digitizes documents (PDFs and images)
into structured text, tables, and Markdown/HTML using the Sarvam AI
Document Intelligence ``doc_ai`` digitise job flow.

Features:
- Completely portable: operates in the current working directory or any
  specified path; a single positional argument selects a file or folder.
- Fully recursive: discovers documents in nested subdirectories while
  skipping hidden and system directories.
- Content-addressable deduplication: duplicate files share a single job
  (cache keyed by MD5 + output format + content type).
- Persistent resumable caching: ``.sarvam_ocr_cache.json`` protects API
  quota; atomic writes (``.tmp`` then ``os.replace``) keep it safe.
- Job flow: ``digitise`` -> poll ``get_status`` -> ``get_results`` with a
  ``get_download_url`` fallback that fetches the full rendered output.
- Automatic rate-limit management: polite cooldowns, exponential backoff
  with jitter, and 429 detection; continues past per-document failures.
- Graceful shutdown: SIGINT/SIGTERM flushes the cache before exiting.
- Non-destructive: never modifies or moves source documents; writes into a
  dedicated ``sarvam_ocr_output`` folder.
- Multi-format export: per-document text (``.md``/``.html``/``.txt``) and
  raw ``.json`` plus an aggregate JSON manifest, CSV spreadsheet, and a
  Markdown catalog index.
"""

import argparse
import csv
import hashlib
import json
import os
import random
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

# Optional requests (shared dependency; used for presigned download URLs)
try:
    import requests

    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

# Sarvam AI SDK
try:
    from sarvamai import SarvamAI

    HAS_SARVAM = True
except ImportError:
    HAS_SARVAM = False


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------
DOC_EXTENSIONS = {
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".tiff",
    ".tif",
    ".bmp",
    ".webp",
}

DEFAULT_LANGUAGE = "en-IN"

OUTPUT_FORMATS = ("html", "md", "json")
DEFAULT_OUTPUT_FORMAT = "md"

CONTENT_TYPES = ("printed", "handwritten", "mixed")
DEFAULT_CONTENT_TYPE = "printed"

DEFAULT_DELAY = 3.0
DEFAULT_MAX_RETRIES = 5
INITIAL_BACKOFF = 5.0
DEFAULT_TIMEOUT = 600.0

OUTPUT_DIR = Path("sarvam_ocr_output")
CACHE_FILE = ".sarvam_ocr_cache.json"

# Directories to ignore during recursive search
IGNORED_DIRS = {
    ".git",
    ".svn",
    ".hg",
    ".vscode",
    ".idea",
    "__pycache__",
    "node_modules",
    ".sarvam_temp",
    ".system_generated",
    "sarvam_ocr_output",
}

# Terminal job statuses (compared case-insensitively).
COMPLETED_STATUSES = {"completed", "success", "succeeded", "done", "complete"}
FAILED_STATUSES = {"failed", "error", "cancelled", "canceled"}

# File extension for the per-document text artifact by output format.
TEXT_SUFFIX_BY_FORMAT = {"md": ".md", "html": ".html", "json": ".txt"}


class OcrState:
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


STATE = OcrState()


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


def compute_file_hash(filepath: Path, block_size: int = 65536) -> str:
    """Compute the MD5 hash of a file in memory-efficient chunks."""
    hasher = hashlib.md5()
    with open(filepath, "rb") as f:
        for block in iter(lambda: f.read(block_size), b""):
            hasher.update(block)
    return hasher.hexdigest()


def find_document_files(root_dir: Path) -> list[Path]:
    """
    Recursively scan ``root_dir`` for supported documents, ignoring hidden
    and system directories. Returns paths relative to ``root_dir``, sorted.
    """
    root_path = Path(root_dir).resolve()
    doc_files: list[Path] = []

    for dirpath, dirnames, filenames in os.walk(root_path):
        # Filter ignored directories in-place to avoid traversing them.
        dirnames[:] = [
            d for d in dirnames if d not in IGNORED_DIRS and not d.startswith(".")
        ]

        for fname in filenames:
            ext = os.path.splitext(fname)[1].lower()
            if ext in DOC_EXTENSIONS and not fname.startswith("."):
                full_path = Path(dirpath) / fname
                rel_path = full_path.relative_to(root_path)
                doc_files.append(rel_path)

    return sorted(doc_files, key=lambda p: str(p).lower())


def cache_key(file_hash: str, output_format: str, content_type: str) -> str:
    """Build the resumable cache key from content hash and job parameters."""
    return f"{file_hash}:{output_format}:{content_type}"


def to_dict(obj: Any) -> dict[str, Any]:
    """
    Convert an SDK response object into a plain dict, defensively across
    versions (``model_dump``/``dict`` fallbacks). Returns an empty dict when
    the object cannot be serialized.
    """
    if isinstance(obj, dict):
        return dict(obj)
    for attr in ("model_dump", "dict"):
        method = getattr(obj, attr, None)
        if callable(method):
            try:
                dumped = method()
                if isinstance(dumped, dict):
                    return dumped
            except Exception:
                pass
    data: dict[str, Any] = {}
    for field in vars(obj) if hasattr(obj, "__dict__") else []:
        if not field.startswith("_"):
            data[field] = getattr(obj, field)
    return data


def read_field(obj: Any, *names: str) -> Any:
    """Read the first present field from a dict or object by any of ``names``."""
    if isinstance(obj, dict):
        for name in names:
            if obj.get(name) is not None:
                return obj.get(name)
    for name in names:
        value = getattr(obj, name, None)
        if value is not None:
            return value
    return None


def status_string(status_response: Any) -> str:
    """Extract a lowercase status string from a status response, defensively."""
    status = read_field(status_response, "status", "state", "job_status")
    if status is None:
        data = to_dict(status_response)
        status = data.get("status") or data.get("state") or data.get("job_status")
    return str(status or "").strip().lower()


def extract_documents(results: Any) -> list[dict[str, Any]]:
    """Read the ``documents`` list from a digitise results payload."""
    documents = read_field(results, "documents")
    if documents is None:
        data = to_dict(results)
        documents = data.get("documents")
    if not isinstance(documents, list):
        return []
    normalized: list[dict[str, Any]] = []
    for doc in documents:
        normalized.append(doc if isinstance(doc, dict) else to_dict(doc))
    return normalized


def extract_text_from_documents(documents: list[dict[str, Any]]) -> tuple[str, int]:
    """
    Assemble the concatenated document body and total page count from the
    digitise ``documents`` structure, preserving any Markdown/HTML the API
    returns per page.
    """
    parts: list[str] = []
    page_count = 0

    for doc in documents:
        pages = doc.get("pages")
        if isinstance(pages, list):
            for page in pages:
                page_count += 1
                if isinstance(page, dict):
                    text = (
                        page.get("text")
                        or page.get("content")
                        or page.get("markdown")
                        or page.get("html")
                        or ""
                    )
                else:
                    text = str(page)
                if text:
                    parts.append(str(text).strip())
        else:
            # No page-level structure; fall back to any document-level text.
            text = doc.get("text") or doc.get("content") or doc.get("markdown") or ""
            if text:
                parts.append(str(text).strip())

    return ("\n\n".join(p for p in parts if p).strip(), page_count)


def try_download_rendered_output(client: SarvamAI, job_id: str) -> str | None:
    """
    Attempt to fetch the full rendered output via a presigned download URL.

    Returns the downloaded body text, or ``None`` when the method is missing,
    the URL is absent, ``requests`` is unavailable, or the fetch fails.
    """
    get_url = getattr(client.doc_ai, "get_download_url", None)
    if not callable(get_url):
        return None

    try:
        url_response = get_url(job_id)
    except Exception:
        return None

    data = to_dict(url_response)
    url = data.get("url") or read_field(url_response, "url")
    if not url:
        return None

    if not HAS_REQUESTS:
        return None

    method = (data.get("method") or read_field(url_response, "method") or "GET").upper()
    headers = data.get("headers") or read_field(url_response, "headers") or {}
    if not isinstance(headers, dict):
        headers = {}

    try:
        response = requests.request(method, url, headers=headers, timeout=120)
        response.raise_for_status()
        return response.text
    except Exception:
        return None


def poll_job(
    client: SarvamAI,
    job_id: str,
    delay: float,
    timeout: float,
) -> str:
    """
    Poll ``get_status`` until a terminal state is reached, respecting the
    interrupt flag and an overall timeout. Returns the final status string.
    """
    deadline = time.monotonic() + timeout
    status = ""

    while True:
        if STATE.interrupted:
            raise KeyboardInterrupt()

        try:
            status_response = client.doc_ai.get_status(job_id)
        except Exception as exc:
            raise RuntimeError(f"status poll failed: {exc}") from exc

        status = status_string(status_response)
        if status in COMPLETED_STATUSES:
            return status
        if status in FAILED_STATUSES:
            raise RuntimeError(f"job {job_id} ended with status '{status}'")

        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"job {job_id} did not complete within {timeout:.0f}s "
                f"(last status: '{status or 'unknown'}')"
            )

        time.sleep(max(delay, 0.5))


def _retry_with_backoff(
    action: Any,
    label: str,
    phase: str,
    max_retries: int,
) -> Any:
    """
    Call ``action()`` with 429-aware exponential backoff and jitter.

    ``action`` is a zero-argument callable representing one retryable phase
    (submit, or poll/results). A 429 (detected by substring) is retried up to
    ``max_retries`` times; any other error, or exhausting the retries, raises.
    """
    for attempt in range(max_retries):
        if STATE.interrupted:
            raise KeyboardInterrupt()

        try:
            return action()
        except KeyboardInterrupt:
            raise
        except Exception as e:
            err_str = str(e)
            is_rate_limit = "429" in err_str or "rate limit" in err_str.lower()

            if is_rate_limit and attempt < max_retries - 1:
                backoff = (INITIAL_BACKOFF * (2**attempt)) + random.uniform(1.0, 4.0)
                print(
                    f"\n[Rate Limit 429] on {label} ({phase}). Cooling down for "
                    f"{backoff:.1f}s (attempt {attempt + 1}/{max_retries})..."
                )
                time.sleep(backoff)
                continue
            raise

    raise RuntimeError(f"Exceeded max retries ({max_retries}) on {label} ({phase}).")


def digitise_document(
    client: SarvamAI,
    path: Path,
    args: argparse.Namespace,
) -> dict[str, Any]:
    """
    Run the full digitise flow for a single document with 429-aware
    exponential backoff. Returns a payload dict with the extracted body,
    page count, raw results, and any rendered download.

    The submit phase (``digitise``, which creates the job_id) and the
    poll/results phase are retried independently: a 429 encountered while
    polling or fetching results retries against the *same* captured job_id
    instead of re-submitting a brand-new job and wasting quota.

    Raises on unrecoverable errors so the caller can count the failure and
    continue with the next document.
    """
    label = path.name

    # --- Submit phase: open the file in binary mode and upload its bytes. ---
    # A bare str in the SDK's ``file`` list is treated as file *content*, so we
    # pass a ``(filename, binary_handle)`` tuple to upload the document bytes.
    # The handle stays open until the digitise submit returns, then is closed.
    def submit() -> str:
        with open(path, "rb") as fh:
            digitise_kwargs: dict[str, Any] = {
                "file": [(path.name, fh)],
                "language": args.language,
                "output_format": args.output_format,
                "content_type": args.content_type,
            }
            if args.model:
                digitise_kwargs["model"] = args.model

            start_response = client.doc_ai.digitise(**digitise_kwargs)

        job_id = read_field(start_response, "job_id", "id")
        if not job_id:
            data = to_dict(start_response)
            job_id = data.get("job_id") or data.get("id")
        if not job_id:
            raise RuntimeError("digitise did not return a job_id")
        return str(job_id)

    job_id = _retry_with_backoff(submit, label, "submit", args.max_retries)

    # --- Poll/results phase: retried against the SAME captured job_id. ---
    def poll_and_fetch() -> dict[str, Any]:
        poll_job(client, job_id, args.delay, args.timeout)

        results = client.doc_ai.get_results(job_id)
        documents = extract_documents(results)
        body, page_count = extract_text_from_documents(documents)

        rendered = try_download_rendered_output(client, job_id)
        if rendered and (not body or len(rendered) > len(body)):
            body = rendered

        return {
            "job_id": job_id,
            "body": body,
            "page_count": page_count,
            "raw_results": to_dict(results),
            "rendered_downloaded": rendered is not None,
        }

    return _retry_with_backoff(poll_and_fetch, label, "poll/results", args.max_retries)


def write_document_outputs(
    stem: str,
    source_rel: Path,
    payload: dict[str, Any],
    args: argparse.Namespace,
    output_dir: Path,
) -> tuple[Path, int]:
    """
    Write the per-document text artifact (with a header block) and the raw
    JSON payload. Returns the text output path and the character count of the
    extracted body.
    """
    text_suffix = TEXT_SUFFIX_BY_FORMAT.get(args.output_format, ".txt")
    text_path = output_dir / f"{stem}{text_suffix}"
    json_path = output_dir / f"{stem}.json"

    body = str(payload.get("body") or "")
    page_count = int(payload.get("page_count") or 0)

    header = [
        f"Source File   : {source_rel}",
        f"Language      : {args.language}",
        f"Content Type  : {args.content_type}",
        f"Output Format : {args.output_format}",
        f"Pages         : {page_count}",
        f"Model         : {args.model or 'default'}",
    ]

    if args.output_format == "html":
        header_block = "<!--\n" + "\n".join(header) + "\n-->\n"
        text_path.write_text(header_block + body + "\n", encoding="utf-8")
    else:
        header_block = "\n".join(header) + "\n" + ("=" * 80) + "\n\n"
        text_path.write_text(
            header_block + (body or "[No text extracted]") + "\n",
            encoding="utf-8",
        )

    raw = {
        "source_file": str(source_rel),
        "language": args.language,
        "content_type": args.content_type,
        "output_format": args.output_format,
        "model": args.model,
        "job_id": payload.get("job_id"),
        "page_count": page_count,
        "rendered_downloaded": payload.get("rendered_downloaded"),
        "results": payload.get("raw_results"),
    }
    json_path.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return text_path, len(body)


def write_aggregate_outputs(
    records: list[dict[str, Any]],
    args: argparse.Namespace,
    output_dir: Path,
    root_dir: Path,
) -> None:
    """Write the suite-consistent manifest JSON/CSV and Markdown catalog."""
    manifest_json = output_dir / "ocr_manifest.json"
    manifest_csv = output_dir / "ocr_manifest.csv"
    catalog_md = output_dir / "OCR_CATALOG.md"

    manifest_json.write_text(
        json.dumps(records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    fieldnames = [
        "source_path",
        "output_file",
        "language",
        "content_type",
        "output_format",
        "pages",
        "char_count",
        "status",
    ]
    with manifest_csv.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            writer.writerow({k: record.get(k, "") for k in fieldnames})

    md_lines = [
        "# Sarvam AI — Document OCR Catalog",
        "",
        "> Generated by **Sarvam AI Standalone Document OCR & Parser**.",
        "",
        f"* **Total Documents:** {len(records)}",
        f"* **Language:** {args.language}",
        f"* **Content Type:** {args.content_type}",
        f"* **Output Format:** {args.output_format}",
        f"* **Root Directory:** `{root_dir}`",
        f"* **Generated:** {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "| Source | Output | Lang | Content | Format | Pages | Chars | Status |",
        "| :--- | :--- | :--- | :--- | :--- | ---: | ---: | :--- |",
    ]
    for record in records:
        md_lines.append(
            "| `{source}` | `{output}` | {lang} | {content} | {fmt} | "
            "{pages} | {chars} | {status} |".format(
                source=record.get("source_path", ""),
                output=record.get("output_file", ""),
                lang=record.get("language", ""),
                content=record.get("content_type", ""),
                fmt=record.get("output_format", ""),
                pages=record.get("pages", 0),
                chars=record.get("char_count", 0),
                status=record.get("status", ""),
            )
        )
    md_lines.append("")
    catalog_md.write_text("\n".join(md_lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Digitize PDFs and images into structured text, tables, and "
            "Markdown/HTML with the Sarvam AI Document Intelligence API."
        )
    )

    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Path to a document or a folder to scan recursively (default: current directory).",
    )
    parser.add_argument(
        "--language",
        type=str,
        default=DEFAULT_LANGUAGE,
        help=f"Document language BCP-47 code (default: {DEFAULT_LANGUAGE}).",
    )
    parser.add_argument(
        "--output-format",
        type=str,
        choices=OUTPUT_FORMATS,
        default=DEFAULT_OUTPUT_FORMAT,
        help=f"Rendered output format (default: {DEFAULT_OUTPUT_FORMAT}).",
    )
    parser.add_argument(
        "--content-type",
        type=str,
        choices=CONTENT_TYPES,
        default=DEFAULT_CONTENT_TYPE,
        help=f"Document content type (default: {DEFAULT_CONTENT_TYPE}).",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Optional Document Intelligence model to pass through (default: API default).",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        type=str,
        default=None,
        help="Output directory for OCR results (default: <path>/sarvam_ocr_output).",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY,
        help=f"Cooldown/poll interval in seconds between API calls (default: {DEFAULT_DELAY}).",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=DEFAULT_MAX_RETRIES,
        help=f"Maximum retries on 429 rate limits (default: {DEFAULT_MAX_RETRIES}).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"Overall per-document polling timeout in seconds (default: {DEFAULT_TIMEOUT}).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit the number of unprocessed documents to digitize in this run.",
    )
    parser.add_argument(
        "--cache-file",
        type=str,
        default=None,
        help="Path to the resumable cache file (default: <output-dir>/.sarvam_ocr_cache.json).",
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
    if args.output_format not in OUTPUT_FORMATS:
        return (
            f"--output-format '{args.output_format}' is invalid. "
            f"Choose one of: {', '.join(OUTPUT_FORMATS)}."
        )
    if args.content_type not in CONTENT_TYPES:
        return (
            f"--content-type '{args.content_type}' is invalid. "
            f"Choose one of: {', '.join(CONTENT_TYPES)}."
        )
    if args.max_retries <= 0:
        return "--max-retries must be greater than 0."
    if args.delay < 0:
        return "--delay must not be negative."
    if args.timeout <= 0:
        return "--timeout must be greater than 0."
    if args.limit is not None and args.limit <= 0:
        return "--limit must be greater than 0."
    if not Path(args.path).exists():
        return f"path '{args.path}' does not exist."
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

    # Discover documents: a single file or a recursive folder scan.
    if input_path.is_file():
        root_dir = input_path.parent
        rel_paths = [Path(input_path.name)]
    else:
        root_dir = input_path
        rel_paths = find_document_files(input_path)

    if not rel_paths:
        print(f"No supported documents found in: {input_path}")
        print("Supported extensions: " + ", ".join(sorted(DOC_EXTENSIONS)))
        return 0

    output_dir = (
        Path(args.output_dir).resolve() if args.output_dir else root_dir / OUTPUT_DIR
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    STATE.setup_signals()
    STATE.cache_path = (
        str(Path(args.cache_file).resolve())
        if args.cache_file
        else str(output_dir / CACHE_FILE)
    )
    if os.path.exists(STATE.cache_path):
        try:
            with open(STATE.cache_path, "r", encoding="utf-8") as f:
                STATE.cache = json.load(f)
            print(f"Loaded {len(STATE.cache)} cached result(s) from '{CACHE_FILE}'.")
        except Exception as e:
            print(f"[!] Warning reading cache file: {e}")
            STATE.cache = {}

    # Compute content hashes for dedup and resumable caching.
    file_hashes: dict[str, str] = {}
    for rel in rel_paths:
        full = root_dir / rel
        try:
            file_hashes[str(rel)] = compute_file_hash(full)
        except Exception as exc:
            print(f"  ✗ {rel}: could not read file ({exc})")

    # Determine which unique (hash, params) keys still need processing.
    needed: dict[str, Path] = {}
    for rel in rel_paths:
        h = file_hashes.get(str(rel))
        if not h:
            continue
        key = cache_key(h, args.output_format, args.content_type)
        if key not in STATE.cache and key not in needed:
            needed[key] = rel

    total_docs = len([r for r in rel_paths if str(r) in file_hashes])
    cached_count = total_docs - len(needed)

    if args.limit is not None and len(needed) > args.limit:
        limited = list(needed.items())[: args.limit]
        needed = dict(limited)

    print("=======================================================")
    print(" Sarvam AI Document OCR & Parser")
    print(f" Root Directory : {root_dir}")
    print(f" Output Dir     : {output_dir}")
    print(f" Language       : {args.language}")
    print(f" Content Type   : {args.content_type}")
    print(f" Output Format  : {args.output_format}")
    print(f" Model          : {args.model or 'default'}")
    print(f" Documents      : {total_docs} (cached: {cached_count})")
    print(f" To Process     : {len(needed)}")
    print("=======================================================")
    print()

    client = SarvamAI(api_subscription_key=api_key)

    # Process each needed document, flushing the cache after each success.
    for processed, (key, rel) in enumerate(needed.items(), start=1):
        if STATE.interrupted:
            break
        full = root_dir / rel
        print(f"[{processed}/{len(needed)}] {rel}")
        try:
            payload = digitise_document(client, full, args)
            STATE.cache[key] = payload
            STATE.flush_cache()
            print(f"  ✓ {rel} ({payload.get('page_count', 0)} page(s))")
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            print(f"  ✗ {rel}: {exc}")
        if args.delay > 0:
            time.sleep(args.delay)

    # Build outputs for every discovered document from the cache.
    records: list[dict[str, Any]] = []
    successful = 0
    failed = 0

    for rel in rel_paths:
        h = file_hashes.get(str(rel))
        if not h:
            failed += 1
            records.append(
                {
                    "source_path": str(rel),
                    "output_file": "",
                    "language": args.language,
                    "content_type": args.content_type,
                    "output_format": args.output_format,
                    "pages": 0,
                    "char_count": 0,
                    "status": "failed: unreadable",
                }
            )
            continue

        key = cache_key(h, args.output_format, args.content_type)
        payload = STATE.cache.get(key)
        if payload is None:
            failed += 1
            records.append(
                {
                    "source_path": str(rel),
                    "output_file": "",
                    "language": args.language,
                    "content_type": args.content_type,
                    "output_format": args.output_format,
                    "pages": 0,
                    "char_count": 0,
                    "status": "not processed",
                }
            )
            continue

        try:
            stem = rel.stem
            text_path, char_count = write_document_outputs(
                stem, rel, payload, args, output_dir
            )
            successful += 1
            records.append(
                {
                    "source_path": str(rel),
                    "output_file": text_path.name,
                    "language": args.language,
                    "content_type": args.content_type,
                    "output_format": args.output_format,
                    "pages": int(payload.get("page_count") or 0),
                    "char_count": char_count,
                    "status": "ok",
                }
            )
        except Exception as exc:
            failed += 1
            print(f"  ✗ {rel}: could not write outputs ({exc})")
            records.append(
                {
                    "source_path": str(rel),
                    "output_file": "",
                    "language": args.language,
                    "content_type": args.content_type,
                    "output_format": args.output_format,
                    "pages": int(payload.get("page_count") or 0),
                    "char_count": 0,
                    "status": f"failed: {exc}",
                }
            )

    write_aggregate_outputs(records, args, output_dir, root_dir)

    print()
    print("=== Finished ===")
    print(f"Successful : {successful}")
    print(f"Failed     : {failed}")
    print(f"Output     : {output_dir}")

    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
