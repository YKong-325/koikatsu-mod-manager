"""Time the two write endpoints against a copy of the real index.

Disabling a mod and restoring one both rewrite the whole index, so their cost
tracks the size of the catalog rather than the single change. This replays each
handler against a throwaway copy of the live catalog and prints where the
seconds go, which is what any future tuning should be judged against:

    python maintenance/Bench-WritePaths.py            # uses release/ catalog
    python maintenance/Bench-WritePaths.py --catalog <dir>

The catalog is copied, never touched: the mods and card files referenced by the
index are created as stand-ins inside the same temp directory.
"""
import argparse
import json
import shutil
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ModCatalogServer as S  # noqa: E402  (needs the path above)

builder = S.load_module("catalog_builder", "Build-ModCatalog.py")
library_builder = S.library_builder


def measure(handler, label, action, detail):
    detail.clear()
    started = time.perf_counter()
    action()
    elapsed = time.perf_counter() - started
    print(f"{label:22s}{elapsed:6.2f}s  status {handler.responses[-1][0]}")
    for name, seconds in detail:
        print(f"   {name:24s}{seconds:6.2f}s")
    return elapsed


def run(catalog, userdata):
    index_file = catalog / "index.json"
    library_file = catalog / "library.json"
    if not index_file.is_file() or not library_file.is_file():
        raise SystemExit("both index.json and library.json are required in " + str(catalog))
    records = json.loads(index_file.read_text("utf-8"))
    library = json.loads(library_file.read_text("utf-8"))
    mod_record = next((r for r in records if r["path"].startswith("ForCharactersLoading/")),
                      records[0])

    work = Path(tempfile.mkdtemp(prefix="kk-bench-", dir=ROOT / "runtime"))
    try:
        copy = work / "catalog"
        copy.mkdir()
        for name in ("index.json", "library.json", "deps.json", "deps.js", "packages.csv",
                     "items.csv", "summary.json"):
            if (catalog / name).exists():
                shutil.copyfile(catalog / name, copy / name)
        mods = work / "mods"
        (mods / mod_record["path"]).parent.mkdir(parents=True, exist_ok=True)
        (mods / mod_record["path"]).write_bytes(b"x" * 1024)
        (work / "UserData" / "chara").mkdir(parents=True)

        server = types.SimpleNamespace(
            mods=mods, catalog=copy, userdata=work / "UserData",
            resource_roots={"chara": work / "UserData" / "chara",
                            "coordinate": work / "UserData" / "coordinate",
                            "studio": work / "UserData" / "studio"},
            records=work / "modtrash",
            settings={}, deps_cache=None, mutation_lock=threading.Lock())
        server.log = server.records / "delete-log.jsonl"
        server.records.mkdir()
        S.attach_indexes(server)

        detail = []

        def save_records(current):
            server.deps_cache = None
            started = time.perf_counter()
            builder.save(current, copy, copy.parent / "zipmod_inventory._mods.csv", 0)
            detail.append(("builder.save", time.perf_counter() - started))
            started = time.perf_counter()
            library = server.read_library()
            detail.append(("read library", time.perf_counter() - started))
            started = time.perf_counter()
            deps = library_builder.dependencies(current, library)
            detail.append(("dependencies()", time.perf_counter() - started))
            started = time.perf_counter()
            library_builder.save_dependencies(deps, copy)
            detail.append(("save_dependencies", time.perf_counter() - started))
            server.deps_cache = deps
            started = time.perf_counter()
            shutil.copyfile(copy / "items.csv", copy.parent / "zipmod_inventory.csv")
            shutil.copyfile(copy / "packages.csv", copy.parent / "zipmod_inventory._mods.csv")
            detail.append(("csv copies", time.perf_counter() - started))

        server.save_records = save_records

        class Handler(S.CatalogHandler):
            def __init__(self, srv):
                self.server = srv
                self.responses = []

            def _json(self, status, payload):
                self.responses.append((status, payload))

        handler = Handler(server)
        # Startup warms both parsed indexes, so measure the steady state.
        server.read_records()
        server.read_library()

        print(f"records {len(records):,} · library {len(library):,} · "
              f"index {index_file.stat().st_size / 1048576:.1f} MiB · "
              f"cards {library_file.stat().st_size / 1048576:.1f} MiB")
        total_delete = measure(handler, "disable a mod",
                               lambda: handler._delete({"path": mod_record["path"]}), detail)
        event_id = handler.responses[-1][1]["ids"][0]
        total_restore = measure(handler, "restore a mod",
                                lambda: handler._restore({"id": event_id}), detail)
        started = time.perf_counter()
        handler._dependencies()
        print(f"{'/api/deps after a restore':22s}{time.perf_counter() - started:6.2f}s")
        print(f"disable/restore ratio {total_delete / total_restore:.2f}x")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path,
                        default=ROOT / "release" / "KoikatsuModManager" / "catalog")
    parser.add_argument("--userdata", type=Path, default=ROOT.parent / "UserData")
    args = parser.parse_args()
    run(args.catalog, args.userdata)


if __name__ == "__main__":
    main()
