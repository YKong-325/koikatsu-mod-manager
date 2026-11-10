"""Index local cards with full-file hashes and MessagePack resolver records."""

import argparse
import hashlib
import json
import mmap
import os
import re
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import msgpack
from PIL import Image

SCHEMA = 4
EXTENSIONS = {".png", ".scene", ".dat"}
KINDS = {"chara": "角色卡", "studio": "场景", "coordinate": "服装卡"}

def builtin_items(game, catalog):
    """Read shipped ChaListData tables, not sideloaded mod lists."""
    folder = Path(game) / "abdata" / "list" / "characustom"
    paths = sorted(folder.rglob("*.unity3d")) if folder.is_dir() else []
    stamps = {str(path.relative_to(folder)): [path.stat().st_size, path.stat().st_mtime_ns]
              for path in paths}
    cache = catalog / "builtin-items.json"
    if cache.exists():
        old = json.loads(cache.read_text("utf-8"))
        if (old.get("root") == str(folder.resolve()) and old.get("files") == stamps
                and not old.get("errors") and old.get("schema") == 1):
            return {tuple(pair) for pair in old["items"]}
    import UnityPy
    items, errors = set(), []
    for path in paths:
        try:
            env = UnityPy.load(path.read_bytes())
            for obj in env.objects:
                if obj.type.name != "TextAsset":
                    continue
                asset = obj.read()
                raw = asset.m_Script
                if isinstance(raw, str):
                    raw = raw.encode("utf-8", "surrogateescape")
                try:
                    data = msgpack.unpackb(raw, raw=False, strict_map_key=False)
                except (ValueError, msgpack.UnpackException):
                    continue
                if not isinstance(data, dict) or data.get("mark") != "【ChaListData】":
                    continue
                category, entries = data.get("categoryNo"), data.get("dictList")
                if type(category) is not int or not isinstance(entries, dict):
                    continue
                items.update((category, key) for key in entries if type(key) is int)
        except Exception as exc:
            errors.append({"file": str(path), "error": str(exc)})
    write_json(cache, {"schema": 1, "root": str(folder.resolve()), "files": stamps,
                       "items": sorted(items), "errors": errors})
    return items


def write_text_atomic(path, text):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def write_json(path, value, prefix=""):
    write_text_atomic(path, prefix + json.dumps(
        value, ensure_ascii=False, separators=(",", ":")) +
        (";" if prefix else ""))


def resolver_records(raw):
    """Decode bounded MessagePack maps, never infer GUIDs from arbitrary text."""
    found = {}
    offset = 0
    while True:
        offset = raw.find(b"\xa5ModID", offset)
        if offset < 0:
            break
        start = offset - 1
        offset += 6
        if start < 0 or not 0x81 <= raw[start] <= 0x8f:
            continue
        try:
            decoder = msgpack.Unpacker(raw=False, strict_map_key=False,
                                       max_buffer_size=65536)
            decoder.feed(raw[start:start + 65536])
            item = next(decoder)
            if (not isinstance(item, dict) or
                    not isinstance(item.get("ModID"), str) or
                    not isinstance(item.get("Slot"), int) or
                    not ("Property" in item or "SceneDicKey" in item)):
                continue
            guid = item["ModID"].strip()
            if not guid or len(guid) > 256:
                continue
            ref = {"guid": guid, "slot": item["Slot"],
                   "property": str(item.get("Property", "Studio item")),
                   "category": item.get("CategoryNo"),
                   "author": str(item.get("Author") or ""),
                   "website": str(item.get("Website") or "")}
            found[(guid, ref["slot"], ref["property"])] = ref
        except (ValueError, TypeError, StopIteration, msgpack.UnpackException):
            continue
    # Studio stores map/filter/ramp/BGM GUIDs as named strings, and patterns
    # inside ObjectPatternInfo rather than ordinary ResolveInfo records.
    for key, label in (("mapInfoGUID", "场景地图"), ("filterInfoGUID", "场景滤镜"),
                       ("rampInfoGUID", "场景色阶"), ("bgmInfoGUID", "场景 BGM"),
                       ("ObjectPatternInfo", "场景物品纹样")):
        marker = msgpack.packb(key, use_bin_type=True)
        offset = 0
        while True:
            start = raw.find(marker, offset)
            if start < 0:
                break
            offset = start + len(marker)
            try:
                decoder = msgpack.Unpacker(raw=False, strict_map_key=False,
                                           max_buffer_size=65536)
                decoder.feed(raw[offset:offset + 65536])
                value = next(decoder)
                entries = (list(value.values()) if key == "ObjectPatternInfo"
                           and isinstance(value, dict) else
                           [{"GUID": value, "Slot": None}] if key != "ObjectPatternInfo"
                           else [])
                for item in entries:
                    if not isinstance(item, dict):
                        continue
                    guid = item.get("GUID")
                    slot = item.get("Slot")
                    if (not isinstance(guid, str) or not guid.strip() or len(guid) > 256
                            or (key == "ObjectPatternInfo" and
                                (not isinstance(slot, int) or isinstance(slot, bool)
                                 or not isinstance(item.get("LocalSlot"), int)))):
                        continue
                    ref = {"guid": guid.strip(), "slot": slot, "property": label,
                           "category": None, "author": str(item.get("Author") or ""),
                           "website": str(item.get("Website") or "")}
                    found[(ref["guid"], slot, label)] = ref
            except (ValueError, TypeError, StopIteration, msgpack.UnpackException):
                continue
    return list(found.values())


def apply_dependencies(records, mods, builtins=None):
    builtins = builtins or set()
    by_guid = defaultdict(list)
    for mod in mods:
        if mod.get("guid"):
            by_guid[mod["guid"].casefold()].append(mod["path"])
    for record in records:
        refs = record.get("references", [])
        for ref in refs:
            category, slot = ref.get("category"), ref.get("slot")
            # Restrict the fallback to known Sunshine backport GUIDs. Arbitrary
            # mods can reuse base IDs without supplying the same asset.
            ref["providedByBase"] = (
                ref["guid"].casefold() not in by_guid and
                ref["guid"].casefold().startswith("kks.") and
                type(category) is int and type(slot) is int and
                (category, slot) in builtins)
        record["mods"] = sorted({path for ref in refs
                                 for path in by_guid.get(ref["guid"].casefold(), [])})
        record["missingTokens"] = sorted({ref["guid"] for ref in refs
                                         if ref["guid"].casefold() not in by_guid
                                         and not ref["providedByBase"]})
        record["baseGameTokens"] = sorted({ref["guid"] for ref in refs if ref["providedByBase"]})
        record["missing"] = bool(record["missingTokens"])
        record["dependencyStatus"] = "parsed" if refs else "unknown"
    groups = defaultdict(list)
    for record in records:
        if record.get("sha256"):
            groups[record["sha256"]].append(record["path"])
    for record in records:
        record["duplicates"] = groups.get(record.get("sha256"), [record["path"]])
        record["duplicate"] = len(record["duplicates"]) > 1


def save_library(records, catalog):
    # Only library.json is produced: the page fetches cards from /api/library
    # when the dependency view is opened, so a 38 MB script is no longer loaded
    # on every visit. A stale library.js from an older build is removed here.
    write_json(catalog / "library.json", records)
    stale = catalog / "library.js"
    if stale.exists():
        stale.unlink()
    summary = {
        "files": len(records),
        "kinds": {kind: sum(r["kind"] == kind for r in records)
                  for kind in sorted({r["kind"] for r in records})},
        "withModRefs": sum(bool(r["mods"]) for r in records),
        "missing": sum(r["missing"] for r in records),
        "unknown": sum(r["dependencyStatus"] == "unknown" for r in records),
        "duplicateFiles": sum(r["duplicate"] for r in records),
        "duplicateGroups": len({r["sha256"] for r in records if r["duplicate"]}),
        "thumbnails": sum(bool(r["thumb"]) for r in records),
        "errors": sum(bool(r.get("error")) for r in records),
    }
    write_json(catalog / "library-summary.json", summary)
    return summary


CHARACUSTOM = re.compile(r"(^|/)characustom/", re.IGNORECASE)
USAGE_KEYS = {"角色卡": "cards", "服装卡": "outfits", "场景": "scenes"}


def dependencies(mods, library):
    """Single source of truth for mod <-> card references.

    Mirrors what the browser used to recompute on every load, but keeps the
    base-game table (``providedByBase``) and the duplicate-GUID rules in one
    place. Everything downstream is a reader of this result.
    """
    normalize = lambda value: str(value or "").strip().casefold()
    by_guid = defaultdict(list)
    for mod in mods:
        guid = normalize(mod.get("guid"))
        if guid:
            by_guid[guid].append(mod)

    usage = defaultdict(lambda: {"cards": [], "outfits": [], "scenes": []})
    cards, mod_state = {}, {}
    empty = lambda: {"total": 0, "parsed": 0, "unknown": 0, "missing": 0, "complete": 0}
    coverage = empty()
    coverage_by_kind = {kind: empty() for kind in USAGE_KEYS}

    for resource in library:
        references = resource.get("references") or []
        guids = {normalize(ref.get("guid")) for ref in references} - {""}
        bucket_key = USAGE_KEYS.get(resource.get("kind"))
        for guid in (guids if bucket_key else ()):
            usage[guid][bucket_key].append(resource.get("path"))
        kind_coverage = coverage_by_kind.get(resource.get("kind"))
        if kind_coverage is None:
            continue
        missing = {}
        for ref in references:
            guid = normalize(ref.get("guid"))
            if not guid or guid in by_guid or ref.get("providedByBase"):
                continue
            # Insertion order keeps the first sighting, the value keeps the last
            # spelling, matching the browser analysis this replaces.
            missing[guid] = ref.get("guid")
        state = "unknown" if not guids else ("missing" if missing else "complete")
        kind_coverage["total"] += 1
        kind_coverage[state] += 1
        if guids:
            kind_coverage["parsed"] += 1
        if resource.get("kind") == "角色卡":
            coverage["total"] += 1
            coverage[state] += 1
            if guids:
                coverage["parsed"] += 1
        cards[resource.get("path")] = {"state": state, "missing": list(missing.values())}

    summary = {"characterMods": 0, "matched": 0, "unmatched": 0, "unknown": 0}
    for mod in mods:
        guid = normalize(mod.get("guid"))
        refs = usage[guid] if guid in usage else {"cards": [], "outfits": [], "scenes": []}
        items = mod.get("items") or []
        character = bool(refs["cards"]) or any(
            item.get("Category") == "捏脸/衣橱" or
            CHARACUSTOM.search(str(item.get("ListFile") or "").replace("\\", "/"))
            for item in items) or (
            not items and any(normalize(directory) == "chara"
                              for directory in (mod.get("dataDirs") or [])))
        state = "other"
        if character:
            summary["characterMods"] += 1
            # Without a GUID or without any parsed card there is nothing to be
            # missing from; say so instead of reporting a false orphan.
            state = ("matched" if refs["cards"] else
                     "unknown" if not guid or not coverage["parsed"] else "unmatched")
            summary[state] += 1
        mod_state[mod.get("path")] = {
            "state": state, "cards": list(refs["cards"]),
            "outfits": list(refs["outfits"]), "scenes": list(refs["scenes"]),
            "duplicateGuid": len(by_guid.get(guid, [])) > 1}

    return {"cards": cards, "mods": mod_state, "coverage": coverage,
            "coverageByKind": coverage_by_kind, "summary": summary}


def save_dependencies(deps, catalog):
    # The snapshot goes out twice (JSON for the server, JS for the file:// page);
    # serialising once keeps every mod mutation from dumping 3 MB twice.
    payload = json.dumps(deps, ensure_ascii=False, separators=(",", ":"))
    write_text_atomic(catalog / "deps.json", payload)
    write_text_atomic(catalog / "deps.js", "window.KK_DEPS=" + payload + ";")
    return deps


def card_thumbnail(source, catalog, base):
    """Return the thumbnail path for a card, encoding one when it is missing.

    Card covers used to be JPEG at quality 84. WebP holds the same pixels in
    about two thirds of the bytes, and the format is already used for package
    previews; `.jpg` is still honoured so older catalogs keep working.
    """
    for suffix in (".webp", ".jpg"):
        if (catalog / (base + suffix)).is_file():
            return base + suffix
    target = catalog / (base + ".webp")
    temporary = target.with_name(f"{target.name}.{threading.get_ident()}.tmp")
    try:
        with Image.open(source) as image:
            image.thumbnail((320, 320))
            image.convert("RGB").save(temporary, "WEBP", quality=82, method=4)
        os.replace(temporary, target)
        return base + ".webp"
    except (OSError, ValueError, Image.DecompressionBombError):
        return None
    finally:
        temporary.unlink(missing_ok=True)


def build(userdata, catalog, mods=None, resource_roots=None, reuse=True, progress=None,
          game=None, workers=None):
    catalog.mkdir(parents=True, exist_ok=True)
    thumbs = catalog / "library_thumbs"
    thumbs.mkdir(exist_ok=True)
    old_file = catalog / "library.json"
    old_records = {r["path"]: r for r in json.loads(old_file.read_text("utf-8"))} \
        if old_file.exists() else {}
    previous = old_records if reuse else {}
    records = []
    sources = []
    if resource_roots is None:
        sources = [(p, p.relative_to(userdata).as_posix(), userdata.resolve())
                   for p in userdata.rglob("*")
                   if p.is_file() and p.suffix.lower() in EXTENSIONS]
    else:
        for prefix, root in resource_roots.items():
            if root is not None:
                root = Path(root).resolve()
                sources.extend((p, prefix + "/" + p.relative_to(root).as_posix(), root)
                               for p in root.rglob("*")
                               if p.is_file() and p.suffix.lower() in EXTENSIONS)
        # Keep indexing miscellaneous UserData folders from existing installations.
        if userdata.is_dir():
            for folder in userdata.iterdir():
                if folder.is_dir() and folder.name.lower() not in KINDS:
                    sources.extend((p, p.relative_to(userdata).as_posix(), userdata.resolve())
                                   for p in folder.rglob("*")
                                   if p.is_file() and p.suffix.lower() in EXTENSIONS)
    if progress:
        progress(0, len(sources))
    def scan(entry):
        source, relative, source_root = entry
        stat = source.stat()
        old = previous.get(relative, {})
        if (old.get("schema") == SCHEMA and old.get("mtimeNs") == stat.st_mtime_ns
                and old.get("bytes") == stat.st_size and not old.get("error")
                and (not old.get("thumb") or (catalog / old["thumb"]).is_file())):
            record = dict(old)
            # Tags and folder tags were dropped with the card view; an index
            # written by an older build still carries the empty keys.
            record.pop("tags", None)
            record.pop("folderTags", None)
        else:
            record = {
                "path": relative, "file": source.name,
                "kind": KINDS.get(relative.split("/")[0].lower(), "其他文件"),
                "bytes": stat.st_size, "modified": stat.st_mtime,
                "mtimeNs": stat.st_mtime_ns, "schema": SCHEMA,
                "thumb": None, "references": [], "sha256": None,
            }
            try:
                with source.open("rb") as stream:
                    record["sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
                    if stat.st_size:
                        with mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as raw:
                            record["references"] = resolver_records(raw)
                if source.suffix.lower() == ".png":
                    record["thumb"] = card_thumbnail(
                        source, catalog, "library_thumbs/" + record["sha256"][:24])
            except OSError as exc:
                record["error"] = str(exc)
        record["sourceRoot"] = str(source_root)
        return record

    count = workers if workers is not None else min(4, os.cpu_count() or 1)
    with ThreadPoolExecutor(max_workers=max(1, count), thread_name_prefix="card-scan") as pool:
        for index, record in enumerate(pool.map(scan, sources), 1):
            records.append(record)
            if progress and (index % 100 == 0 or index == len(sources)):
                progress(index, len(sources))
            if index % 500 == 0:
                print(f"Library: {index}/{len(sources)}", flush=True)
    if mods is None:
        mods = json.loads((catalog / "index.json").read_text("utf-8"))
    apply_dependencies(records, mods, builtin_items(game or userdata.parent, catalog))
    summary = save_library(records, catalog)
    save_dependencies(dependencies(mods, records), catalog)
    print(json.dumps(summary, ensure_ascii=True, indent=2), flush=True)
    return records


def main():
    parser = argparse.ArgumentParser()
    app_root = Path(__file__).resolve().parent
    parser.add_argument("--userdata", type=Path, default=app_root.parent / "UserData")
    parser.add_argument("--catalog", type=Path, default=app_root / "catalog")
    args = parser.parse_args()
    build(args.userdata, args.catalog)


if __name__ == "__main__":
    main()
