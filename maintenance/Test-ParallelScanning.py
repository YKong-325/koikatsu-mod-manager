"""Compare serial and parallel cold scans against the same disposable outputs."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import shutil
import statistics
import tempfile
import time
from unittest.mock import patch

APP = Path(__file__).resolve().parent.parent


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, APP / filename)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def main():
    builder = module("parallel_mods", "Build-ModCatalog.py")
    library = module("parallel_library", "Build-LibraryIndex.py")
    installation = APP / "release" / "KoikatsuModManager"
    config = json.loads((installation / "config.json").read_text("utf-8"))
    mods = Path(config["mods"])
    existing = json.loads((installation / "catalog" / "index.json").read_text("utf-8"))
    candidates = [r for r in existing if r["thumbBundles"] and r["images"] and
                  not r["errors"] and not r.get("previewErrors") and
                  (mods / r["path"]).is_file()]
    # Spread samples across authors and bundle layouts.
    samples = candidates[::max(1, len(candidates) // 48)][:48]
    paths = [mods / r["path"] for r in samples]
    report = {"modSamples": len(paths), "timings": {}}
    import UnityPy
    from UnityPy.export import Texture2DConverter
    elapsed = {}
    with tempfile.TemporaryDirectory(prefix="kk-parallel-test-") as temporary:
        root = Path(temporary)
        results = []
        for run, workers in enumerate((1, 2, 2, 1)):
            out = root / f"mods-{workers}-{run}"
            (out / "thumbs").mkdir(parents=True)
            start = time.perf_counter()
            with ThreadPoolExecutor(max_workers=workers) as pool:
                records = list(pool.map(lambda path: builder.scan_one(path, mods, out), paths))
            elapsed.setdefault(f"mods_{workers}_workers", []).append(time.perf_counter() - start)
            assert all((out / image["src"]).is_file() for r in records for image in r["images"])
            results.append(records)
        assert all(result == results[0] for result in results), "Serial and parallel mod scans differ"
        # Exercise sync_records itself, including reuse and progress, on valid fixtures.
        fixture_mods = root / "fixture-mods"
        fixture_mods.mkdir()
        for index, path in enumerate(paths[:6]):
            shutil.copyfile(path, fixture_mods / f"{index}.zipmod")
        catalog = root / "sync-catalog"
        (catalog / "thumbs").mkdir(parents=True)
        events = []
        records = builder.sync_records(fixture_mods, catalog, workers=2,
                                       progress=lambda done, total: events.append((done, total)))
        assert len(records) == 6 and events[0] == (0, 6) and events[-1] == (6, 6)
        (catalog / "index.json").write_text(json.dumps(records), "utf-8")
        with patch.object(builder, "scan_one", side_effect=AssertionError("Cached mod rescanned")):
            assert builder.sync_records(fixture_mods, catalog, workers=2) == records
        cards = json.loads((installation / "catalog" / "library.json").read_text("utf-8"))
        userdata = root / "UserData"
        (userdata / "chara").mkdir(parents=True)
        sources = [r for r in cards if r["kind"] == "角色卡" and r["references"]
                   and r["bytes"] < 16 * 1024 * 1024 and
                   (Path(config["userdata"]) / r["path"]).is_file()][:48]
        for index, record in enumerate(sources):
            shutil.copyfile(Path(config["userdata"]) / record["path"],
                            userdata / "chara" / f"{index}.png")
        # Identical images must be safe when workers share a thumbnail filename.
        for index in range(4):
            shutil.copyfile(userdata / "chara" / "0.png", userdata / "chara" / f"duplicate-{index}.png")
        results = []
        for run, workers in enumerate((1, 4, 4, 1)):
            catalog = root / f"cards-{workers}-{run}"
            start = time.perf_counter()
            with redirect_stdout(io.StringIO()):
                records = library.build(userdata, catalog, existing, workers=workers)
            elapsed.setdefault(f"cards_{workers}_workers", []).append(time.perf_counter() - start)
            results.append(records)
            assert all((catalog / r["thumb"]).is_file() for r in records if r["thumb"])
            assert not list((catalog / "library_thumbs").glob("*.tmp"))
        assert all(result == results[0] for result in results), "Serial and parallel card scans differ"
        assert all(r["duplicate"] for r in results[1] if r["file"].startswith("duplicate-"))
        with patch.object(library, "resolver_records", side_effect=AssertionError("Cached card rescanned")):
            with redirect_stdout(io.StringIO()):
                assert library.build(userdata, catalog, existing, workers=4) == results[1]
        report["cardSamples"] = len(results[1])
        report["timings"] = {key: round(statistics.median(values), 3)
                             for key, values in elapsed.items()}
        report["notes"] = "Fresh output caches; warmed imports; two runs per mode in reversed order. OS file cache is not cleared."
    output = APP / "mod-recovery" / "2026-10-29"
    output.mkdir(parents=True, exist_ok=True)
    (output / "parallel-scan-benchmark.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("PASS: serial/parallel result equality, real Unity thumbnails, duplicate image writes, "
          "dependency equality, progress and cache reuse")


if __name__ == "__main__":
    main()
