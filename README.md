# Sarvam AI API Tools Suite

[![Python Version](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Repository](https://img.shields.io/badge/GitHub-GeekLord%2Fsarvam--ai__api__tools-181717?logo=github)](https://github.com/GeekLord/sarvam-ai_api_tools)
[![Author](https://img.shields.io/badge/Author-Shobhit%20Kumar%20Prabhakar-orange)](https://github.com/GeekLord)

A modular, production-ready suite of Python CLI tools leveraging [Sarvam AI](https://www.sarvam.ai/) APIs for multimodal AI tasks—including Speech-to-Text audio transcription with speaker diarization, Document Intelligence, computer vision image analysis, and automated SEO cataloging.

Designed with standalone portability, resilient error handling, automatic rate-limit throttling, and unified configuration.

---

## 📦 Available Tools & Roadmap

The toolkit currently includes two primary command-line applications and is architected for continuous expansion with additional Sarvam AI API capabilities:

| Tool | Script | Primary API / Model | Status | Key Capabilities |
| :--- | :--- | :--- | :--- | :--- |
| **Speech Transcription & Diarization** | [`transcribe_sarvam.py`](file:///h:/Desktop/sarvam-ai_api_tools/transcribe_sarvam.py) | Sarvam Speech-to-Text (`saaras:v4`) | **Active** | Multilingual audio transcription, speaker diarization (auto or 1–20 speakers), formatted millisecond dialogue transcripts (`.txt`), chronological timeline spreadsheets (`.csv`), and raw API payloads (`.json`). |
| **Image Renamer & Metadata Cataloger** | [`sarvam_image_renamer.py`](file:///h:/Desktop/sarvam-ai_api_tools/sarvam_image_renamer.py) | Sarvam Document Intelligence / Vision | **Active** | Recursive image scanning, content-addressable MD5 deduplication, SEO slug generation, non-destructive renaming, fail-safe rollback, Markdown/CSV/JSON catalogs. |
| **Text Translation Tool** | *Planned* | Sarvam Translation API (`mayura`) | *Upcoming* | High-fidelity translation across Indic languages and English with batch file support. |
| **Text-to-Speech (TTS) Generator** | *Planned* | Sarvam Voice Synthesis (`bulbul`) | *Upcoming* | Natural Indic voice generation with customizable speaker personas and audio output formats. |
| **Document OCR & Parser** | *Planned* | Sarvam Document Intelligence | *Upcoming* | Structured text extraction, table extraction, and document digitization. |

---

## 📋 System Requirements

* Python **3.8+**
* An active [Sarvam AI API Subscription Key](https://dashboard.sarvam.ai/)

---

## 🚀 Installation & Unified Setup

### 1. Clone the Repository
```bash
git clone https://github.com/GeekLord/sarvam-ai_api_tools.git
cd sarvam-ai_api_tools
```

### 2. Install Dependencies
Install all required dependencies with `pip`:
```bash
pip install -r requirements.txt
```

### 3. Configure Your API Key
Every tool in this repository follows a unified 3-tier resolution priority:
1. **Explicit CLI Flag**: `--api-key "<KEY>"`
2. **Environment Variable**: `SARVAM_API_KEY`
3. **`.env` File Fallback**: Automatically loaded from target directory, current directory, or script root directory.

#### Option A: `.env` File (Recommended)
Copy the sample file to `.env`:
```bash
# Linux / macOS / Git Bash
cp .env.sample .env

# Windows PowerShell
Copy-Item .env.sample .env
```
Edit `.env` and paste your key:
```ini
SARVAM_API_KEY=your_sarvam_api_key_here
```

#### Option B: Environment Variable
```bash
# Linux / macOS / Git Bash
export SARVAM_API_KEY="your_sarvam_api_key_here"

# Windows PowerShell
$env:SARVAM_API_KEY="your_sarvam_api_key_here"

# Windows Command Prompt (cmd)
set SARVAM_API_KEY=your_sarvam_api_key_here
```

#### Option C: Command Line Argument
Pass `--api-key` directly when executing any script:
```bash
python transcribe_sarvam.py --api-key "your_api_key_here"
python sarvam_image_renamer.py --api-key "your_api_key_here"
```

---

## 🎙️ Tool 1: Speech Transcription & Diarization (`transcribe_sarvam.py`)

`transcribe_sarvam.py` transcribes audio recordings using the **Sarvam Saaras v4** speech model (`saaras:v4`), separates distinct speakers with diarization, provides millisecond timestamps, and generates multi-format deliverables.

### Supported Audio Formats
`.wav`, `.mp3`, `.aac`, `.m4a`, `.mp4`, `.flac`, `.ogg`, `.opus`, `.aiff`, `.aif`, `.amr`, `.wma`, `.webm`

### Key Highlights
* **Speaker Diarization Engine**: Automatically detects different speakers or accepts an explicit speaker count constraint (`--speakers 1-20`).
* **Multi-Format Deliverables**:
  1. `[filename].txt`: Human-readable transcript formatted with speaker dialogue turns and millisecond timestamps:
     ```
     [00:00:01.200 - 00:00:04.550] Speaker 1: Good morning and welcome to the session.
     [00:00:04.800 - 00:00:08.100] Speaker 2: Thank you, glad to be here.
     ```
  2. `[filename].csv`: Spreadsheet timeline containing `speaker`, `start_time_seconds`, `end_time_seconds`, `start_time`, `end_time`, and `transcript`.
  3. `[filename].json`: Full raw Sarvam AI response payload for programmatic use.
* **Batch Processing & Isolation**: Configurable batching (`--batch-size 20`), temp directory isolation, and failure recovery that keeps processing remaining files if one fails.

### Common Commands

```bash
# 1. Transcribe audio files in current folder (automatic speaker detection)
python transcribe_sarvam.py

# 2. Transcribe a specific folder
python transcribe_sarvam.py "C:\Recordings\Interviews"

# 3. Constrain speaker detection to an exact count (e.g., 2 speakers)
python transcribe_sarvam.py "C:\Recordings\Interviews" --speakers 2

# 4. Save outputs to a custom directory with a smaller batch size
python transcribe_sarvam.py "C:\Recordings" -o "C:\Transcripts" --batch-size 10

# 5. Pass API key via CLI flag
python transcribe_sarvam.py "C:\Recordings" --api-key "your_api_key_here"
```

### CLI Parameters Reference

| Parameter | Short | Default | Description |
| :--- | :--- | :--- | :--- |
| `folder` | | `.` (Current Dir) | Path to folder containing audio files to transcribe |
| `--speakers` | | `None` (Auto) | Expected number of speakers (1–20) for diarization |
| `--output-dir` | `-o` | `<folder>/sarvam_transcripts` | Output folder for generated transcripts |
| `--batch-size` | | `20` | Number of audio files processed per API batch |
| `--api-key` | | Env / `.env` | Sarvam AI API subscription key |

### Output Structure

```
sarvam_transcripts/
├── interview_01.txt      # Dialogue script with speaker timestamps
├── interview_01.csv      # Chronological CSV timeline (Excel / Sheets compatible)
└── interview_01.json     # Complete raw Sarvam API response payload
```

---

## 🛠️ Tool 2: Image Renamer & Metadata Cataloger (`sarvam_image_renamer.py`)

`sarvam_image_renamer.py` scans local folders recursively, analyzes images using Sarvam AI's Document Intelligence API, renames images with descriptive SEO slugs, and compiles structured catalog deliverables.

### Key Highlights
* **MD5 Deduplication**: Identical images across subfolders are analyzed only once.
* **Persistent Cache**: Results are saved to `.sarvam_cache.json` for pause-and-resume workflows.
* **Rate-Limit Resilience**: Configurable cooldown delay (default: 5.0s), single-worker execution by default, and exponential backoff with jitter on HTTP 429.
* **Collision-Safe Renaming**: Generates numbered suffixes (`-02.jpg`, `-03.jpg`) per subdirectory to prevent overwrites.
* **100% Reversible Rollback**: All actions are audited in `rename_history.json`. Roll back filenames anytime with `--undo`.
* **Multi-Format Catalogs**: Automatically generates `IMAGE_CATALOG.md`, `image_manifest.json`, and `image_manifest.csv`.

### Common Commands

```bash
# 1. Preview / Dry Run (Default: analyzes, caches, and compiles catalog without touching files)
python sarvam_image_renamer.py "E:\Photos\JobSites"

# 2. Throttled processing (process 15 images with 5s delay)
python sarvam_image_renamer.py "E:\Photos\JobSites" --limit 15 --delay 5.0

# 3. Perform in-place renaming (after reviewing dry-run catalog)
python sarvam_image_renamer.py "E:\Photos\JobSites" --mode rename

# 4. Non-destructive copy mode (exports renamed copies into 'renamed/' subfolder)
python sarvam_image_renamer.py "E:\Photos\JobSites" --mode copy

# 5. Offline catalog regeneration (rebuilds docs from cache without API calls)
python sarvam_image_renamer.py "E:\Photos\JobSites" --mode catalog-only

# 6. Complete rollback / Undo (restores original filenames from history)
python sarvam_image_renamer.py "E:\Photos\JobSites" --undo
```

### CLI Parameters Reference

| Option | Short | Default | Description |
| :--- | :--- | :--- | :--- |
| `path` / `--dir` | `-d` | Current Dir | Target directory containing images to scan recursively |
| `--mode` | | `dry-run` | Mode: `dry-run`, `rename` (in-place), `copy` (to `/renamed/`), or `catalog-only` |
| `--delay` | | `5.0` | Cooldown delay in seconds between API requests |
| `--workers` | | `1` | Number of concurrent worker threads |
| `--limit` | | `None` (All) | Maximum unanalyzed images to process in this run |
| `--max-words` | | `5` | Maximum number of descriptive words in generated filenames |
| `--max-retries` | | `5` | Maximum retries with exponential backoff on HTTP 429 |
| `--api-key` | | Env / `.env` | Sarvam AI API subscription key |
| `--output-dir` | | Target Dir | Destination folder for catalog files |
| `--cache-file` | | `.sarvam_cache.json` | Path to custom JSON cache file |
| `--history-file` | | `rename_history.json`| Path to custom history file for rollback |
| `--force` | | `False` | Force rename even if some images lack AI descriptions |
| `--undo` / `--rollback` | | `False` | Revert all renamed files back to their original names |

---

## 🗂️ Project Structure

```
sarvam-ai_api_tools/
├── .env.sample               # Environment variable configuration template
├── .gitignore                # Git ignore rules (protects credentials, caches, outputs)
├── requirements.txt          # Python dependencies
├── README.md                 # Documentation
├── sarvam_image_renamer.py   # Tool: Image Renamer & Metadata Cataloger
└── transcribe_sarvam.py      # Tool: Speech Transcription & Diarization
```

---

## 🛡️ Security & Best Practices

1. **Protect Credentials**: Never commit `.env` or API keys. The repository includes a preconfigured `.gitignore` to prevent secret leaks.
2. **Quota Management**: Sarvam AI enforces rate limits and quotas. Both tools are built with conservative defaults (sequential batches, backoff on 429).
3. **Safe Signal Handling**: Graceful termination via `Ctrl + C` ensures partial work is flushed to disk without file corruption.

---

## 🧩 Adding New Tools to the Suite

When contributing or adding new tools to this suite:
1. **Follow Unified API Key Resolution**: Use `get_api_key(cli_key, target_dir)` supporting CLI arguments, `SARVAM_API_KEY` environment variable, and `.env` fallback.
2. **Support `--api-key` in `argparse`**: Always provide a `--api-key` CLI parameter.
3. **Preserve Non-Destructive Defaults**: Implement preview/dry-run capabilities for file modification workflows.
4. **Isolate Outputs**: Direct output files to dedicated directories and register output extensions in `.gitignore`.
5. **Document Usage**: Add the new tool to the tools matrix and usage section in this `README.md`.

---

## 👤 Author & Attribution

* **Author**: [Shobhit Kumar Prabhakar](https://github.com/GeekLord) ([@GeekLord](https://github.com/GeekLord))
* **Repository**: [https://github.com/GeekLord/sarvam-ai_api_tools](https://github.com/GeekLord/sarvam-ai_api_tools)
* **Powered By**: [Sarvam AI APIs](https://www.sarvam.ai/)

---

## 📄 License

This project is licensed under the **MIT License** — see the [LICENSE](LICENSE) file for details.
