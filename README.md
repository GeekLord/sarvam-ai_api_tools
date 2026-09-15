# Sarvam AI API Tools Suite

A modular, production-ready suite of Python CLI tools leveraging [Sarvam AI](https://www.sarvam.ai/) APIs for multimodal AI tasks—including Document Intelligence, computer vision image renaming, SEO catalog generation, speech-to-text audio transcription, and speaker diarization.

Designed for standalone portability, robust error handling, rate-limit resilience, and unified configuration.

---

## 📦 Available Tools

| Tool | Script | Primary API / Model | Capabilities |
| :--- | :--- | :--- | :--- |
| **Image Renamer & Metadata Cataloger** | [`sarvam_image_renamer.py`](file:///h:/Desktop/sarvam-ai_api_tools/sarvam_image_renamer.py) | Sarvam Document Intelligence / Vision | Recursive image analysis, content-addressable deduplication, SEO slug generation, non-destructive renaming, fail-safe rollback, Markdown/CSV/JSON catalogs. |
| **Speech Transcription & Diarization** | [`transcribe_sarvam.py`](file:///h:/Desktop/sarvam-ai_api_tools/transcribe_sarvam.py) | Sarvam Speech-to-Text (`saaras:v4`) | Multilingual audio transcription, speaker diarization (auto or 1–20 speakers), timestamped human-readable dialogue (`.txt`), spreadsheet timeline (`.csv`), and raw payload (`.json`). |
| *Upcoming Tools* | *In Development* | Translation, TTS (`bulbul`), LLMs | Text translation, voice synthesis, and conversational pipeline utilities. |

---

## 📋 System Requirements

* Python **3.8+**
* An active [Sarvam AI API Subscription Key](https://dashboard.sarvam.ai/)

---

## 🚀 Installation & Unified Setup

### 1. Clone or Download Repository
```bash
git clone https://github.com/your-org/sarvam-ai_api_tools.git
cd sarvam-ai_api_tools
```

### 2. Install Dependencies
Install dependencies for all tools using `requirements.txt`:
```bash
pip install -r requirements.txt
```

### 3. Configure Your API Key
Both scripts share a standardized 3-tier API key resolution hierarchy:
1. **Explicit CLI Flag**: `--api-key <KEY>`
2. **Environment Variable**: `SARVAM_API_KEY`
3. **`.env` File Fallback**: Checked in the target directory, the current working directory, and the script's root directory.

#### Option A: `.env` File (Recommended)
Copy the provided sample file to `.env`:
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
Pass `--api-key` directly to any script invocation:
```bash
python transcribe_sarvam.py --api-key "your_api_key_here"
python sarvam_image_renamer.py --api-key "your_api_key_here"
```

---

## 🛠️ Tool 1: Image Renamer & Metadata Cataloger

`sarvam_image_renamer.py` scans local directories recursively, analyzes visual scenes using Sarvam AI's Document Intelligence API, renames images with descriptive SEO slugs, and compiles structured catalog deliverables.

### Key Highlights
* **Deduplication via MD5**: Identical images across nested folders are analyzed once and cached.
* **Persistent Cache**: Saved to `.sarvam_cache.json`. Runs can be paused and resumed without re-calling the API.
* **Rate-Limit Throttling**: Polite request cooldowns (default: 5.0s), single-worker execution by default, and exponential backoff with jitter on HTTP 429.
* **Collision-Proof Renaming**: Resolves name collisions per subfolder (`-02.jpg`, `-03.jpg`) without overwriting files.
* **100% Reversible**: Every rename is logged to `rename_history.json`. Roll back changes anytime using `--undo`.
* **Multi-Format Deliverables**: Outputs `IMAGE_CATALOG.md`, `image_manifest.json`, and `image_manifest.csv`.

### Common Commands

```bash
# 1. Preview / Dry Run (Default: analyzes, caches, and builds catalog without touching files)
python sarvam_image_renamer.py "E:\Photos\JobSites"

# 2. Throttled processing (process 15 images with 5s delay)
python sarvam_image_renamer.py "E:\Photos\JobSites" --limit 15 --delay 5.0

# 3. In-place renaming (executes after dry-run review)
python sarvam_image_renamer.py "E:\Photos\JobSites" --mode rename

# 4. Non-destructive copy mode (saves renamed copies into 'renamed/' subfolder)
python sarvam_image_renamer.py "E:\Photos\JobSites" --mode copy

# 5. Offline catalog regeneration (rebuilds docs from cache without API calls)
python sarvam_image_renamer.py "E:\Photos\JobSites" --mode catalog-only

# 6. Complete rollback / Undo (restores original filenames from history)
python sarvam_image_renamer.py "E:\Photos\JobSites" --undo
```

### CLI Options Reference

| Option | Short | Default | Description |
| :--- | :--- | :--- | :--- |
| `path` / `--dir` | `-d` | Current Dir | Target directory containing images to scan recursively |
| `--mode` | | `dry-run` | Execution mode: `dry-run`, `rename`, `copy`, or `catalog-only` |
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

## 🎙️ Tool 2: Speech Transcription & Diarization

`transcribe_sarvam.py` transcribes audio recordings using the **Sarvam Saaras v4** speech model (`saaras:v4`), detects distinct speakers via diarization, computes millisecond timestamps, and generates human-readable transcripts and spreadsheets.

### Supported Audio Formats
`.wav`, `.mp3`, `.aac`, `.m4a`, `.mp4`, `.flac`, `.ogg`, `.opus`, `.aiff`, `.aif`, `.amr`, `.wma`, `.webm`

### Key Highlights
* **Diarization Engine**: Segregates dialogue per speaker with automatic speaker count detection or an explicit speaker constraint (`--speakers 1-20`).
* **Multi-Format Exports**:
  1. `[filename].txt`: Human-readable dialogue formatted as `[HH:MM:SS.mmm - HH:MM:SS.mmm] Speaker N: text`.
  2. `[filename].csv`: Spreadsheet-ready chronological breakdown (`speaker`, `start_time_seconds`, `end_time_seconds`, `start_time`, `end_time`, `transcript`).
  3. `[filename].json`: Raw API response payload including confidence and diarization metadata.
* **Batch Processing**: Configurable batch size (`--batch-size 20`), isolated temp staging directories, and error handling that processes remaining files even if one fails.

### Common Commands

```bash
# 1. Transcribe audio files in the current folder (automatic speaker detection)
python transcribe_sarvam.py

# 2. Transcribe a specific folder
python transcribe_sarvam.py "C:\Recordings\Interviews"

# 3. Specify expected speaker count (e.g., 2-person interview)
python transcribe_sarvam.py "C:\Recordings\Interviews" --speakers 2

# 4. Custom output folder and batch size
python transcribe_sarvam.py "C:\Recordings" -o "C:\Recordings\Transcripts" --batch-size 10

# 5. Pass API key explicitly via CLI
python transcribe_sarvam.py "C:\Recordings" --api-key "your_api_key_here"
```

### CLI Options Reference

| Argument / Flag | Short | Default | Description |
| :--- | :--- | :--- | :--- |
| `folder` | | `.` (Current Dir) | Target directory containing audio files |
| `--speakers` | | `None` (Auto) | Expected number of speakers (1–20) for diarization |
| `--output-dir` | `-o` | `<folder>/sarvam_transcripts` | Output folder for generated transcripts |
| `--batch-size` | | `20` | Number of audio files processed per API batch job |
| `--api-key` | | Env / `.env` | Sarvam AI API subscription key |

### Output Deliverables

Transcriptions are stored in the output directory (default: `sarvam_transcripts/`):
```
sarvam_transcripts/
├── meeting_01.txt      # Formatted transcript with speaker timestamps
├── meeting_01.csv      # CSV with timeline columns (ready for Excel/Sheets)
└── meeting_01.json     # Complete raw Sarvam AI response
```

---

## 🗂️ Project Structure

```
sarvam-ai_api_tools/
├── .env.sample               # Environment variable template
├── .gitignore                # Git exclusions (protects secrets, outputs, caches)
├── requirements.txt          # Python dependencies
├── README.md                 # Project documentation
├── sarvam_image_renamer.py   # Tool 1: Image Renamer & Metadata Cataloger
└── transcribe_sarvam.py      # Tool 2: Speech Transcription & Diarization
```

---

## 🛡️ Security & Best Practices

1. **Never Commit Secrets**: The `.gitignore` file excludes `.env`, `*.key`, and temporary configuration files. Always use `.env.sample` as a template.
2. **Rate Limit Courtesy**: Sarvam AI imposes per-minute and burst quotas. Both scripts respect rate limits, utilize throttled batches, and employ backoff routines.
3. **Safe Interruption**: Pressing `Ctrl + C` gracefully flushes progress to disk without corrupting caches or files.

---

## 🧩 Adding New Tools

When contributing or adding new tools to this suite:
* Adopt the standard 3-tier API key resolution: `get_api_key(cli_key, target_dir)`.
* Include `--api-key` in `argparse` options alongside `os.environ` and `.env` fallback.
* Use non-destructive operations by default or provide preview modes where applicable.
* Add tool-specific output paths to `.gitignore` and document usage in this README.

---

## 📄 License & Attribution

Licensed under the **MIT License**.
Powered by [Sarvam AI APIs](https://www.sarvam.ai/).
