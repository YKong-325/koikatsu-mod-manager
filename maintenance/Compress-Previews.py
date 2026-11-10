"""Re-encode already-extracted previews to WebP and rewrite the indexes.

Package previews used to be 512 px RGBA PNGs: 47k files, 1.1 GB, because
full-colour art is the worst case for PNG. WebP keeps the alpha channel and the
same edge cap at roughly a fifth of the bytes, so the grid also loads faster.

The rewrite is staged so an interrupted run never leaves the catalog broken:

  1. encode every image from `thumbs/` into `thumbs_new/` (old files untouched);
  2. swap directories: `thumbs` -> `_thumbs_png`, `thumbs_new` -> `thumbs`;
  3. rewrite `index.json` + `data.js` (and `library.json`).

Step 3 resolves each preview by what is on disk, so it is safe to run twice and
safe after a partial run: a preview that was never converted keeps its old name.

Usage:
    python maintenance/Compress-Previews.py --catalog release/KoikatsuModManager/catalog
    python maintenance/Compress-Previews.py --catalog ... --limit 200 --dry-run
    python maintenance/Compress-Previews.py --catalog ... --purge
"""

import argparse
import importlib.util
import json
import os
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

APP = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("preview_builder", APP / "Build-ModCatalog.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)

SOURCE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".gif"}


def human(count):
    return f"{count / 1048576:.1f} MiB"


def write_text_atomic(path, text):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    # Replacing a freshly written 40 MB file occasionally trips over a scanner
    # holding the target open for a moment; a short retry settles it.
    for attempt in range(20):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.25)


def folder_bytes(folder):
    total = 0
    for path in folder.iterdir():
        try:
            total += path.stat().st_size
        except OSError:
            pass
    return total


def convert(source, target_folder):
    """Encode one file; return (bytes before, bytes after, kept as-is)."""
    key = source.stem
    before = source.stat().st_size
    try:
        with Image.open(source) as image:
            suffix, payload = builder.encode_preview(image)
    except Exception:
        # Unreadable: copy through unchanged so the card keeps its picture.
        target = target_folder / (key + ".png")
        if not target.is_file():
            shutil.copyfile(source, target)
        return before, target.stat().st_size, True
    target = target_folder / (key + suffix)
    if not target.is_file():
        temporary = target.with_name(target.name + ".tmp")
        temporary.write_bytes(payload)
        os.replace(temporary, target)
    return before, target.stat().st_size, suffix != ".webp"


def prepare(catalog, name, staging, limit, workers, dry_run):
    """Encode `catalog/name` into `catalog/staging`, then swap on a full run."""
    source_folder, stage_folder = catalog / name, catalog / staging
    files = sorted(p for p in source_folder.iterdir()
                   if p.is_file() and p.suffix.lower() in SOURCE_SUFFIXES)
    all_files = len(files)
    if limit:
        files = files[:limit]
    stage_folder.mkdir(parents=True, exist_ok=True)
    before = after = kept = 0
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="preview") as pool:
        for number, (size_before, size_after, stayed) in enumerate(
                pool.map(lambda p: convert(p, stage_folder), files), 1):
            before += size_before
            after += size_after
            kept += stayed
            if number % 2000 == 0 or number == len(files):
                rate = number / max(time.perf_counter() - started, 1e-6)
                print(f"  {number:,} / {len(files):,}  {rate:,.0f} files/s  "
                      f"{human(before)} -> {human(after)}", flush=True)
    report = {"files": len(files), "all": all_files, "before": before, "after": after,
              "kept": kept, "seconds": time.perf_counter() - started, "swapped": False}
    if not dry_run and len(files) == all_files:
        retired = catalog / ("_" + name + "_old")
        if retired.exists():
            shutil.rmtree(retired, ignore_errors=True)
        os.replace(source_folder, retired)
        os.replace(stage_folder, source_folder)
        report["swapped"] = True
    return report


def resolve(catalog, relative):
    """Point a preview path at whichever file actually exists for its key."""
    path = catalog / relative
    if path.is_file():
        return relative
    for suffix in (".webp", ".png", ".jpg", ".jpeg"):
        candidate = path.parent / (path.stem + suffix)
        if candidate.is_file():
            return candidate.relative_to(catalog).as_posix()
    return relative


def rewrite_mods(catalog):
    index = catalog / "index.json"
    records = json.loads(index.read_text(encoding="utf-8"))
    changed = missing = 0
    for record in records:
        for image in record.get("images", []):
            source = image.get("src", "")
            target = resolve(catalog, source)
            if target != source:
                image["src"] = target
                changed += 1
            if not (catalog / target).is_file():
                missing += 1
    payload = json.dumps(records, ensure_ascii=False, separators=(",", ":"))
    write_text_atomic(index, payload)
    write_text_atomic(catalog / "data.js", "window.MOD_CATALOG=" + payload + ";")
    return len(records), changed, missing


def rewrite_library(catalog):
    path = catalog / "library.json"
    if not path.exists():
        return 0, 0
    records = json.loads(path.read_text(encoding="utf-8"))
    changed = 0
    for record in records:
        thumb = record.get("thumb")
        if not thumb:
            continue
        target = resolve(catalog, thumb)
        if target != thumb:
            record["thumb"] = target
            changed += 1
    payload = json.dumps(records, ensure_ascii=False, separators=(",", ":"))
    write_text_atomic(path, payload)
    return len(records), changed


def purge(catalog, names):
    """Delete retired folders; a blocked delete is reported, never fatal."""
    freed = 0
    for name in names:
        folder = catalog / name
        if not folder.is_dir():
            continue
        size = folder_bytes(folder)
        shutil.rmtree(folder, ignore_errors=True)
        left = len(list(folder.iterdir())) if folder.is_dir() else 0
        if left:
            print(f"  kept {folder.name} ({left:,} files, {human(size)}) — "
                  f"delete blocked, remove it manually", flush=True)
        else:
            freed += size
    return freed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=APP / "catalog")
    parser.add_argument("--limit", type=int, help="only convert the first N files (trial run)")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--dry-run", action="store_true",
                        help="encode into the staging folder but keep the catalog untouched")
    parser.add_argument("--purge", action="store_true",
                        help="delete the retired PNG/JPEG folders after a successful run")
    parser.add_argument("--skip-library", action="store_true",
                        help="leave card cover thumbnails alone")
    parser.add_argument("--rewrite-only", action="store_true",
                        help="only update the indexes (after an interrupted swap)")
    args = parser.parse_args()
    catalog = args.catalog
    if not (catalog / "index.json").is_file():
        sys.exit(f"not a catalog folder: {catalog}")

    jobs = ["thumbs"] + ([] if args.skip_library else ["library_thumbs"])
    retired = []
    for name in jobs:
        source_folder = catalog / name
        old_folder = catalog / ("_" + name + "_old")
        if not source_folder.is_dir():
            continue
        files = [p for p in source_folder.iterdir()
                 if p.is_file() and p.suffix.lower() in SOURCE_SUFFIXES]
        # A retired folder with no staging folder means an earlier run already
        # swapped the files in; only the indexes can still be behind.
        if args.rewrite_only or not files or (
                old_folder.is_dir() and not (catalog / (name + "_new")).is_dir()):
            print(f"{name}: files already converted, indexes only", flush=True)
        else:
            print(f"{name}: {len(files):,} files, {human(folder_bytes(source_folder))}",
                  flush=True)
            report = prepare(catalog, name, name + "_new", args.limit,
                             args.workers, args.dry_run)
            print(f"  {report['files']:,} encoded in {report['seconds']:.0f}s: "
                  f"{human(report['before'])} -> {human(report['after'])} "
                  f"({100 * report['after'] / max(report['before'], 1):.0f}%), "
                  f"{report['kept']:,} kept", flush=True)
            if args.dry_run:
                print("  dry run: catalog untouched", flush=True)
                continue
            if not report["swapped"]:
                print(f"  partial run: written to {name}_new, catalog untouched",
                      flush=True)
                continue
        if old_folder.is_dir() and old_folder.name not in retired:
            retired.append(old_folder.name)
        if name == "thumbs":
            records, changed, missing = rewrite_mods(catalog)
            print(f"  index.json: {records:,} packages, {changed:,} preview paths "
                  f"updated, {missing:,} unresolved", flush=True)
        else:
            records, changed = rewrite_library(catalog)
            print(f"  library.json: {records:,} cards, {changed:,} covers updated",
                  flush=True)

    if not retired:
        print("nothing to retire", flush=True)
        return
    if args.purge:
        print(f"purged {human(purge(catalog, retired))}", flush=True)
    else:
        total = sum(folder_bytes(catalog / name) for name in retired
                    if (catalog / name).is_dir())
        print(f"originals kept in {', '.join(retired)} ({human(total)}); "
              f"delete them or re-run with --purge", flush=True)


if __name__ == "__main__":
    main()
