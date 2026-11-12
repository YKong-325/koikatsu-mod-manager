"""Read-only recursive mod inventory and original-thumbnail catalog."""

import argparse
import csv
import hashlib
import io
import json
import os
import re
import shutil
import threading
import time
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from xml.etree import ElementTree

from PIL import Image


APP_ROOT = Path(__file__).resolve().parent
GAME_ROOT = APP_ROOT.parent
PREVIEW_SCHEMA = 2
PREVIEW_EDGE = 512
PREVIEW_QUALITY = 82


CLOTHES = {
    "105": "上衣", "106": "下装", "107": "胸罩", "108": "内裤",
    "109": "手套", "110": "裤袜", "111": "袜子", "112": "鞋",
}
SYMBOLS = {
    "co_top": "上衣", "co_bot": "下装", "co_bra": "胸罩",
    "co_shorts": "内裤", "co_gloves": "手套", "co_panst": "裤袜",
    "co_socks": "袜子", "co_shoes": "鞋",
    "hair_front": "前发", "hair_back": "后发", "hair_side": "侧发",
    "hair_option": "其他头发",
}


def decode(raw):
    for encoding in ("utf-8-sig", "cp932", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", errors="replace")


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def preview_file(directory, key):
    """Return an already-written preview for this key, whatever its format."""
    for suffix in (".webp", ".png"):
        candidate = directory / (key + suffix)
        if candidate.is_file():
            return candidate
    return None


def encode_preview(image):
    """Return (suffix, bytes) for one preview, preferring WebP.

    These used to be RGBA PNGs: 47k files cost 1.1 GB, because full-colour
    512x512 art is exactly the worst case for PNG. WebP keeps the alpha channel
    and the same edge cap at roughly a fifth of the bytes. Tiny flat icons can
    still pack smaller as PNG, so those keep whichever of the two wins.
    """
    if max(image.size) > PREVIEW_EDGE:
        image = image.copy()
        image.thumbnail((PREVIEW_EDGE, PREVIEW_EDGE))
    rgba = image.convert("RGBA")
    if rgba.getchannel("A").getextrema()[0] >= 250:
        rgba = rgba.convert("RGB")
    payload = io.BytesIO()
    rgba.save(payload, "WEBP", quality=PREVIEW_QUALITY, method=4)
    suffix = ".webp"
    if max(rgba.size) <= 128:
        alternative = io.BytesIO()
        rgba.save(alternative, "PNG", optimize=True)
        if alternative.tell() < payload.tell():
            payload, suffix = alternative, ".png"
    return suffix, payload.getvalue()


def write_preview(image, directory, key):
    suffix, payload = encode_preview(image)
    target = directory / (key + suffix)
    temporary = target.with_name(f"{target.name}.{threading.get_ident()}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, target)
    return target


def parse_list(text, filename):
    rows = list(csv.reader(io.StringIO(text, newline="")))
    header_index = next(
        (i for i, row in enumerate(rows)
         if row and row[0].strip().upper() == "ID" and "Name" in row),
        None,
    )
    if header_index is None:
        return []
    header = [cell.strip() for cell in rows[header_index]]
    code = rows[0][0].strip() if rows and rows[0] else ""
    preamble = "\n".join(",".join(row) for row in rows[:header_index]).lower()
    low = filename.lower()
    is_studio = "abdata/studio/info/" in low
    category = "工作室" if is_studio else "捏脸/衣橱"
    slot = ""
    if not is_studio:
        for token, label in SYMBOLS.items():
            if token in preamble:
                slot = label
                break
        if not slot and "/ao_" in preamble:
            slot = "配件"
        if not slot:
            slot = CLOTHES.get(code, "清单类别 " + code)
    else:
        slot = "工作室条目"
    result = []
    for values in rows[header_index + 1:]:
        if not values or not values[0].strip().isdigit():
            continue
        row = dict(zip(header, values))
        if not row.get("Name"):
            continue
        # A list entry can describe a texture or animation as well as a model.
        result.append({
            "Name": row["Name"], "ID": row.get("ID", ""),
            "Category": category, "Slot": slot, "ListCategory": code,
            "Kind": row.get("Kind", ""), "ListFile": filename,
            "MainAB": row.get("MainAB", row.get("Main AssetBundle", "")),
            "MainData": row.get("MainData", row.get("Main Data", "")),
            "ThumbAB": row.get("ThumbAB", ""),
            "ThumbTex": row.get("ThumbTex", ""),
        })
    return result


def manifest(z, names):
    name = next((n for n in names if n.lower() == "manifest.xml"), None)
    if name is None:
        name = next((n for n in names if n.lower().endswith("/manifest.xml")), None)
    if not name:
        return {}
    root = ElementTree.fromstring(z.read(name))
    return {child.tag.rsplit("}", 1)[-1].lower(): (child.text or "").strip()
            for child in root}


def extract_original(z, info, relative, out):
    if info.file_size > 16 * 1024 * 1024:
        return None
    key = digest(relative + "|" + info.filename + "|" + str(info.CRC) +
                 "|" + str(info.file_size))
    target = preview_file(out / "thumbs", key)
    if target is None:
        with Image.open(io.BytesIO(z.read(info))) as image:
            target = write_preview(image, out / "thumbs", key)
    return {"src": "thumbs/" + target.name, "name": info.filename,
            "source": "archive-image"}


def asset_path(value):
    return value.replace("\\", "/").lstrip("/").casefold()


def thumbnail_sources(z, rec):
    """Use list references as well as dedicated thumbnail bundle names."""
    infos = {asset_path(info.filename): info for info in z.infolist() if not info.is_dir()}
    wanted = {}
    for item in rec["items"]:
        bundle, texture = item.get("ThumbAB", "").strip(), item.get("ThumbTex", "").strip()
        if not bundle or not texture or bundle == "0" or texture == "0":
            continue
        key = asset_path(bundle)
        if not key.startswith("abdata/"):
            key = "abdata/" + key
        wanted.setdefault(key, set()).add(texture.casefold())
    rec["missingThumbBundles"] = sorted(key for key in wanted if key not in infos)
    bundles = {asset_path(bundle["path"]) for bundle in rec["thumbBundles"]}
    bundles.update(key for key in wanted if key in infos)
    rec["thumbBundles"] = [{"path": infos[key].filename, "bytes": infos[key].file_size}
                           for key in sorted(bundles) if key in infos]
    return wanted


def texture_lookup(names):
    """Map both the listed name and its extension-less stem to the CSV spelling.

    Lists routinely name the texture with an image extension (`nyxheadth.png`)
    while the object inside the bundle is plain (`nyxheadth`), so an exact
    string compare silently drops previews that are actually there.
    """
    lookup = {}
    for raw in names:
        lookup.setdefault(raw, set()).add(raw)
        stem = Path(raw).stem
        if stem and stem != raw:
            lookup.setdefault(stem, set()).add(raw)
    return lookup


def decode_thumbnail_bundle(data, bundle_path, fingerprint, names, rec, out,
                            source_kind="unity-thumbnail"):
    import UnityPy

    seen = {image["src"] for image in rec["images"]}
    lookup = texture_lookup(names or ())
    resolved = set()
    env = UnityPy.load(data)
    for obj in env.objects:
        if obj.type.name not in {"Texture2D", "Sprite"}:
            continue
        if obj.type.name == "Sprite" and not names:
            continue
        name = obj.peek_name() or ""
        key = name.casefold()
        originals = lookup.get(key)
        if names and not originals:
            continue
        if originals:
            resolved.update(originals)
        image_key = digest(rec["path"] + "|" + bundle_path +
                           "|" + str(fingerprint) + "|" + str(obj.path_id))
        target = preview_file(out / "thumbs", image_key)
        if target is not None and ("thumbs/" + target.name) in seen:
            continue
        try:
            if target is None:
                target = write_preview(obj.read().image, out / "thumbs", image_key)
            source = "thumbs/" + target.name
            if source in seen:
                continue
            rec["images"].append({
                "src": source, "name": bundle_path + " :: " + name,
                "source": source_kind, "bundle": bundle_path, "texture": name,
            })
            seen.add(source)
        except Exception as exc:
            rec["previewErrors"].append(bundle_path + " :: " + name + ": " + str(exc))
    if names and names - resolved:
        rec["previewErrors"].append(bundle_path + ": texture not found: " +
                                    ", ".join(sorted(names - resolved)))


def extract_thumbnails(z, rec, out):
    wanted = thumbnail_sources(z, rec)
    seen = {image["src"] for image in rec["images"] if (out / image["src"]).is_file()}
    rec["images"] = [image for image in rec["images"] if image["src"] in seen]
    rec["previewErrors"] = []
    for bundle in rec["thumbBundles"]:
        key = asset_path(bundle["path"])
        names = wanted.get(key)
        # Model bundles can contain skin maps; only extract their listed previews.
        dedicated = "thumb" in key or key.endswith((".png", ".jpg", ".jpeg", ".webp"))
        if not names and not dedicated:
            continue
        if bundle["bytes"] > 256 * 1024 * 1024:
            rec["previewErrors"].append(bundle["path"] + ": thumbnail bundle exceeds 256 MB")
            continue
        try:
            decode_thumbnail_bundle(z.read(bundle["path"]), bundle["path"],
                                    z.getinfo(bundle["path"]).CRC, names, rec, out)
        except Exception as exc:
            rec["previewErrors"].append(bundle["path"] + ": " + str(exc))
    rec["previewSchema"] = PREVIEW_SCHEMA


def extract_game_thumbnails(rec, game, out):
    sources = {}
    root = game.resolve()
    for key in rec.get("missingThumbBundles", []):
        path = (root / key).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            continue
        stat = path.stat()
        sources[key] = {"bytes": stat.st_size, "mtimeNs": stat.st_mtime_ns}
    if (rec.get("gameThumbSources") == sources and
            all((out / image["src"]).is_file() for image in rec["images"])):
        return
    rec["images"] = [image for image in rec["images"] if image["source"] != "game-thumbnail"]
    rec["previewErrors"] = rec.get("previewErrors", [])
    for key, stamp in sources.items():
        if stamp["bytes"] > 256 * 1024 * 1024:
            continue
        names = {item["ThumbTex"].strip().casefold() for item in rec["items"]
                 if asset_path(item.get("ThumbAB", "")).removeprefix("abdata/") ==
                 key.removeprefix("abdata/") and item.get("ThumbTex")}
        try:
            decode_thumbnail_bundle((root / key).read_bytes(), key,
                                    str(root) + "|" + str(stamp), names, rec, out,
                                    source_kind="game-thumbnail")
        except Exception as exc:
            rec["previewErrors"].append(key + ": " + str(exc))
    rec["gameThumbSources"] = sources


def scan_one(path, mods, out, relative=None):
    """Scan one archive into a catalog record.

    `relative` overrides the path stored in the record. Preview keys are derived
    from it, so scanning a disabled "<name>.modmanager-<stamp>.disabled" file
    with its original name lands on the previews extracted when the mod was
    still installed instead of writing a second copy beside them.
    """
    if relative is None:
        relative = path.relative_to(mods).as_posix()
    rec = {"path": relative, "file": path.name, "bytes": path.stat().st_size,
           "name": "", "guid": "", "author": "", "items": [], "images": [],
           "thumbBundles": [], "errors": [], "dataDirs": []}
    try:
        with zipfile.ZipFile(path) as z:
            infos = z.infolist()
            names = [i.filename for i in infos]
            try:
                mf = manifest(z, names)
                rec.update({k: mf.get(k, "") for k in ("name", "guid", "author")})
            except Exception as exc:
                rec["errors"].append("manifest: " + str(exc))
            dirs = set()
            for info in infos:
                low = info.filename.replace("\\", "/").lower()
                if info.is_dir():
                    continue
                match = re.match(r"abdata/([^/]+)/", low)
                if match and match[1] != "list":
                    dirs.add(match[1])
                if (low.endswith(".csv") and
                    ("abdata/list/" in low or "abdata/studio/info/" in low)):
                    try:
                        rec["items"].extend(parse_list(decode(z.read(info)), low))
                    except Exception as exc:
                        rec["errors"].append(info.filename + ": " + str(exc))
                if low.endswith(".unity3d") and "thumb" in low:
                    rec["thumbBundles"].append(
                        {"path": info.filename, "bytes": info.file_size})
                if (low.endswith((".png", ".jpg", ".jpeg", ".webp")) and
                    ("thumb" in low or re.match(r"abdata/img-", low))):
                    try:
                        image = extract_original(z, info, relative, out)
                        if image:
                            rec["images"].append(image)
                    except Exception as exc:
                        with z.open(info) as image_stream:
                            signature = image_stream.read(8)
                        if signature.startswith((b"UnityFS", b"UnityRaw", b"UnityWeb")):
                            rec["thumbBundles"].append(
                                {"path": info.filename, "bytes": info.file_size})
                        else:
                            rec["errors"].append(info.filename + ": " + str(exc))
            rec["dataDirs"] = sorted(dirs)
            extract_thumbnails(z, rec, out)
    except Exception as exc:
        rec["errors"].append("archive: " + str(exc))
    rec["slots"] = sorted({item["Slot"] for item in rec["items"]})
    return rec


def write_csv(path, rows, fields):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def merge_renders(records, out):
    """Attach locally rendered previews to packages that ship no picture.

    The renders live in their own manifest instead of the scanned record, so a
    rebuild that re-opens every archive does not throw away a picture that was
    drawn for one. A real preview always wins: the render is only a fallback.
    """
    manifest_path = out / "renders.json"
    if not manifest_path.is_file():
        return
    try:
        manifest = json.loads(manifest_path.read_text("utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(manifest, dict):
        return
    for record in records:
        if record["images"]:
            continue
        entry = manifest.get(record["path"])
        if not isinstance(entry, dict):
            continue
        source = entry.get("src") or ""
        if not source or not (out / source).is_file():
            continue
        record["images"].append({
            "src": source,
            "name": entry.get("name") or "网格渲染预览",
            "source": "mesh-render",
        })


def save(records, out, old, elapsed):
    merge_renders(records, out)
    old_rows = []
    if old.exists():
        with old.open(encoding="utf-8-sig", newline="") as f:
            old_rows = list(csv.DictReader(f))
    old_names = {row["Mod"].casefold() for row in old_rows}
    guid_counts = Counter(r["guid"] for r in records if r["guid"])
    summary = {
        "packages": len(records), "items": sum(len(r["items"]) for r in records),
        "packagesWithImages": sum(bool(r["images"]) for r in records),
        "images": sum(len(r["images"]) for r in records),
        "packagesWithThumbBundles": sum(bool(r["thumbBundles"]) for r in records),
        "packagesWithoutParsedItems": sum(not r["items"] for r in records),
        "packagesWithErrors": sum(bool(r["errors"]) for r in records),
        "oldPackages": len(old_rows),
        "oldNamesWithReplacementCharacters": sum("\ufffd" in r["Mod"] for r in old_rows),
        "currentPathsMatchingOldBasenames": sum(r["file"].casefold() in old_names for r in records),
        "duplicateGuidGroups": sum(count > 1 for count in guid_counts.values()),
        "seconds": round(elapsed, 1),
        "notes": [
            "Names and list entries are not proof of a standalone mesh.",
            "No parsed entries does not imply unused or safe to remove.",
            "Original thumbnails may depict several entries together.",
            "Duplicate GUIDs require version and content checks before removal.",
            "PreviewSource identifies original images and decoded thumbnail textures.",
        ],
    }
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    # index.json and data.js carry the same 24 MB of records; serialise once and
    # share the text, or every save pays for the dump twice.
    payload = json.dumps(records, ensure_ascii=False, separators=(",", ":"))
    (out / "index.json").write_text(payload, encoding="utf-8")
    (out / "data.js").write_text("window.MOD_CATALOG=" + payload + ";", encoding="utf-8")
    package_rows = [{
        "Mod": r["path"], "Guid": r["guid"], "ModName": r["name"],
        "Author": r["author"], "SizeMB": round(r["bytes"] / 1048576, 2),
        "ItemCount": len(r["items"]), "Slots": ";".join(r["slots"]),
        "ImageCount": len(r["images"]), "ThumbBundleCount": len(r["thumbBundles"]),
        "DataDirs": ";".join(r["dataDirs"]), "Errors": " | ".join(r["errors"]),
    } for r in records]
    write_csv(out / "packages.csv", package_rows, list(package_rows[0]) if package_rows else ["Mod"])
    fields = ["Mod", "Guid", "ModName", "Author", "Name", "ID", "Category",
              "Slot", "ListCategory", "Kind", "ListFile", "MainAB", "MainData",
              "ThumbAB", "ThumbTex"]
    write_csv(out / "items.csv",
              (dict(item, Mod=r["path"], Guid=r["guid"], ModName=r["name"], Author=r["author"])
               for r in records for item in r["items"]), fields)
    template = Path(__file__).with_name("ModManager.html")
    if not template.is_file():
        raise FileNotFoundError(
            "ModManager.html is missing next to the builder; the page shell has a "
            "single source of truth and is never generated from this script.")
    (out / "gallery.html").write_text(template.read_text("utf-8"), encoding="utf-8")
    # Front-end modules are copied as-is; keep this list in sync with the
    # <script> tags in ModManager.html.
    for name in ("app-core.js", "media-thumbs.js", "view-mods.js",
                 "view-trash.js", "view-archive.js",
                 "plugin-deps.js", "preview-ui.js",
                 "mod-search.js", "manager.css", "theme.css"):
        asset = Path(__file__).with_name(name)
        if asset.exists():
            (out / name).write_text(asset.read_text("utf-8"), encoding="utf-8")
    assets = Path(__file__).with_name("assets")
    if assets.is_dir():
        shutil.copytree(assets, out / "assets", dirs_exist_ok=True)
    print(json.dumps(summary, ensure_ascii=True, indent=2), flush=True)


def sync_records(mods, out, reuse=True, progress=None, game=None, workers=None):
    index = out / "index.json"
    previous = {r["path"]: r for r in json.loads(index.read_text("utf-8"))} \
        if index.exists() and reuse else {}
    records = []
    paths = [p for p in mods.rglob("*")
             if p.is_file() and p.suffix.lower() in {".zip", ".zipmod"}]
    if progress:
        progress(0, len(paths))
    def scan(path):
        relative = path.relative_to(mods).as_posix()
        stat = path.stat()
        old = previous.get(relative)
        if (old and old.get("bytes") == stat.st_size and
                old.get("mtimeNs", stat.st_mtime_ns) == stat.st_mtime_ns):
            record = dict(old)
            if record.get("previewSchema") != PREVIEW_SCHEMA:
                # Only packages that failed to find a listed texture can gain an
                # image from the looser name matching; re-decoding the rest would
                # cost minutes for nothing.
                if not record.get("images") and any(
                        "texture not found" in error
                        for error in record.get("previewErrors", [])):
                    record = scan_one(path, mods, out)
                else:
                    record["previewSchema"] = PREVIEW_SCHEMA
            elif any(not (out / image["src"]).is_file() for image in record["images"]):
                record = scan_one(path, mods, out)
        else:
            record = scan_one(path, mods, out)
        extract_game_thumbnails(record, game or mods.parent, out)
        record["mtimeNs"] = stat.st_mtime_ns
        return record

    # Unity bundles expand in memory; keep only a small number active together.
    count = workers if workers is not None else min(2, os.cpu_count() or 1)
    with ThreadPoolExecutor(max_workers=max(1, count), thread_name_prefix="mod-scan") as pool:
        for number, record in enumerate(pool.map(scan, paths), 1):
            records.append(record)
            if progress and (number % 50 == 0 or number == len(paths)):
                progress(number, len(paths))
    return records




def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mods", type=Path, default=GAME_ROOT / "mods")
    parser.add_argument("--out", type=Path, default=APP_ROOT / "catalog")
    parser.add_argument("--enrich", action="store_true")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "thumbs").mkdir(exist_ok=True)
    if args.enrich:
        enrich(args.mods, args.out)
        return
    paths = sorted(p for p in args.mods.rglob("*")
                   if p.is_file() and p.suffix.lower() in (".zipmod", ".zip"))
    started = time.monotonic()
    records = []
    print(f"Scanning {len(paths)} archives recursively", flush=True)
    for i, path in enumerate(paths, 1):
        records.append(scan_one(path, args.mods, args.out))
        if i % 250 == 0:
            print(f"{i}/{len(paths)} archives; "
                  f"{sum(len(r['items']) for r in records)} entries; "
                  f"{time.monotonic()-started:.1f}s", flush=True)
    save(records, args.out, args.out.parent / "zipmod_inventory._mods.csv",
         time.monotonic() - started)


def enrich(mods, out):
    started = time.monotonic()
    records = sync_records(mods, out, progress=lambda current, total:
                           print(f"Previews: {current}/{total}", flush=True))
    save(records, out, out.parent / "zipmod_inventory._mods.csv",
         time.monotonic() - started)


if __name__ == "__main__":
    main()
