from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    from sarvamai import SarvamAI
    HAS_SARVAM = True
except ImportError:
    HAS_SARVAM = False



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

MODEL = "saaras:v4"
MODE = "transcribe"
BATCH_SIZE = 20
OUTPUT_DIR = Path("sarvam_transcripts")


def find_audio_files(folder: Path) -> list[Path]:
    return sorted(
        [
            p for p in folder.iterdir()
            if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS
        ],
        key=lambda p: p.name.lower(),
    )


def chunks(items: list[Path], size: int) -> Iterable[list[Path]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def format_time(seconds: Any) -> str:
    """Convert seconds to HH:MM:SS.mmm."""
    try:
        total_ms = max(0, round(float(seconds) * 1000))
    except (TypeError, ValueError):
        return "00:00:00.000"

    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1_000)

    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def normalize_speaker_id(value: Any) -> str:
    """Make speaker labels friendly and consistent."""
    if value is None:
        return "Unknown Speaker"

    value = str(value)

    # Sarvam examples use numeric IDs such as "0", "1".
    if value.isdigit():
        return f"Speaker {int(value) + 1}"

    # Also handle SPEAKER_00 / SPEAKER_01 style labels.
    upper = value.upper()
    if upper.startswith("SPEAKER_"):
        suffix = upper.split("_", 1)[1]
        if suffix.isdigit():
            return f"Speaker {int(suffix) + 1}"

    return value


def extract_segments(data: dict) -> list[dict[str, Any]]:
    """
    Extract diarized segments from Sarvam's documented response.

    Expected shape:
      diarized_transcript:
        entries:
          - transcript
          - start_time_seconds
          - end_time_seconds
          - speaker_id
    """
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

    # Defensive fallback for an alternate "segments" representation.
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

    return segments


def write_outputs(
    audio_path: Path,
    result_json: dict,
    output_dir: Path,
) -> None:
    """
    Write:
      1. human-readable TXT
      2. raw JSON
      3. CSV containing speaker/timestamps/text
    """
    base = output_dir / audio_path.stem
    txt_path = base.with_suffix(".txt")
    json_path = base.with_suffix(".json")
    csv_path = base.with_suffix(".csv")

    segments = extract_segments(result_json)

    # ---- Human-readable transcript ----
    lines: list[str] = [
        f"File: {audio_path.name}",
        f"Model: {MODEL}",
        f"Language: {result_json.get('language_code') or 'auto-detected'}",
        "",
        "TRANSCRIPT",
        "=" * 80,
        "",
    ]

    if segments:
        for segment in segments:
            start = format_time(segment["start"])
            end = format_time(segment["end"])
            lines.append(
                f"[{start} - {end}] {segment['speaker']}: "
                f"{segment['text']}"
            )
    else:
        # If diarization unexpectedly returns no entries, preserve the
        # full transcript rather than losing the result.
        transcript = str(result_json.get("transcript", "")).strip()
        lines.append(transcript or "[No transcript returned]")

    txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ---- Raw JSON ----
    json_path.write_text(
        json.dumps(result_json, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # ---- CSV ----
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

    print(f"  ✓ {audio_path.name}")
    print(f"    TXT : {txt_path}")
    print(f"    CSV : {csv_path}")
    print(f"    JSON: {json_path}")


def locate_downloaded_json(
    temp_dir: Path,
    entry: dict,
    entry_index: int,
    successful_count: int,
) -> Path | None:
    """
    Locate the JSON downloaded by the SDK. The SDK/API may use the
    original output filename or generated names, so use a few fallbacks.
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

    # Last-resort: if there is exactly one JSON file.
    if len(json_files) == 1:
        return json_files[0]

    return None


def match_result_to_input(
    entry: dict,
    batch: list[Path],
    fallback_index: int,
) -> Path | None:
    names = [
        entry.get("input_file_name"),
        entry.get("file_name"),
        entry.get("filename"),
    ]

    for name in names:
        if not name:
            continue

        name = Path(str(name)).name

        for audio_path in batch:
            if audio_path.name == name:
                return audio_path

    if 0 <= fallback_index < len(batch):
        return batch[fallback_index]

    return None


def process_batch(
    client: SarvamAI,
    batch: list[Path],
    batch_number: int,
    output_dir: Path,
    num_speakers: int | None,
) -> tuple[int, int]:
    print(f"=== Batch {batch_number}: {len(batch)} file(s) ===")

    successful = 0
    failed = 0

    with tempfile.TemporaryDirectory(prefix="sarvam_stt_") as tmp:
        tmp_dir = Path(tmp)

        try:
            job_kwargs = {
                "model": MODEL,
                "mode": MODE,
                "with_diarization": True,
            }

            # If supplied, tell Sarvam how many speakers to expect.
            # Otherwise Sarvam automatically detects them.
            if num_speakers is not None:
                job_kwargs["num_speakers"] = num_speakers

            job = client.speech_to_text_job.create_job(**job_kwargs)

            job.upload_files(
                file_paths=[str(p) for p in batch]
            )

            print(f"  Job created: {job.job_id}")
            print("  Starting job...")
            job.start()

            print("  Waiting for Sarvam to finish...")
            job.wait_until_complete()

            file_results = job.get_file_results()

            successful_entries = file_results.get("successful") or []
            failed_entries = file_results.get("failed") or []

            if successful_entries:
                job.download_outputs(output_dir=str(tmp_dir))

            # Map each successful result back to its original audio file.
            for index, entry in enumerate(successful_entries):
                audio_path = match_result_to_input(
                    entry,
                    batch,
                    index,
                )

                if audio_path is None:
                    print(
                        f"  ✗ Could not map successful result "
                        f"{entry.get('file_name', index)} to an input file."
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
                    print(
                        f"  ✗ Could not locate downloaded JSON for "
                        f"{audio_path.name}"
                    )
                    failed += 1
                    continue

                try:
                    result_json = json.loads(
                        json_path.read_text(encoding="utf-8")
                    )
                except Exception as exc:
                    print(
                        f"  ✗ Could not parse result for "
                        f"{audio_path.name}: {exc}"
                    )
                    failed += 1
                    continue

                write_outputs(
                    audio_path,
                    result_json,
                    output_dir,
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


def get_api_key(cli_key: str | None = None, target_dir: Path | None = None) -> str | None:
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Transcribe audio files with Sarvam Saaras v4, "
            "speaker diarization, and timestamps."
        )
    )

    parser.add_argument(
        "folder",
        nargs="?",
        default=".",
        help="Path to folder containing audio recordings to transcribe (default: current directory).",
    )

    parser.add_argument(
        "--api-key",
        type=str,
        default=None,
        help="Sarvam AI API subscription key (or set SARVAM_API_KEY in environment or .env file).",
    )

    parser.add_argument(
        "--output-dir",
        "-o",
        type=str,
        default=None,
        help="Output directory for generated transcripts (default: <folder>/sarvam_transcripts).",
    )

    parser.add_argument(
        "--speakers",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Expected number of speakers (1-20). "
            "If omitted, Sarvam automatically detects speakers."
        ),
    )

    parser.add_argument(
        "--batch-size",
        type=int,
        default=BATCH_SIZE,
        metavar="N",
        help=f"Number of audio files per processing batch (default: {BATCH_SIZE}).",
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

    current_folder = Path(args.folder).resolve()
    if not current_folder.is_dir():
        print(f"ERROR: Target folder '{args.folder}' does not exist or is not a directory.")
        return 1

    api_key = get_api_key(cli_key=args.api_key, target_dir=current_folder)
    if not api_key:
        print("ERROR: Sarvam AI API subscription key was not found.")
        print()
        print("Please provide your key via one of the following methods:")
        print("  1. CLI argument:        --api-key 'your-api-key'")
        print("  2. Environment variable: export SARVAM_API_KEY='your-api-key' (or $env:SARVAM_API_KEY='your-api-key')")
        print("  3. .env file:            Copy .env.sample to .env and set SARVAM_API_KEY='your-api-key'")
        return 1

    output_dir = Path(args.output_dir).resolve() if args.output_dir else current_folder / OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    audio_files = find_audio_files(current_folder)

    if not audio_files:
        print(f"No supported audio recordings found in: {current_folder}")
        print("Supported extensions:")
        print(", ".join(sorted(AUDIO_EXTENSIONS)))
        return 0

    print(f"Found {len(audio_files)} audio file(s) in: {current_folder}")
    print(f"Model       : {MODEL}")
    print(f"Mode        : {MODE}")
    print("Diarization : enabled")
    print(
        "Speakers    : "
        + (
            str(args.speakers)
            if args.speakers is not None
            else "automatic detection"
        )
    )
    print(f"Batch Size  : {args.batch_size}")
    print(f"Output      : {output_dir.resolve()}")
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
            num_speakers=args.speakers,
        )

        total_successful += successful
        total_failed += failed

    print("=== Finished ===")
    print(f"Successful : {total_successful}")
    print(f"Failed     : {total_failed}")
    print(f"Output     : {output_dir.resolve()}")

    return 0 if total_failed == 0 else 2



if __name__ == "__main__":
    sys.exit(main())