# Sarvam AI API Tools Suite

[![Python Version](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Repository](https://img.shields.io/badge/GitHub-GeekLord%2Fsarvam--ai__api__tools-181717?logo=github)](https://github.com/GeekLord/sarvam-ai_api_tools)
[![Author](https://img.shields.io/badge/Author-Shobhit%20Kumar%20Prabhakar-orange)](https://github.com/GeekLord)

Python command-line tools for [Sarvam AI](https://www.sarvam.ai/) APIs. Currently includes tools for speech-to-text audio transcription with speaker diarization and automated image renaming with metadata catalog generation.

Each tool runs standalone, handles API rate limits with polite request cooldowns and backoff, and shares a single API key configuration.

---

## Available Tools and Roadmap

| Tool | Script | API / Model | Status | Capabilities |
| :--- | :--- | :--- | :--- | :--- |
| **Speech Transcription and Diarization** | [`transcribe_sarvam.py`](file:///h:/Desktop/sarvam-ai_api_tools/transcribe_sarvam.py) | Sarvam Speech-to-Text (`saaras:v4`) | Active | Multilingual speech transcription, speaker diarization (auto or 1-20 speakers), formatted millisecond dialogue transcripts (`.txt`), chronological timeline spreadsheets (`.csv`), and raw API payloads (`.json`). |
| **Image Renamer and Metadata Cataloger** | [`sarvam_image_renamer.py`](file:///h:/Desktop/sarvam-ai_api_tools/sarvam_image_renamer.py) | Sarvam Document Intelligence | Active | Recursive image scanning, MD5 hash deduplication, descriptive slug generation, non-destructive renaming, rollback with history logs, and Markdown/CSV/JSON catalogs. |
| **Text Translation** | *Planned* | Sarvam Translation API (`mayura`) | Upcoming | Batch translation across Indic languages and English. |
| **Text-to-Speech (TTS)** | *Planned* | Sarvam Voice Synthesis (`bulbul`) | Upcoming | Voice synthesis with selectable speaker personas and audio export. |
| **Document OCR and Parser** | *Planned* | Sarvam Document Intelligence | Upcoming | Structured text extraction, table parsing, and PDF digitization. |

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
Pass `--api-key` directly when running either script:
```bash
python transcribe_sarvam.py --api-key "your_api_key_here"
python sarvam_image_renamer.py --api-key "your_api_key_here"
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

## Project Structure

```
sarvam-ai_api_tools/
├── .env.sample               # Environment variable configuration template
├── .gitignore                # Excludes secrets, caches, and generated files
├── requirements.txt          # Python dependencies
├── README.md                 # Project documentation
├── sarvam_image_renamer.py   # Tool: Image Renamer and Metadata Cataloger
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
