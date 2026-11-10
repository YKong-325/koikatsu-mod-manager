"""Draw a preview for every package that ships none.

A few thousand packages have no thumbnail: the author never packed one and the
game folder has no copy. Their meshes and textures are still in the archive, so
the picture is rendered locally instead of borrowed from an unrelated character
card (see ModMeshRender.py). Results are cached per package in renders.json, so
the pass is incremental: only packages without a picture and without a render
are attempted again.

    python maintenance/Render-ModThumbs.py --limit 40          # spot check
    python maintenance/Render-ModThumbs.py --workers 8         # the whole set
    python maintenance/Render-ModThumbs.py --force --limit 5   # redraw

The catalog index is rewritten at the end of a run (builder.save merges the
manifest), so a running manager has to be closed first.
"""
import argparse
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ModMeshRender  # noqa: E402  (needs the path above)

MANIFEST = "renders.json"
SCHEMA = 1
MAX_BYTES = 96 * 1024 * 1024


_BUILDER = None


def load_builder():
    global _BUILDER
    if _BUILDER is None:
        import ModCatalogServer as server
        _BUILDER = server.load_module("catalog_builder", "Build-ModCatalog.py")
    return _BUILDER


def render_one(job):
    """Runs in a worker process: draw one package, save its WebP, return entry."""
    path, catalog, key, size = job
    image = ModMeshRender.render_package(Path(path), size=size)
    if image is None:
        return None
    builder = load_builder()
    target = builder.write_preview(image, Path(catalog) / "thumbs", key)
    return {"src": "thumbs/" + target.name, "name": "网格渲染预览", "schema": SCHEMA}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path,
                        default=ROOT / "release" / "KoikatsuModManager" / "catalog")
    parser.add_argument("--mods", type=Path, default=ROOT.parent / "mods")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) // 2))
    parser.add_argument("--size", type=int, default=ModMeshRender.SIZE)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--sample", type=int, default=0,
                        help="pick a random subset instead of the first N")
    parser.add_argument("--no-save", action="store_true",
                        help="render only; leave index.json untouched")
    args = parser.parse_args(argv)

    catalog = args.catalog.resolve()
    index_file = catalog / "index.json"
    if not index_file.is_file():
        raise SystemExit("catalog index missing: " + str(index_file))
    records = json.loads(index_file.read_text("utf-8"))
    manifest_path = catalog / MANIFEST
    manifest = {}
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text("utf-8"))
            if not isinstance(manifest, dict):
                manifest = {}
        except ValueError:
            manifest = {}

    builder = load_builder()
    queue = []
    for record in records:
        if record["images"]:
            continue
        if record.get("bytes", 0) > MAX_BYTES:
            continue
        path = args.mods / Path(record["path"])
        if not path.is_file():
            continue
        key = builder.digest(record["path"] + "|mesh-render|" + str(SCHEMA))
        current = manifest.get(record["path"])
        if not args.force and current and (catalog / current.get("src", "")).is_file():
            continue
        queue.append((str(path), str(catalog), key, args.size, record["path"]))
    if args.sample and len(queue) > args.sample:
        import random
        random.seed(20261031)
        queue = random.sample(queue, args.sample)
    if args.limit:
        queue = queue[:args.limit]
    if not queue:
        print("nothing to render: every package without a preview already has one")
        return 0

    print(f"{len(queue)} package(s) to render with {args.workers} worker(s)", flush=True)
    started = time.perf_counter()
    done = failed = 0
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(render_one, job[:4]): job[4] for job in queue}
        for future in as_completed(futures):
            relative = futures[future]
            try:
                entry = future.result()
            except Exception as exc:  # a broken archive must not stop the pass
                entry = None
                print(f"  !! {relative}: {exc}", flush=True)
            done += 1
            if entry is None:
                failed += 1
                manifest[relative] = {"src": "", "name": "", "schema": SCHEMA}
            else:
                manifest[relative] = entry
            if done % 25 == 0 or done == len(queue):
                speed = done / max(time.perf_counter() - started, 1e-6)
                print(f"  {done}/{len(queue)} · {speed:.1f}/s · "
                      f"{failed} undrawable", flush=True)
    rendered = {path: entry for path, entry in manifest.items() if entry.get("src")}
    temporary = manifest_path.with_name(MANIFEST + ".tmp")
    temporary.write_text(json.dumps(rendered, ensure_ascii=False, indent=0), encoding="utf-8")
    os.replace(temporary, manifest_path)
    print(f"manifest: {len(rendered)} render(s) in {manifest_path.name}", flush=True)

    if not args.no_save:
        inventory = catalog.parent / "zipmod_inventory._mods.csv"
        builder.save(records, catalog, inventory, round(time.perf_counter() - started, 1))
        print("index.json rewritten; restart the manager to see the new previews", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
