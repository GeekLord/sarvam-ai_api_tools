#!/usr/bin/env python3
"""
Sarvam AI Standalone Image Renamer & Captioner
==============================================
A robust, fail-safe CLI tool designed to run from ANY directory.
Recursively scans image files across directories and subdirectories,
analyzes visual content using Sarvam AI Document Intelligence API,
renames files with clean SEO-optimized slugs, and generates rich
captions and metadata catalogs.

Features:
- Completely portable: operates in current working directory or any specified path.
- Fully recursive: discovers images in nested subdirectories and preserves structure.
- Content-addressable deduplication: duplicate files share a single API call.
- Persistent resumable caching: `.sarvam_cache.json` protects API quota.
- Automatic rate-limit management: polite cooldowns, exponential backoff, and 429 handling.
- Fail-safe rollback: built-in `--undo` flag to revert renames using `rename_history.json`.
- Multi-format metadata export: generates Markdown catalog, JSON manifest, and CSV spreadsheet.
- Collision avoidance: automatic incremental slug deduplication per folder.
"""

import os
import sys
import re
import json
import csv
import time
import shutil
import hashlib
import zipfile
import argparse
import signal
import tempfile
import random
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# Optional PIL / Pillow
try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

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

# Supported image extensions
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tiff', '.tif', '.heic'}

# Directories to ignore during recursive search
IGNORED_DIRS = {
    '.git', '.svn', '.hg', '.vscode', '.idea', '__pycache__',
    'node_modules', '.sarvam_temp', '.system_generated', 'renamed'
}

# English stop words to filter out when generating SEO slugs
STOP_WORDS = {
    'the', 'image', 'shows', 'depicts', 'scene', 'picture', 'photo', 'with', 'and', 'a', 'an', 
    'of', 'in', 'on', 'at', 'to', 'for', 'from', 'by', 'is', 'are', 'there', 'that', 'this',
    'it', 'its', 'as', 'can', 'be', 'seen', 'appears', 'likely', 'some', 'several', 'large', 'small',
    'overall', 'background', 'foreground', 'middle', 'ground', 'right', 'left', 'side', 'area',
    'suggests', 'indicating', 'including', 'visible', 'various', 'presence', 'type', 'types',
    'view', 'taken', 'features', 'featured', 'displays', 'displaying', 'composed', 'located'
}

class PipelineState:
    """Manages global state and handles graceful exit on Ctrl+C."""
    def __init__(self):
        self.interrupted = False
        self.cache = {}
        self.cache_path = None

    def setup_signals(self):
        signal.signal(signal.SIGINT, self._handle_interrupt)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, self._handle_interrupt)

    def _handle_interrupt(self, sig, frame):
        print("\n\n[!] Interrupt received (Ctrl+C). Flushing cache and safely shutting down...")
        self.interrupted = True
        self.flush_cache()
        sys.exit(130)

    def flush_cache(self):
        if self.cache_path and self.cache:
            try:
                temp_file = f"{self.cache_path}.tmp"
                with open(temp_file, 'w', encoding='utf-8') as f:
                    json.dump(self.cache, f, indent=2, ensure_ascii=False)
                if os.path.exists(temp_file):
                    os.replace(temp_file, self.cache_path)
            except Exception as e:
                print(f"[!] Warning: Could not flush cache: {e}")

STATE = PipelineState()


def get_api_key(cli_key=None, target_dir=None):
    """
    Resolve Sarvam AI API subscription key with prioritized search:
    1. Explicit CLI argument --api-key
    2. Environment variable SARVAM_API_KEY
    3. .env file in target_dir, current working directory, or script directory
    """
    if cli_key and cli_key.strip():
        return cli_key.strip()
        
    env_key = os.environ.get("SARVAM_API_KEY", "").strip()
    if env_key:
        return env_key
        
    # Check target_dir, current dir, or script dir for .env
    candidates = []
    if target_dir:
        candidates.append(Path(target_dir))
    candidates.append(Path.cwd())
    candidates.append(Path(__file__).resolve().parent)

    for candidate in candidates:
        env_file = candidate / ".env"
        if env_file.is_file():
            try:
                with open(env_file, 'r', encoding='utf-8') as f:
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


def compute_file_hash(filepath, block_size=65536):
    """Computes MD5 hash in memory-efficient chunks."""
    hasher = hashlib.md5()
    with open(filepath, 'rb') as f:
        for block in iter(lambda: f.read(block_size), b''):
            hasher.update(block)
    return hasher.hexdigest()


def find_image_files(root_dir):
    """
    Recursively scans root_dir for image files, ignoring hidden/system directories.
    Returns list of Path objects relative to root_dir.
    """
    root_path = Path(root_dir).resolve()
    image_files = []
    
    for dirpath, dirnames, filenames in os.walk(root_path):
        # Filter out ignored directories in-place to avoid traversing them
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS and not d.startswith('.')]
        
        for fname in filenames:
            ext = os.path.splitext(fname)[1].lower()
            if ext in IMAGE_EXTENSIONS and not fname.startswith('.'):
                full_path = Path(dirpath) / fname
                rel_path = full_path.relative_to(root_path)
                image_files.append(rel_path)
                
    return sorted(image_files)


def clean_slug(text, max_words=5, custom_priority_keywords=None):
    """
    Converts AI descriptive text into a clean, SEO-friendly, filesystem-safe slug.
    """
    if not text or not text.strip():
        return "image-content"

    # Normalize and extract words
    text = text.lower()
    words = re.findall(r'[a-zA-Z0-9]+', text)
    meaningful = [w for w in words if w not in STOP_WORDS and len(w) > 2 and not w.isnumeric()]

    priority_keywords = custom_priority_keywords or [
        'mulching', 'mulcher', 'clearing', 'skid', 'steer', 'brush', 'trees',
        'stump', 'excavation', 'grading', 'debris', 'storm', 'concrete',
        'residential', 'landscape', 'outdoor', 'construction', 'equipment'
    ]

    selected = []
    seen = set()

    # 1. Pick priority keywords first
    for w in meaningful:
        if w in priority_keywords and w not in seen:
            seen.add(w)
            selected.append(w)
            if len(selected) >= max_words:
                break

    # 2. Fill remaining slots with meaningful descriptive words
    if len(selected) < max_words:
        for w in meaningful:
            if w not in seen:
                seen.add(w)
                selected.append(w)
                if len(selected) >= max_words:
                    break

    if not selected:
        return "project-photo"

    return "-".join(selected)


def categorize_description(desc):
    """Infers high-level category from visual description text."""
    d = desc.lower()
    if any(k in d for k in ['kubota', 'skid steer', 'tractor', 'excavator', 'loader', 'machinery', 'equipment in']):
        return "Equipment in Action"
    elif any(k in d for k in ['mulch', 'mulching', 'wood chip', 'shredded']):
        return "Forestry Mulching"
    elif any(k in d for k in ['storm', 'hurricane', 'downed', 'fallen tree', 'debris', 'pile']):
        return "Storm & Debris Cleanup"
    elif any(k in d for k in ['pond', 'lake', 'water', 'residential', 'house', 'driveway', 'home', 'lawn', 'yard']):
        return "Residential Property"
    elif any(k in d for k in ['concrete', 'block', 'dumpster', 'construction', 'rebar', 'foundation']):
        return "Site Prep & Concrete Work"
    elif any(k in d for k in ['fence', 'boundary', 'easement', 'trail', 'path', 'survey']):
        return "Fence Line & Access Clearing"
    else:
        return "Land & Lot Clearing"


def get_image_dimensions(filepath):
    """Extracts width and height of an image without loading full bitmap into memory."""
    if not HAS_PIL:
        return "Unknown"
    try:
        with Image.open(filepath) as im:
            return f"{im.width}x{im.height}"
    except Exception:
        return "Unknown"


def analyze_image_with_sarvam(client, filepath, max_retries=5, initial_backoff=25.0):
    """
    Submits an image to Sarvam AI Document Intelligence API with robust 429 backoff.
    Extracts visual scene description text from the output JSON.
    """
    filename = os.path.basename(filepath)
    temp_zip = None

    for attempt in range(max_retries):
        if STATE.interrupted:
            raise KeyboardInterrupt()

        try:
            job = client.document_intelligence.create_job(language="en-IN", output_format="html")
            job.upload_file(str(filepath))
            job.start()
            job.wait_until_complete()

            # Download output zip into system temporary directory
            fd, temp_zip = tempfile.mkstemp(prefix="sarvam_", suffix=".zip")
            os.close(fd)
            job.download_output(temp_zip)

            description = ""
            with zipfile.ZipFile(temp_zip, 'r') as z:
                for member_name in z.namelist():
                    if member_name.endswith('.json'):
                        try:
                            meta = json.loads(z.read(member_name).decode('utf-8', errors='ignore'))
                            for block in meta.get('blocks', []):
                                if block.get('layout_tag') == 'image':
                                    block_text = block.get('text', '').strip()
                                    if len(block_text) > len(description):
                                        description = block_text
                        except Exception:
                            continue

            if temp_zip and os.path.exists(temp_zip):
                os.remove(temp_zip)
                temp_zip = None

            return description.strip()

        except Exception as e:
            if temp_zip and os.path.exists(temp_zip):
                try:
                    os.remove(temp_zip)
                except Exception:
                    pass
                temp_zip = None

            err_str = str(e)
            is_rate_limit = "429" in err_str or "rate limit" in err_str.lower()

            if is_rate_limit:
                # Exponential backoff with random jitter
                backoff = (initial_backoff * (2 ** attempt)) + random.uniform(2.0, 6.0)
                print(f"\n[Rate Limit 429] on {filename}. Cooling down for {backoff:.1f}s (attempt {attempt+1}/{max_retries})...")
                time.sleep(backoff)
            else:
                if attempt == max_retries - 1:
                    print(f"\n[API Error] Failed to analyze {filename}: {e}")
                    raise
                time.sleep(5.0)

    raise RuntimeError(f"Exceeded max retries ({max_retries}) on {filename}.")


def generate_markdown_catalog(catalog_items, root_dir):
    """Builds a comprehensive, formatted Markdown catalog of all processed images."""
    categories = {}
    for item in catalog_items:
        categories.setdefault(item['category'], []).append(item)

    lines = [
        "# FixMyLand — Automated Image Catalog & SEO Metadata",
        "",
        "> Generated by **Sarvam AI Standalone Image Renamer & Captioner**.",
        "> Includes scene descriptions, suggested alt texts, marketing captions, and file specifications.",
        "",
        f"* **Total Files Indexed:** {len(catalog_items)}",
        f"* **Service Categories:** {len(categories)}",
        f"* **Generation Timestamp:** {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"* **Root Directory:** `{root_dir}`",
        "",
        "---",
        "",
        "## 📑 Category Index",
        ""
    ]

    for cat, items in categories.items():
        anchor = cat.lower().replace(' ', '-').replace('&', '').replace('--', '-')
        lines.append(f"* [{cat} ({len(items)} images)](#{anchor})")

    lines.append("")
    lines.append("---")
    lines.append("")

    for cat, items in categories.items():
        anchor = cat.lower().replace(' ', '-').replace('&', '').replace('--', '-')
        lines.append(f"## {cat}")
        lines.append("")
        lines.append("| Original Path | New SEO Filename | Dimensions | Suggested Alt Text |")
        lines.append("| :--- | :--- | :--- | :--- |")
        for it in items:
            orig = it['original_path']
            new_f = it['new_filename']
            dims = it['dimensions']
            alt = it['alt_text']
            lines.append(f"| `{orig}` | **`{new_f}`** | {dims} | {alt} |")
        lines.append("")
        
        lines.append(f"### Detailed Captions — {cat}")
        lines.append("")
        for it in items:
            lines.append(f"#### 📷 `{it['new_filename']}`")
            lines.append(f"* **Original Location:** `{it['original_path']}`")
            lines.append(f"* **Dimensions:** {it['dimensions']}")
            lines.append(f"* **Suggested Alt Text:** {it['alt_text']}")
            lines.append(f"* **Website Caption:** *\"{it['caption']}\"*")
            lines.append(f"* **Scene Breakdown:** {it['full_description']}")
            lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def perform_rollback(target_dir, history_file=None):
    """
    Reverts previously renamed files back to their original names
    using the recorded rename_history.json.
    """
    target_path = Path(target_dir).resolve()
    h_file = Path(history_file).resolve() if history_file else target_path / "rename_history.json"

    if not h_file.is_file():
        print(f"[!] Error: History file not found at: {h_file}")
        print("Cannot undo without a valid rename_history.json.")
        sys.exit(1)

    with open(h_file, 'r', encoding='utf-8') as f:
        history = json.load(f)

    print(f"\n=======================================================")
    print(f" Undoing / Rolling Back Image Renames")
    print(f" Target Directory: {target_path}")
    print(f" History File:     {h_file}")
    print(f" Total Entries:    {len(history)}")
    print(f"=======================================================\n")

    reverted_count = 0
    errors = []

    for entry in history:
        # Expected keys: 'original_path', 'new_path' (or legacy 'from', 'to')
        orig_rel = entry.get('original_path') or entry.get('from')
        new_rel = entry.get('new_path') or entry.get('to')

        if not orig_rel or not new_rel:
            continue

        orig_full = target_path / orig_rel
        new_full = target_path / new_rel

        if not new_full.exists():
            if orig_full.exists():
                # Already in original state
                continue
            errors.append(f"Missing current file: {new_rel}")
            continue

        if orig_full.exists() and orig_full != new_full:
            errors.append(f"Target file already exists: {orig_rel}")
            continue

        try:
            orig_full.parent.mkdir(parents=True, exist_ok=True)
            os.rename(new_full, orig_full)
            reverted_count += 1
            print(f"[REVERTED] {new_rel}  ===>  {orig_rel}")
        except Exception as e:
            errors.append(f"Failed to revert {new_rel} -> {orig_rel}: {e}")

    print(f"\nRollback Summary: Successfully reverted {reverted_count} files.")
    if errors:
        print(f"[!] Encountered {len(errors)} issues during rollback:")
        for err in errors[:10]:
            print(f"    - {err}")
        if len(errors) > 10:
            print(f"    ... and {len(errors) - 10} more.")


def run_renamer_pipeline(args):
    """Main execution flow for image discovery, AI analysis, renaming, and cataloging."""
    STATE.setup_signals()

    target_dir = Path(args.dir).resolve() if args.dir else Path.cwd().resolve()
    if not target_dir.is_dir():
        print(f"[!] Error: Target directory does not exist: {target_dir}")
        sys.exit(1)

    if args.undo:
        perform_rollback(target_dir, args.history_file)
        return

    if not HAS_SARVAM and args.mode != "catalog-only":
        print("[!] Error: 'sarvamai' package is required.")
        print("Please install requirements: pip install -r requirements.txt")
        sys.exit(1)

    api_key = get_api_key(args.api_key, target_dir)
    if not api_key and args.mode != "catalog-only":
        print("[!] Error: Sarvam AI API subscription key not found.")
        print("Provide via --api-key, SARVAM_API_KEY environment variable, or .env file.")
        sys.exit(1)

    client = SarvamAI(api_subscription_key=api_key) if HAS_SARVAM and api_key else None

    # Paths for output and cache
    output_dir = Path(args.output_dir).resolve() if args.output_dir else target_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    cache_file = Path(args.cache_file).resolve() if args.cache_file else output_dir / ".sarvam_cache.json"
    catalog_md = output_dir / "IMAGE_CATALOG.md"
    manifest_json = output_dir / "image_manifest.json"
    manifest_csv = output_dir / "image_manifest.csv"
    history_json = output_dir / "rename_history.json"

    STATE.cache_path = str(cache_file)

    # 1. Scan images recursively
    print(f"\n=======================================================")
    print(f" Sarvam AI Image Renamer & Captioner")
    print(f" Target Directory: {target_dir}")
    print(f" Output Directory: {output_dir}")
    print(f" Mode:             {args.mode.upper()}")
    print(f" Pacing Delay:     {args.delay}s | Workers: {args.workers}")
    print(f"=======================================================\n")

    print(f"Scanning for images in '{target_dir}' and subdirectories...")
    image_rel_paths = find_image_files(target_dir)
    if not image_rel_paths:
        print("[!] No supported images found in target directory.")
        return

    print(f"Found {len(image_rel_paths)} total image file(s).")

    # 2. Load cache
    cache = {}
    if cache_file.is_file():
        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                cache = json.load(f)
            print(f"Loaded {len(cache)} cached image analyses from '{cache_file.name}'.")
        except Exception as e:
            print(f"[!] Warning reading cache file: {e}")
            cache = {}
    STATE.cache = cache

    # 3. Hash files to deduplicate API calls
    print("Computing file hashes for deduplication...")
    file_hashes = {}
    hash_to_files = {}
    for rel_p in image_rel_paths:
        full_p = target_dir / rel_p
        try:
            h = compute_file_hash(full_p)
            file_hashes[rel_p] = h
            hash_to_files.setdefault(h, []).append(rel_p)
        except Exception as e:
            print(f"[!] Could not read {rel_p}: {e}")

    unique_hashes = len(hash_to_files)
    needed_hashes = {h: flist[0] for h, flist in hash_to_files.items() if h not in cache}
    print(f"Unique Image Content Hashes: {unique_hashes} | Already Cached: {unique_hashes - len(needed_hashes)} | Needed: {len(needed_hashes)}")

    # Apply batch limit if requested
    if args.limit and needed_hashes:
        limited_keys = list(needed_hashes.keys())[:args.limit]
        needed_hashes = {k: needed_hashes[k] for k in limited_keys}
        print(f"Batch Limit Applied: Processing next {len(needed_hashes)} unanalyzed images.")

    # 4. Throttled AI Analysis
    if needed_hashes and args.mode != "catalog-only":
        print(f"\nStarting throttled Sarvam AI analysis for {len(needed_hashes)} image(s)...")
        completed = 0

        if args.workers == 1:
            for h, sample_rel in needed_hashes.items():
                if STATE.interrupted:
                    break
                sample_full = target_dir / sample_rel
                completed += 1
                try:
                    desc = analyze_image_with_sarvam(client, sample_full, max_retries=args.max_retries)
                    if not desc:
                        # Fallback contextual description if no blocks found
                        stem_clean = sample_rel.stem.replace('_', ' ').replace('-', ' ')
                        desc = f"Visual scene showing {stem_clean} in natural outdoor setting."
                        print(f"[{completed}/{len(needed_hashes)}] Fallback applied: {sample_rel}")
                    else:
                        print(f"[{completed}/{len(needed_hashes)}] Analyzed: {sample_rel} ({len(desc)} chars)")

                    cache[h] = desc
                    STATE.flush_cache()
                except Exception as e:
                    print(f"[{completed}/{len(needed_hashes)}] Paused on {sample_rel}: {e}")
                    break

                time.sleep(args.delay)
        else:
            with ThreadPoolExecutor(max_workers=args.workers) as executor:
                future_to_hash = {
                    executor.submit(analyze_image_with_sarvam, client, target_dir / sf, args.max_retries): (h, sf)
                    for h, sf in needed_hashes.items()
                }
                for fut in as_completed(future_to_hash):
                    if STATE.interrupted:
                        break
                    h, sample_rel = future_to_hash[fut]
                    completed += 1
                    try:
                        desc = fut.result()
                        if not desc:
                            stem_clean = sample_rel.stem.replace('_', ' ').replace('-', ' ')
                            desc = f"Visual scene showing {stem_clean} in natural outdoor setting."
                        print(f"[{completed}/{len(needed_hashes)}] Analyzed: {sample_rel} ({len(desc)} chars)")
                        cache[h] = desc
                        STATE.flush_cache()
                    except Exception as e:
                        print(f"[{completed}/{len(needed_hashes)}] Error on {sample_rel}: {e}")
                    time.sleep(args.delay)

    # 5. Generate SEO filenames, captions, and catalogs
    print("\nCompiling metadata and resolving unique filenames per directory...")
    catalog_items = []
    # Track used filenames per subdirectory to prevent collisions
    dir_taken_names = {}

    for rel_p in image_rel_paths:
        full_p = target_dir / rel_p
        sub_dir = rel_p.parent
        ext = rel_p.suffix.lower()
        h = file_hashes.get(rel_p)
        desc = cache.get(h, f"Outdoor photo showing {rel_p.stem.replace('_', ' ')}.")

        base_slug = clean_slug(desc, max_words=args.max_words)
        
        dir_taken = dir_taken_names.setdefault(sub_dir, set())
        
        # Determine unique filename in this subfolder
        counter = 1
        candidate_name = f"{base_slug}{ext}"
        while candidate_name in dir_taken or ((target_dir / sub_dir / candidate_name).exists() and candidate_name != rel_p.name):
            counter += 1
            candidate_name = f"{base_slug}-{counter:02d}{ext}"
            
        dir_taken.add(candidate_name)
        new_rel_path = sub_dir / candidate_name if str(sub_dir) != '.' else Path(candidate_name)

        dims = get_image_dimensions(full_p)
        cat = categorize_description(desc)
        alt_text = f"{cat} - {desc[:90].strip('.')} project site"
        caption = f"{desc[:180].strip('.')}."

        catalog_items.append({
            "original_path": str(rel_p).replace('\\', '/'),
            "new_path": str(new_rel_path).replace('\\', '/'),
            "original_filename": rel_p.name,
            "new_filename": candidate_name,
            "subdirectory": str(sub_dir).replace('\\', '/'),
            "category": cat,
            "dimensions": dims,
            "md5": h,
            "alt_text": alt_text,
            "caption": caption,
            "full_description": desc
        })

    # 6. Save Deliverables (Markdown, JSON, CSV)
    print("Writing metadata catalog deliverables...")
    md_content = generate_markdown_catalog(catalog_items, str(target_dir))
    with open(catalog_md, 'w', encoding='utf-8') as f:
        f.write(md_content)
    print(f" -> Markdown Catalog: {catalog_md.name}")

    with open(manifest_json, 'w', encoding='utf-8') as f:
        json.dump(catalog_items, f, indent=2, ensure_ascii=False)
    print(f" -> JSON Manifest:     {manifest_json.name}")

    with open(manifest_csv, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=[
            "original_path", "new_path", "original_filename", "new_filename",
            "subdirectory", "category", "dimensions", "alt_text", "caption",
            "full_description", "md5"
        ])
        writer.writeheader()
        writer.writerows(catalog_items)
    print(f" -> CSV Spreadsheet:   {manifest_csv.name}")

    # 7. Apply File Operations
    if args.mode == "copy":
        dest_root = output_dir / "renamed"
        dest_root.mkdir(parents=True, exist_ok=True)
        print(f"\nCopying renamed files to: {dest_root}")
        for item in catalog_items:
            src = target_dir / Path(item['original_path'])
            dst = dest_root / Path(item['new_path'])
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        print(f"Successfully copied {len(catalog_items)} files to '{dest_root}'.")

    elif args.mode == "rename":
        # Safety check: ensure all images have been analyzed
        unanalyzed = sum(1 for it in catalog_items if it['md5'] not in cache)
        if unanalyzed > 0 and not args.force:
            print(f"\n[SAFETY HALT] {unanalyzed} image(s) still need AI analysis.")
            print("Run another batch or add '--force' to proceed with fallback names.")
            print("No files were renamed.")
            return

        print("\nExecuting in-place file rename...")
        rename_log = []
        renamed_count = 0

        for item in catalog_items:
            orig_p = target_dir / Path(item['original_path'])
            new_p = target_dir / Path(item['new_path'])

            if orig_p != new_p and orig_p.exists():
                try:
                    new_p.parent.mkdir(parents=True, exist_ok=True)
                    os.rename(orig_p, new_p)
                    renamed_count += 1
                    rename_log.append({
                        "original_path": item['original_path'],
                        "new_path": item['new_path'],
                        "timestamp": time.time()
                    })
                except Exception as e:
                    print(f"[!] Error renaming {item['original_path']} -> {item['new_path']}: {e}")

        with open(history_json, 'w', encoding='utf-8') as f:
            json.dump(rename_log, f, indent=2)
        print(f"Renamed {renamed_count} files in-place! Audit history saved to '{history_json.name}'.")
        print("To revert at any time, run: python sarvam_image_renamer.py --undo")

    else:
        print("\n[DRY RUN] No files were moved or renamed on disk.")
        print(f"Review the generated '{catalog_md.name}' and '{manifest_json.name}'.")
        print("To execute in-place renaming, run with: --mode rename")


def main():
    parser = argparse.ArgumentParser(
        description="Sarvam AI Standalone Image Renamer & Captioner — Portable, recursive, and fail-safe.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dry-run on current directory (generates catalogs and previews):
  python sarvam_image_renamer.py

  # Process a specific folder with custom 5-second delay:
  python sarvam_image_renamer.py --dir /path/to/photos --delay 5.0

  # Run small batch of 15 images to conserve quota:
  python sarvam_image_renamer.py --dir /path/to/photos --limit 15

  # Perform in-place renaming once all images are analyzed:
  python sarvam_image_renamer.py --dir /path/to/photos --mode rename

  # Revert all renames back to original filenames:
  python sarvam_image_renamer.py --dir /path/to/photos --undo
        """
    )
    parser.add_argument("path", nargs="?", default=None,
                        help="Target directory containing images (defaults to current directory)")
    parser.add_argument("-d", "--dir", dest="dir", default=None,
                        help="Target directory (alternative to positional argument)")
    parser.add_argument("--mode", choices=["dry-run", "rename", "copy", "catalog-only"], default="dry-run",
                        help="Operation mode: 'dry-run' (default), 'rename' (in-place), 'copy' (to /renamed/), 'catalog-only' (skip API)")
    parser.add_argument("--delay", type=float, default=5.0,
                        help="Cooldown delay in seconds between API calls to prevent 429 rate limits (default: 5.0)")
    parser.add_argument("--workers", type=int, default=1,
                        help="Concurrent API workers (default: 1 for burst rate limit safety)")
    parser.add_argument("--limit", type=int, default=None,
                        help="Limit number of unanalyzed images to process in this run")
    parser.add_argument("--max-words", type=int, default=5,
                        help="Maximum number of descriptive words in generated filenames (default: 5)")
    parser.add_argument("--max-retries", type=int, default=5,
                        help="Max retries with exponential backoff on 429 rate limits (default: 5)")
    parser.add_argument("--api-key", default=None,
                        help="Sarvam AI API subscription key (or set SARVAM_API_KEY environment variable)")
    parser.add_argument("--output-dir", default=None,
                        help="Directory to save catalog deliverables (defaults to target directory)")
    parser.add_argument("--cache-file", default=None,
                        help="Path to custom JSON cache file (defaults to .sarvam_cache.json in target directory)")
    parser.add_argument("--history-file", default=None,
                        help="Path to custom rename_history.json file for undo")
    parser.add_argument("--force", action="store_true",
                        help="Force in-place rename even if some images lack AI descriptions")
    parser.add_argument("--undo", "--rollback", dest="undo", action="store_true",
                        help="Revert previously renamed files using rename_history.json")

    args = parser.parse_args()

    # Handle positional path vs --dir
    if args.path and not args.dir:
        args.dir = args.path

    run_renamer_pipeline(args)


if __name__ == "__main__":
    main()
