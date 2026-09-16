# Sarvam AI API Tools Suite

[![Python Version](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Repository](https://img.shields.io/badge/GitHub-GeekLord%2Fsarvam--ai__api__tools-181717?logo=github)](https://github.com/GeekLord/sarvam-ai_api_tools)
[![Author](https://img.shields.io/badge/Author-Shobhit%20Kumar%20Prabhakar-orange)](https://github.com/GeekLord)

Python command-line tools for [Sarvam AI](https://www.sarvam.ai/) APIs. The suite currently includes five tools: speech-to-text audio transcription with speaker diarization, automated image renaming with metadata catalog generation, text translation across Indic languages and English, text-to-speech voice synthesis, and document OCR and parsing.

Each tool runs standalone, handles API rate limits with polite request cooldowns and backoff, and shares a single API key configuration. An optional [Gradio web front end](#web-front-end-apppy) (`app.py`) is also included so you can interactively test the Translation, Text-to-Speech, and Document OCR tools from your browser.

---

## Available Tools and Roadmap

| Tool | Script | API / Model | Status | Capabilities |
| :--- | :--- | :--- | :--- | :--- |
| **Speech Transcription and Diarization** | [`transcribe_sarvam.py`](file:///h:/Desktop/sarvam-ai_api_tools/transcribe_sarvam.py) | Sarvam Speech-to-Text (`saaras:v4`) | Active | Multilingual speech transcription, speaker diarization (auto or 1-20 speakers), formatted millisecond dialogue transcripts (`.txt`), chronological timeline spreadsheets (`.csv`), and raw API payloads (`.json`). |
| **Image Renamer and Metadata Cataloger** | [`sarvam_image_renamer.py`](file:///h:/Desktop/sarvam-ai_api_tools/sarvam_image_renamer.py) | Sarvam Document Intelligence | Active | Recursive image scanning, MD5 hash deduplication, descriptive slug generation, non-destructive renaming, rollback with history logs, and Markdown/CSV/JSON catalogs. |
| **Text Translation** | [`sarvam_translate.py`](file:///h:/Desktop/sarvam-ai_api_tools/sarvam_translate.py) | Sarvam Translation (`mayura:v1`) | Active | Batch and literal-text translation across Indic languages and English, `auto` source detection, register/script/numeral controls, long-text chunking, resumable cache, and Markdown/CSV/JSON exports. |
| **Text-to-Speech (TTS)** | [`sarvam_tts.py`](file:///h:/Desktop/sarvam-ai_api_tools/sarvam_tts.py) | Sarvam Voice Synthesis (`bulbul:v3`) | Active | Voice synthesis with selectable speaker personas, multiple codecs and sample rates, automatic long-text chunking, and per-unit audio plus Markdown/CSV/JSON catalogs. |
| **Document OCR and Parser** | [`sarvam_doc_ocr.py`](file:///h:/Desktop/sarvam-ai_api_tools/sarvam_doc_ocr.py) | Sarvam Document Intelligence | Active | Digitizes PDFs and images into Markdown/HTML/text with table parsing, MD5 deduplication, resumable cache, polling with timeout, and Markdown/CSV/JSON catalogs. |

---

## System Requirements

* Python 3.8 or higher
* An active [Sarvam AI API subscription key](https://dashboard.sarvam.ai/)

---

## Installation and API Key Setup

### 1. Clone the repository
```bash
git clone https://github.com/GeekLord/sarvam-ai_api_tools.git
cd sarvam-ai_api_tools
```

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Configure your API key
Each script checks for your key in this order:
1. Command-line flag: `--api-key "<KEY>"`
2. Environment variable: `SARVAM_API_KEY`
3. `.env` file in the target directory, current directory, or script root directory.

#### Option A: `.env` file (recommended)
Copy the sample file to `.env`:
```bash
# Linux / macOS / Git Bash
cp .env.sample .env

# Windows PowerShell
Copy-Item .env.sample .env
```
Edit `.env` and set your key:
```ini
SARVAM_API_KEY=your_sarvam_api_key_here
```

#### Option B: Environment variable
```bash
# Linux / macOS / Git Bash
export SARVAM_API_KEY="your_sarvam_api_key_here"

# Windows PowerShell
$env:SARVAM_API_KEY="your_sarvam_api_key_here"

# Windows Command Prompt
set SARVAM_API_KEY=your_sarvam_api_key_here
```

#### Option C: Command-line argument
Pass `--api-key` directly when running any script:
```bash
python transcribe_sarvam.py --api-key "your_api_key_here"
python sarvam_image_renamer.py --api-key "your_api_key_here"
python sarvam_translate.py --target hi-IN --api-key "your_api_key_here"
python sarvam_tts.py --language hi-IN --api-key "your_api_key_here"
python sarvam_doc_ocr.py --api-key "your_api_key_here"
```

---

## Tool 1: Speech Transcription and Diarization (`transcribe_sarvam.py`)

`transcribe_sarvam.py` transcribes audio files using Sarvam's `saaras:v4` speech model, identifies distinct speakers through diarization, timestamps each speech turn to the millisecond, and writes outputs in three formats.

### Supported audio formats
`.wav`, `.mp3`, `.aac`, `.m4a`, `.mp4`, `.flac`, `.ogg`, `.opus`, `.aiff`, `.aif`, `.amr`, `.wma`, `.webm`

### Features
* **Speaker diarization**: Automatically detects speaker turns or constrains detection to a known number of speakers (`--speakers 1-20`).
* **Three output formats per audio file**:
  1. `[filename].txt`: Text dialogue with speaker labels and timestamps:
     ```
     [00:00:01.200 - 00:00:04.550] Speaker 1: Good morning and welcome to the session.
     [00:00:04.800 - 00:00:08.100] Speaker 2: Thank you, glad to be here.
     ```
  2. `[filename].csv`: Spreadsheet with columns for `speaker`, `start_time_seconds`, `end_time_seconds`, `start_time`, `end_time`, and `transcript`.
  3. `[filename].json`: The complete raw response from the Sarvam AI API.
* **Batch processing**: Groups files into batches (`--batch-size 20`), isolates uploads in temporary directories, and continues processing remaining files if an individual file fails.

### Usage examples

```bash
# Transcribe all audio files in the current folder (automatic speaker detection)
python transcribe_sarvam.py

# Transcribe files in a specific folder
python transcribe_sarvam.py "C:\Recordings\Interviews"

# Set an expected speaker count for a two-person interview
python transcribe_sarvam.py "C:\Recordings\Interviews" --speakers 2

# Save outputs to a custom folder and process 10 files per batch
python transcribe_sarvam.py "C:\Recordings" -o "C:\Transcripts" --batch-size 10

# Provide the API key directly
python transcribe_sarvam.py "C:\Recordings" --api-key "your_api_key_here"
```

### Options reference

| Parameter | Short | Default | Description |
| :--- | :--- | :--- | :--- |
| `folder` | | `.` (Current Dir) | Folder containing audio files to transcribe |
| `--speakers` | | `None` (Auto) | Number of speakers to detect (1-20) |
| `--output-dir` | `-o` | `<folder>/sarvam_transcripts` | Destination folder for transcripts |
| `--batch-size` | | `20` | Audio files uploaded per API batch job |
| `--api-key` | | Env / `.env` | Sarvam AI subscription key |

### Output folder structure

```
sarvam_transcripts/
├── interview_01.txt      # Formatted transcript with speaker timestamps
├── interview_01.csv      # CSV timeline for spreadsheets
└── interview_01.json     # Complete raw Sarvam API response
```

---

## Tool 2: Image Renamer and Metadata Cataloger (`sarvam_image_renamer.py`)

`sarvam_image_renamer.py` scans directories recursively, uses Sarvam AI's Document Intelligence API to analyze scene content, generates descriptive filenames, and exports structured catalogs.

### Features
* **MD5 deduplication**: Computes content hashes so duplicate photos across folders only trigger an API call once.
* **Resumable cache**: Stores descriptions in `.sarvam_cache.json`. If interrupted, rerun the command to pick up where you left off.
* **Rate-limit protection**: Pauses between requests (default 5.0 seconds), uses a single worker thread by default, and retries with exponential backoff on HTTP 429.
* **Collision prevention**: Appends numbered suffixes (`-02.jpg`, `-03.jpg`) per subdirectory if generated names collide.
* **Reversible changes**: Every rename is logged to `rename_history.json`. Run `--undo` at any point to restore original filenames.
* **Catalog exports**: Produces `IMAGE_CATALOG.md`, `image_manifest.json`, and `image_manifest.csv` for importing into spreadsheets or CMS media libraries.

### Usage examples

```bash
# Preview mode: analyzes and caches images without renaming files (default)
python sarvam_image_renamer.py "E:\Photos\JobSites"

# Process up to 15 unanalyzed images with a 5-second delay between calls
python sarvam_image_renamer.py "E:\Photos\JobSites" --limit 15 --delay 5.0

# Rename files in place after reviewing the preview catalog
python sarvam_image_renamer.py "E:\Photos\JobSites" --mode rename

# Copy renamed files to a separate renamed/ folder without altering originals
python sarvam_image_renamer.py "E:\Photos\JobSites" --mode copy

# Rebuild catalogs from the existing cache without making API calls
python sarvam_image_renamer.py "E:\Photos\JobSites" --mode catalog-only

# Revert all renamed files back to their original names
python sarvam_image_renamer.py "E:\Photos\JobSites" --undo
```

### Options reference

| Option | Short | Default | Description |
| :--- | :--- | :--- | :--- |
| `path` / `--dir` | `-d` | Current Dir | Directory containing images to scan |
| `--mode` | | `dry-run` | Action: `dry-run`, `rename` (in-place), `copy` (to `/renamed/`), or `catalog-only` |
| `--delay` | | `5.0` | Seconds to wait between API requests |
| `--workers` | | `1` | Concurrent worker threads |
| `--limit` | | `None` (All) | Maximum unanalyzed images to process in this run |
| `--max-words` | | `5` | Maximum words in generated filenames |
| `--max-retries` | | `5` | Retry attempts on HTTP 429 errors |
| `--api-key` | | Env / `.env` | Sarvam AI subscription key |
| `--output-dir` | | Target Dir | Output directory for catalog files |
| `--cache-file` | | `.sarvam_cache.json` | Path to JSON cache file |
| `--history-file` | | `rename_history.json`| Path to rename log for undo operations |
| `--force` | | `False` | Proceed with renaming even if unanalyzed images remain |
| `--undo` / `--rollback` | | `False` | Restore original filenames using the history log |

---

## Tool 3: Text Translation (`sarvam_translate.py`)

`sarvam_translate.py` translates text files or literal strings across Indic languages and English using Sarvam AI's Translation API (`mayura:v1` by default, with `sarvam-translate:v1` also supported). It discovers `.txt` and `.md` files in a folder (non-recursive) or translates a single file or a literal `--text` string, automatically chunking inputs that exceed the model character limit.

### Features
* **Batch or literal translation**: Point at a folder to translate every `.txt`/`.md` file, target a single file, or pass `--text "..."` for a one-off string.
* **Automatic source detection**: Uses `auto` source detection by default (`mayura:v1`), or set `--source` to a specific BCP-47 code.
* **Fine-grained output controls**: Choose translation register with `--mode`, transliteration style with `--output-script`, numeral style with `--numerals-format`, and a `--speaker-gender` hint for gendered translations.
* **Long-text chunking**: Inputs above the model character limit (1000 characters for `mayura:v1`) are split on paragraph and sentence boundaries so nothing is silently truncated.
* **Resumable cache**: Stores results in `.sarvam_translate_cache.json` with atomic writes. If interrupted, rerun to resume without re-spending quota.
* **Rate-limit protection**: Polite cooldown between calls (`--delay`, default 1.0 seconds) and exponential backoff with jitter on HTTP 429, capped by `--max-retries`.
* **Multi-format export**: Per-file `.txt` and `.json` outputs plus an aggregate `translations_manifest.json`, `translations_manifest.csv`, and a `TRANSLATIONS.md` Markdown catalog.

### Usage examples

```bash
# Translate every .txt/.md file in the current folder to Hindi
python sarvam_translate.py --target hi-IN

# Translate a literal string to Tamil
python sarvam_translate.py --text "Good morning, welcome to the session." --target ta-IN

# Translate a specific file with an explicit source language and formal register
python sarvam_translate.py "docs/notes.txt" --source en-IN --target bn-IN --mode formal
```

```bash
# Linux / macOS / Git Bash: translate a folder to a custom output directory
python sarvam_translate.py "/home/user/articles" --target te-IN -o "/home/user/translated"
```

```powershell
# Windows PowerShell: translate a folder to a custom output directory
python sarvam_translate.py "C:\Users\me\articles" --target te-IN -o "C:\Users\me\translated"
```

### Options reference

| Parameter | Short | Default | Description |
| :--- | :--- | :--- | :--- |
| `path` | | `.` (Current Dir) | Path to a text file or a folder of `.txt`/`.md` files |
| `--text` | | `None` | Translate this literal text instead of reading files |
| `--source` / `--source-language` | | `auto` | Source language BCP-47 code, or `auto` to detect |
| `--target` / `--target-language` | | Required | Target language BCP-47 code, e.g. `hi-IN` |
| `--model` | | `mayura:v1` | Translation model (`mayura:v1` or `sarvam-translate:v1`) |
| `--mode` | | Model default | Register: `formal`, `modern-colloquial`, `classic-colloquial`, `code-mixed` |
| `--output-script` | | Model default | Transliteration style: `roman`, `fully-native`, `spoken-form-in-native` |
| `--numerals-format` | | Model default | Numeral style: `international` or `native` |
| `--speaker-gender` | | `None` | Speaker gender hint: `Male` or `Female` |
| `--output-dir` | `-o` | `<path>/sarvam_translations` | Output directory for translations |
| `--delay` | | `1.0` | Cooldown in seconds between API calls |
| `--max-retries` | | `5` | Maximum retries on HTTP 429 rate limits |
| `--api-key` | | Env / `.env` | Sarvam AI subscription key |

### Output folder structure

```
sarvam_translations/
├── notes.hi-IN.txt                 # Translated text per source file
├── notes.hi-IN.json                # Raw Sarvam API response per source file
├── translations_manifest.json      # Aggregate manifest of all translations
├── translations_manifest.csv       # CSV manifest for spreadsheets
├── TRANSLATIONS.md                 # Markdown catalog index
└── .sarvam_translate_cache.json    # Resumable MD5-keyed cache
```

---

## Tool 4: Text-to-Speech (`sarvam_tts.py`)

`sarvam_tts.py` synthesizes speech from text files or literal strings across Indic languages and English using Sarvam AI's Text-to-Speech API (`bulbul:v3` by default, with `bulbul:v2` also supported). It discovers `.txt` and `.md` files in a folder (non-recursive) or synthesizes a single file or a literal `--text` string, chunking long inputs automatically.

### Features
* **Selectable speakers**: Choose any persona with `--speaker` (defaults to `shubh` for `bulbul:v3`). Run `--list-speakers` to print the available speakers per model.
* **Model-aware controls**: `--pace` for both models, plus `--pitch` and `--loudness` and `--enable-preprocessing` for `bulbul:v2`, and `--temperature` for `bulbul:v3`.
* **Flexible audio output**: Pick the sample rate with `--sample-rate` (8000-48000 Hz) and the codec with `--codec` (`wav`, `mp3`, `flac`, `aac`, `opus`, `linear16`, `mulaw`, `alaw`).
* **Long-text chunking**: Inputs above the model character limit (2500 characters for `bulbul:v3`, 1500 for `bulbul:v2`) are split on paragraph and sentence boundaries.
* **Rate-limit protection**: Polite cooldown between calls (`--delay`, default 1.0 seconds) and exponential backoff with jitter on HTTP 429, capped by `--max-retries`.
* **Graceful shutdown**: A SIGINT/SIGTERM handler flushes the run manifest before exiting.
* **Multi-format export**: Per-unit audio file and `.json` metadata plus an aggregate `tts_manifest.json`, `tts_manifest.csv`, and a `TTS_CATALOG.md` Markdown catalog.

### Usage examples

```bash
# List the available speakers for each model and exit
python sarvam_tts.py --list-speakers

# Synthesize a literal string in Hindi with the default speaker
python sarvam_tts.py --text "नमस्ते और स्वागत है।" --language hi-IN

# Synthesize every .txt/.md file in a folder with a chosen speaker and codec
python sarvam_tts.py "scripts/" --language ta-IN --speaker anushka --codec mp3
```

```bash
# Linux / macOS / Git Bash: synthesize a file to a custom output directory
python sarvam_tts.py "/home/user/story.txt" --language te-IN -o "/home/user/audio"
```

```powershell
# Windows PowerShell: synthesize a file to a custom output directory
python sarvam_tts.py "C:\Users\me\story.txt" --language te-IN -o "C:\Users\me\audio"
```

### Options reference

| Parameter | Short | Default | Description |
| :--- | :--- | :--- | :--- |
| `path` | | `.` (Current Dir) | Path to a text file or a folder of `.txt`/`.md` files |
| `--text` | | `None` | Synthesize this literal text instead of reading files |
| `--language` / `--language-code` | | Required | Target language BCP-47 code, e.g. `hi-IN` |
| `--speaker` | | Model default (`shubh` for `bulbul:v3`) | Speaker persona |
| `--model` | | `bulbul:v3` | TTS model (`bulbul:v2` or `bulbul:v3`) |
| `--pace` | | `1.0` | Speaking pace; v3 range 0.5-2.0, v2 range 0.3-3.0 |
| `--pitch` | | Model default | Voice pitch adjustment (`bulbul:v2` only) |
| `--loudness` | | Model default | Loudness adjustment (`bulbul:v2` only) |
| `--temperature` | | Model default | Sampling temperature (`bulbul:v3` only) |
| `--sample-rate` | | `22050` | Audio sample rate in Hz (8000, 16000, 22050, 24000, 32000, 44100, 48000) |
| `--codec` / `--output-audio-codec` | | `wav` | Output codec: `wav`, `mp3`, `flac`, `aac`, `opus`, `linear16`, `mulaw`, `alaw` |
| `--enable-preprocessing` | | `False` | Enable text normalization/preprocessing (`bulbul:v2` only) |
| `--list-speakers` | | `False` | List the available speakers per model and exit |
| `--output-dir` | `-o` | `<path>/sarvam_tts_audio` | Output directory for generated audio |
| `--delay` | | `1.0` | Cooldown in seconds between API calls |
| `--max-retries` | | `5` | Maximum retries on HTTP 429 rate limits |
| `--api-key` | | Env / `.env` | Sarvam AI subscription key |

### Output folder structure

```
sarvam_tts_audio/
├── story.wav              # Generated audio per source unit (extension follows --codec)
├── story.json            # Synthesis metadata per source unit
├── tts_manifest.json     # Aggregate manifest of all synthesized audio
├── tts_manifest.csv      # CSV manifest for spreadsheets
└── TTS_CATALOG.md        # Markdown catalog index
```

---

## Tool 5: Document OCR and Parser (`sarvam_doc_ocr.py`)

`sarvam_doc_ocr.py` digitizes PDFs and images into structured text, tables, and Markdown or HTML using Sarvam AI's Document Intelligence API. It scans a folder recursively (or targets a single document), submits a `digitise` job, polls for completion, and renders the results in the requested format.

### Supported document formats
`.pdf`, `.png`, `.jpg`, `.jpeg`, `.tiff`, `.tif`, `.bmp`, `.webp`

### Features
* **Recursive scanning**: Point at a folder to digitize every supported document, or target a single file.
* **Content-addressable deduplication**: Jobs are cached by MD5 hash plus output format and content type, so duplicate documents cost a single API call.
* **Resumable cache**: Stores results in `.sarvam_ocr_cache.json` (under the output directory by default) with atomic writes. Reruns skip completed documents.
* **Configurable output**: Choose `--output-format` (`md`, `html`, or `json`) and `--content-type` (`printed`, `handwritten`, or `mixed`), and pass an optional `--model`.
* **Robust polling**: The `digitise` job is polled with a `--delay` interval and an overall per-document `--timeout`.
* **Rate-limit protection**: Exponential backoff with jitter on HTTP 429, capped by `--max-retries`.
* **Multi-format export**: Per-document text (`.md`/`.html`/`.txt`) and raw `.json` plus an aggregate `ocr_manifest.json`, `ocr_manifest.csv`, and an `OCR_CATALOG.md` Markdown index.

### Usage examples

```bash
# Digitize every supported document in the current folder to Markdown
python sarvam_doc_ocr.py

# Digitize a single PDF as HTML for handwritten content
python sarvam_doc_ocr.py "invoice.pdf" --output-format html --content-type handwritten

# Limit a run to the first 5 unprocessed documents with a longer poll interval
python sarvam_doc_ocr.py "scans/" --limit 5 --delay 5.0
```

```bash
# Linux / macOS / Git Bash: digitize a folder to a custom output directory
python sarvam_doc_ocr.py "/home/user/scans" -o "/home/user/ocr_out" --language hi-IN
```

```powershell
# Windows PowerShell: digitize a folder to a custom output directory
python sarvam_doc_ocr.py "C:\Users\me\scans" -o "C:\Users\me\ocr_out" --language hi-IN
```

### Options reference

| Parameter | Short | Default | Description |
| :--- | :--- | :--- | :--- |
| `path` | | `.` (Current Dir) | Path to a document or a folder to scan recursively |
| `--language` | | `en-IN` | Document language BCP-47 code |
| `--output-format` | | `md` | Rendered output format: `html`, `md`, or `json` |
| `--content-type` | | `printed` | Document content type: `printed`, `handwritten`, or `mixed` |
| `--model` | | API default | Optional Document Intelligence model to pass through |
| `--output-dir` | `-o` | `<path>/sarvam_ocr_output` | Output directory for OCR results |
| `--delay` | | `3.0` | Cooldown/poll interval in seconds between API calls |
| `--max-retries` | | `5` | Maximum retries on HTTP 429 rate limits |
| `--timeout` | | `600.0` | Overall per-document polling timeout in seconds |
| `--limit` | | `None` (All) | Limit the number of unprocessed documents to digitize |
| `--cache-file` | | `<output-dir>/.sarvam_ocr_cache.json` | Path to the resumable cache file |
| `--api-key` | | Env / `.env` | Sarvam AI subscription key |

### Output folder structure

```
sarvam_ocr_output/
├── invoice.md              # Digitized text per document (.md/.html/.txt by format)
├── invoice.json            # Raw Sarvam API response per document
├── ocr_manifest.json       # Aggregate manifest of all digitized documents
├── ocr_manifest.csv        # CSV manifest for spreadsheets
├── OCR_CATALOG.md          # Markdown catalog index
└── .sarvam_ocr_cache.json  # Resumable MD5-keyed cache
```

---

## Web Front End (`app.py`)

`app.py` is an optional [Gradio](https://www.gradio.app/) web UI that lets you interactively test the suite's tools from your browser instead of the command line. It is an additive layer: rather than reimplementing anything, it imports each tool script's existing worker functions and constants and calls them in-process, so the "one tool = one script" design of the CLIs is untouched.

### Features
* **Three tabs**: The UI exposes the tools that operate on a single input:
  * **Translate**: source and target language, model, mode, output script, numerals format, and speaker gender, returning the translated text and the detected source language.
  * **Text-to-Speech**: language, model, speaker (refreshed per model), sample rate, codec, and pace, plus `bulbul:v3` temperature and `bulbul:v2` pitch, loudness, and enable-preprocessing, with in-browser audio playback and download.
  * **Document OCR**: file upload with language, output format, content type, and an optional model, rendering the extracted content as Markdown alongside a page-count/job summary.
* **CLI tools only**: Speech Transcription (`transcribe_sarvam.py`) and the Image Renamer (`sarvam_image_renamer.py`) are batch/folder oriented and remain command-line only; they are intentionally not exposed in the UI.
* **API-key field with fallback**: A password field accepts your Sarvam AI key. Leave it blank to fall back to the same `SARVAM_API_KEY` environment variable or `.env` file the CLIs use. The key is never logged or echoed.
* **Non-destructive**: Generated audio and uploads are handled in temporary files; your inputs are never modified.

### Installation
The front end ships as part of the standard dependencies, so the usual install now also pulls in `gradio`:
```bash
pip install -r requirements.txt
```

> **Python 3.13 note:** The front end uses `gradio` 5.x, which works on Python 3.8 through 3.13. On Python 3.13 the stdlib `audioop`/`pyaudioop` modules were removed (PEP 594), so `requirements.txt` automatically installs the `audioop-lts` backport there. No manual steps are needed: `pip install -r requirements.txt` is enough on every supported version.

### Launching the app
Start the local server (the launch command is the same on every platform):
```bash
python app.py
```
Then open the printed local URL in your browser (by default `http://127.0.0.1:7860`). Press `Ctrl + C` in the terminal to stop the server.

---

## Project Structure

```
sarvam-ai_api_tools/
├── .env.sample               # Environment variable configuration template
├── .gitignore                # Excludes secrets, caches, and generated files
├── requirements.txt          # Python dependencies
├── README.md                 # Project documentation
├── app.py                    # Gradio web front end to test the tools
├── sarvam_doc_ocr.py         # Tool: Document OCR and Parser
├── sarvam_image_renamer.py   # Tool: Image Renamer and Metadata Cataloger
├── sarvam_translate.py       # Tool: Text Translation
├── sarvam_tts.py             # Tool: Text-to-Speech
└── transcribe_sarvam.py      # Tool: Speech Transcription and Diarization
```

---

## Security and Best Practices

1. **Keep keys private**: Never commit `.env` or API credentials. The repository's `.gitignore` excludes `.env` and local cache files.
2. **Quota limits**: Sarvam AI enforces rate limits on concurrent and per-minute requests. Both scripts default to sequential runs with retry backoff.
3. **Clean exit**: Pressing `Ctrl + C` flushes the current cache state to disk before exiting.

---

## Adding New Tools to the Suite

When contributing a new tool to this repository:
1. Use `get_api_key(cli_key, target_dir)` to preserve the three-tier resolution order (CLI flag, environment variable, `.env` file).
2. Add `--api-key` to your argument parser.
3. Use non-destructive preview defaults for any tool that modifies files on disk.
4. Keep output artifacts organized in dedicated folders and ensure new artifact types are covered in `.gitignore`.
5. Document parameters and examples in this `README.md`.

---

## Author and Attribution

* **Author**: [Shobhit Kumar Prabhakar](https://github.com/GeekLord) ([@GeekLord](https://github.com/GeekLord))
* **Repository**: [https://github.com/GeekLord/sarvam-ai_api_tools](https://github.com/GeekLord/sarvam-ai_api_tools)
* **API Documentation**: [Sarvam AI Docs](https://docs.sarvam.ai/api/getting-started/welcome)

---

## License

This project is licensed under the **MIT License** - see the [LICENSE](LICENSE) file for details.
