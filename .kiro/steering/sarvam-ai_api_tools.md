---
inclusion: always
---

# Sarvam AI API Tools

A suite of standalone Python CLI tools that wrap [Sarvam AI](https://docs.sarvam.ai/) APIs. Each tool is a single self-contained script at the repo root, runnable from any working directory, and shares only a common API key convention — not a shared package.

## Current Tools

| Script | API / Model | Purpose |
| :--- | :--- | :--- |
| `transcribe_sarvam.py` | Speech-to-Text (`saaras:v4`) | Batch audio transcription with speaker diarization; emits `.txt`, `.csv`, `.json` per file into `sarvam_transcripts/` |
| `sarvam_image_renamer.py` | Document Intelligence | Recursive image analysis, slug renaming, and `IMAGE_CATALOG.md` / `image_manifest.{json,csv}` catalogs |
| `sarvam_translate.py` | Translation (`mayura:v1`) | Batch/literal text translation across Indic languages and English; emits per-file `.txt`/`.json`, `translations_manifest.{json,csv}`, and `TRANSLATIONS.md` into `sarvam_translations/` |
| `sarvam_tts.py` | Text-to-Speech (`bulbul:v3`) | Voice synthesis with selectable speakers, codecs, and sample rates; emits per-unit audio + `.json`, `tts_manifest.{json,csv}`, and `TTS_CATALOG.md` into `sarvam_tts_audio/` |
| `sarvam_doc_ocr.py` | Document Intelligence | Digitizes PDFs/images to Markdown/HTML/text with table parsing; emits per-document text + `.json`, `ocr_manifest.{json,csv}`, and `OCR_CATALOG.md` into `sarvam_ocr_output/` |

## Architecture Rules

- **One tool = one script.** Do not introduce packages, shared modules, or `src/` layouts. Duplicating a helper such as `get_api_key` across scripts is intentional and correct.
- **Portable by default.** Resolve the target directory from a positional arg (default: cwd), then `Path(...).resolve()`. Never assume the script lives next to the data.
- **Stdlib first.** Dependencies are limited to `sarvamai`, `python-dotenv`, `requests`, and `pillow`. Adding a dependency requires updating `requirements.txt` with a `>=` floor and a comment marking which tool needs it.
- **Optional imports are guarded.** Wrap non-critical imports in `try/except ImportError` and set a `HAS_*` flag (`HAS_SARVAM`, `HAS_PIL`); `load_dotenv()` failure must never crash the script.
- **Module-level constants** for extension sets, model names, defaults, and output directory names — declared in UPPER_SNAKE at the top of the file, not inlined at call sites.

## API Key Handling

Always resolve credentials through `get_api_key(cli_key, target_dir)` with this exact precedence:

1. `--api-key` CLI flag
2. `SARVAM_API_KEY` environment variable
3. `.env` in target dir → cwd → script dir

Every tool must expose `--api-key`. Instantiate with `SarvamAI(api_subscription_key=api_key)`. Never hardcode, log, or echo key values. When a key is missing, print the three resolution options and exit non-zero.

## CLI Conventions

- `argparse` with a positional path (`nargs="?"`), long flags in kebab-case, and short aliases only where already established (`-o`, `-d`).
- Every `help=` string states the default in parentheses.
- Validate ranges and paths in `main()` before any network call, printing a single `ERROR:`/`[!] Error:` line per failure.
- Exit codes: `0` success, `1` configuration or usage error, `2` partial failure (some items processed, some failed), `130` on interrupt.
- `transcribe_sarvam.py` returns an int from `main()` and uses `sys.exit(main())`; `sarvam_image_renamer.py` calls `sys.exit(n)` inline. Match the file you are editing rather than unifying them.

## Safety and Data Integrity

- **Non-destructive default.** Any tool that mutates files on disk defaults to preview (`--mode dry-run`) and requires an explicit mode to act.
- **Reversible mutations.** Log every rename to `rename_history.json` and provide `--undo` / `--rollback`.
- **Atomic writes.** Write to `<path>.tmp`, then `os.replace()` — used by cache flushing and required for any new state file.
- **Resumable caching.** Key long-running results by MD5 content hash in `.sarvam_cache.json` so duplicates cost one API call and reruns skip completed work.
- **Graceful interrupt.** Install SIGINT/SIGTERM handlers that flush state before exiting when a run can take minutes.
- **Collision safety.** Append numbered suffixes (`-02`, `-03`) per directory instead of overwriting.

## Rate Limiting

Sarvam enforces per-minute and concurrency quotas, so throttling is a first-class concern:

- Default to sequential work (`--workers 1`) and a `--delay` cooldown between calls.
- Detect 429 by substring check on the exception text, then retry with exponential backoff plus random jitter, capped by `--max-retries`.
- Batch job APIs follow the `create_job → upload_files → start → wait_until_complete → get_file_results → download_outputs` sequence. Stage uploads and downloads in `tempfile` directories and always clean them up.
- Treat SDK response field names as unreliable: read results through `.get()` chains with fallback key names and index-based matching, never direct subscripting.

## Error Handling and Output

- **Never abort a whole run for one item.** Catch per-file and per-batch exceptions, print `✗ <name>: <reason>`, count the failure, and continue.
- Progress reporting is `print()`-based, not `logging`. Keep the existing voice: a header block summarizing target, mode, and settings; `[n/total]` progress lines; a closing summary with counts and the resolved output path.
- Use `pathlib.Path` for path work in new code; existing `os.path` usage in `sarvam_image_renamer.py` may stay as-is.
- Docstrings: a module-level docstring describing the tool and its features, plus short docstrings on non-obvious functions. Document the expected API response shape where it is parsed.

## Adding a New Tool

1. Create a single root-level script following the conventions above.
2. Reuse `get_api_key` verbatim and wire up `--api-key`.
3. Write outputs to a dedicated folder or clearly named artifacts, and add those artifacts to `.gitignore`.
4. Document the tool in `README.md`: add a row to the tools table, a features section, usage examples, and a full options reference table.

## Environment Notes

Development happens on Windows with PowerShell. Provide both POSIX and PowerShell forms in docs and examples (`export` vs `$env:`, `cp` vs `Copy-Item`). Never commit `.env`; `.env.sample` is the tracked template.
