"""Audit card dependencies and recover matching public KK zipmods by manifest GUID."""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
import importlib.util
import io
import json
from pathlib import Path
import re
import shutil
from types import SimpleNamespace
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

APP = Path(__file__).resolve().parent.parent
INSTALLATION = APP / "release" / "KoikatsuModManager"
WORK = APP / "mod-recovery" / "2026-10-23"
BASE = "https://sideload.betterrepack.com/download/KKEC/"
HEADERS = {"User-Agent": "KoikatsuModManager-dependency-audit/1.0"}


def save(name, value):
    WORK.mkdir(parents=True, exist_ok=True)
    target = WORK / name
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), "utf-8")
    temporary.replace(target)


def load(name):
    return json.loads((WORK / name).read_text("utf-8"))


def request(url, headers=None):
    return urllib.request.urlopen(
        urllib.request.Request(url, headers=HEADERS | (headers or {})), timeout=30)


def source_bytes(url):
    with request(url) as response:
        data = response.read(32 * 1024 * 1024 + 1)
    if len(data) > 32 * 1024 * 1024:
        raise ValueError("Extra source exceeds the metadata inspection limit")
    if urllib.parse.urlsplit(url).hostname == "api.github.com":
        document = json.loads(data)
        if document.get("encoding") != "base64":
            raise ValueError("GitHub asset is not available as base64 content")
        data = base64.b64decode(document["content"])
    return data


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = set()

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.links.update(value for key, value in attrs if key == "href" and value)


def listing(url):
    parser = Links()
    with request(url) as response:
        parser.feed(response.read(4 * 1024 * 1024).decode("utf-8"))
    found = []
    for href in parser.links:
        target = urllib.parse.urljoin(url, href)
        if (target.startswith(url) and target != url and "?" not in target
                and (target.endswith("/") or target.lower().endswith((".zipmod", ".zip")))
                and "/theme/" not in target):
            found.append(target)
    return sorted(found)


def normalize(value):
    return "".join(character for character in value.casefold() if character.isalnum())


def filename(url):
    return urllib.parse.unquote(urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1])


def owner_matches(name, missing):
    name = normalize(name)
    if len(name) < 3:
        return False
    aliases = {
        "darksoldier27": ["ds27"],
        "darkkingdkcquiet": ["quiet"],
        "deathweaselakaanon11": ["deathweasel"],
    }
    owner_names = [name, *aliases.get(name, [])]
    for row in missing:
        values = [row["guid"], *row["authors"]]
        for value in values:
            value = normalize(value)
            if any(owner in value or (len(value) >= 4 and value in owner)
                   for owner in owner_names):
                return True
    return False


def audit():
    configuration = json.loads((INSTALLATION / "config.json").read_text("utf-8"))
    catalog = INSTALLATION / "catalog"
    spec = importlib.util.spec_from_file_location("recovery_catalog", APP / "Build-ModCatalog.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    records = builder.sync_records(Path(configuration["mods"]), catalog)
    cards = json.loads((catalog / "library.json").read_text("utf-8"))
    installed = {record["guid"].casefold() for record in records if record.get("guid")}
    rows = {}
    for card in cards:
        seen = set()
        for reference in card.get("references", []):
            guid = reference["guid"]
            if guid.casefold() in installed:
                continue
            row = rows.setdefault(guid, {"guid": guid, "authors": [], "websites": [],
                                         "cards": [], "kinds": {}})
            for key, field in (("authors", "author"), ("websites", "website")):
                value = reference.get(field, "").strip()
                if value and value not in row[key]:
                    row[key].append(value)
            if guid not in seen:
                row["cards"].append(card["path"])
                row["kinds"][card["kind"]] = row["kinds"].get(card["kind"], 0) + 1
                seen.add(guid)
    missing = sorted(rows.values(), key=lambda row: -len(row["cards"]))
    save("missing-before-downloads.json", missing)
    print(f"Audit: {len(records)} installed packages, {len(missing)} missing GUIDs", flush=True)
    return missing


def discover():
    missing = audit()
    roots = [url for url in listing(BASE)
             if any(filename(url.rstrip("/")) == name for name in (
                 "Sideloader Modpack", "Sideloader Modpack - Studio",
                 "Sideloader Modpack - Exclusive KK", "Sideloader Modpack - Exclusive KK KKS",
                 "Sideloader Modpack - Maps", "Sideloader Modpack - Fixes",
                 "Sideloader Modpack - Bleeding Edge"))]
    folders, files, errors = set(), set(), []
    for root in roots:
        try:
            for url in listing(root):
                if not url.endswith("/"):
                    files.add(url)
                elif owner_matches(filename(url.rstrip("/")), missing):
                    folders.add(url)
        except Exception as error:
            errors.append({"url": root, "error": str(error)})
    visited = set()
    for depth in range(4):
        pending = sorted(folders - visited)
        if not pending:
            break
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {pool.submit(listing, url): url for url in pending}
            for future in as_completed(futures):
                url = futures[future]
                visited.add(url)
                try:
                    for target in future.result():
                        if target.endswith("/"):
                            folders.add(target)
                        else:
                            files.add(target)
                except Exception as error:
                    errors.append({"url": url, "error": str(error)})
        print(f"Public index: {len(visited)} author folders, {len(files)} archives", flush=True)
    save("public-candidates.json", sorted(files))
    save("discovery-errors.json", errors)


class RemoteArchive(io.RawIOBase):
    """Read ZIP directory and manifest using bounded HTTP byte ranges."""
    def __init__(self, url):
        self.url = url
        self.position = 0
        self.cache = []
        with request(url, {"Range": "bytes=0-16383"}) as response:
            if response.status != 206:
                raise OSError("Server does not support partial archive reads")
            self.length = int(response.headers["Content-Range"].rsplit("/", 1)[1])
            self.cache.append((0, response.read(16384)))
        tail = max(0, self.length - 65536)
        if self.length > 16384:
            with request(url, {"Range": f"bytes={tail}-{self.length - 1}"}) as response:
                if response.status != 206:
                    raise OSError("Server does not support partial archive reads")
                self.cache.append((tail, response.read(65536)))

    def seekable(self):
        return True

    def readable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=0):
        self.position = offset + (self.length if whence == 2 else
                                  self.position if whence == 1 else 0)
        return self.position

    def read(self, size=-1):
        if size < 0:
            size = self.length - self.position
        size = min(size, self.length - self.position)
        if not size:
            return b""
        if size > 8 * 1024 * 1024:
            raise OSError("Archive metadata exceeds the partial-read limit")
        start = self.position
        for block_start, block in self.cache:
            if block_start <= start and start + size <= block_start + len(block):
                self.position += size
                return block[start - block_start:start - block_start + size]
        with request(self.url, {"Range": f"bytes={start}-{start + size - 1}"}) as response:
            if response.status != 206:
                raise OSError("Unexpected full response to partial archive request")
            data = response.read(size + 1)
            if len(data) != size:
                raise OSError("Partial archive response length mismatch")
            self.cache.append((start, data))
        self.position += size
        return data


def manifest(archive):
    names = [name for name in archive.namelist() if name.casefold() == "manifest.xml"]
    if len(names) != 1 or archive.getinfo(names[0]).file_size > 1024 * 1024:
        raise ValueError("Missing or oversized zipmod manifest")
    tree = ET.fromstring(archive.read(names[0]))
    values = {child.tag.casefold(): (child.text or "").strip()
              for child in tree if not list(child)}
    return {"guid": values.get("guid", ""), "name": values.get("name", ""),
            "author": values.get("author", ""), "version": values.get("version", ""),
            "game": values.get("game", "")}


def inspect_remote(url):
    with RemoteArchive(url) as stream, zipfile.ZipFile(stream) as archive:
        result = manifest(archive)
        result.update(url=url, bytes=stream.length)
        return result


def match():
    wanted = {row["guid"].casefold() for row in load("missing-before-downloads.json")}
    candidates = load("public-candidates.json")
    previous = load("remote-manifests.json") if (WORK / "remote-manifests.json").exists() else []
    scanned = {row["url"]: row for row in previous}
    pending = [url for url in candidates if url not in scanned]
    def priority(url):
        name = normalize(filename(url))
        return sum(len(row["cards"]) for row in load_missing
                   if any(normalize(part) in name for part in
                          re.split(r"[._\s-]+", row["guid"]) if len(normalize(part)) >= 5))
    load_missing = load("missing-before-downloads.json")
    pending.sort(key=priority, reverse=True)
    completed = 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(inspect_remote, url): url for url in pending}
        for future in as_completed(futures):
            url = futures[future]
            try:
                row = future.result()
            except Exception as error:
                row = {"url": url, "error": str(error)}
            scanned[url] = row
            completed += 1
            if row.get("guid", "").casefold() in wanted:
                print("Matched GUID: " + row["guid"] + " :: " + filename(url), flush=True)
            if completed % 30 == 0 or completed == len(pending):
                save("remote-manifests.json", list(scanned.values()))
                print(f"Manifest scan: {completed}/{len(pending)}", flush=True)
    matches = [row for row in scanned.values() if row.get("guid", "").casefold() in wanted]
    save("matched-downloads.json", matches)
    print(f"Found {len({r['guid'].casefold() for r in matches})} missing GUIDs", flush=True)


def install():
    configuration = json.loads((INSTALLATION / "config.json").read_text("utf-8"))
    mods = Path(configuration["mods"]).resolve()
    wanted = {row["guid"].casefold() for row in load("missing-before-downloads.json")}
    extras = load("extra-sources.json") if (WORK / "extra-sources.json").exists() else []
    candidates = [row for row in load("remote-manifests.json") + extras
                  if row.get("guid", "").casefold() in wanted]
    chosen = {}
    for row in candidates:
        game = row.get("game", "").casefold()
        source_path = urllib.parse.unquote(row["url"])
        if (game and game not in {"koikatu", "koikatsu", "kk"}
                or "Sideloader Modpack - KKS_" in source_path
                or "Sideloader Modpack - Exclusive KKS/" in source_path):
            continue
        key = row["guid"].casefold()
        version = tuple(int(part) for part in re.findall(r"\d+", row["version"]))
        old = chosen.get(key)
        if not old or version > tuple(int(part) for part in re.findall(r"\d+", old["version"])):
            chosen[key] = row
    downloaded = load("installed-downloads.json") if (WORK / "installed-downloads.json").exists() else []
    done = {row["guid"].casefold() for row in downloaded}
    for key, row in chosen.items():
        if key in done:
            continue
        name = filename(row["url"])
        if not name.casefold().endswith((".zipmod", ".zip")) or Path(name).name != name:
            raise ValueError("Unsafe download filename")
        author = row.get("installAuthor") or urllib.parse.unquote(
            urllib.parse.urlsplit(row["url"]).path.split("/")[-2])
        author = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", author).strip(". ") or "Unknown"
        folder = "ForStudioScenesLoading" if row.get("studio") or "/Sideloader%20Modpack%20-%20Studio/" in row["url"] \
            else "KK_-ExtraMaps" if "/Sideloader%20Modpack%20-%20Maps/" in row["url"] \
            else "ForCharactersLoading"
        target = mods / folder / author / name
        if not target.resolve().is_relative_to(mods):
            raise ValueError("Download target outside mods")
        temporary = WORK / "downloads" / (name + ".partial")
        temporary.parent.mkdir(parents=True, exist_ok=True)
        print(f"Downloading {row['guid']} ({row['bytes'] / 1048576:.1f} MB)", flush=True)
        if urllib.parse.urlsplit(row["url"]).hostname == "api.github.com":
            temporary.write_bytes(source_bytes(row["url"]))
        else:
            with request(row["url"]) as response, temporary.open("wb") as stream:
                shutil.copyfileobj(response, stream, 1024 * 1024)
        if temporary.stat().st_size != row["bytes"]:
            raise OSError("Downloaded archive size mismatch")
        with zipfile.ZipFile(temporary) as archive:
            actual = manifest(archive)
            if actual["guid"].casefold() != key or archive.testzip():
                raise ValueError("Downloaded archive failed GUID or CRC validation")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise FileExistsError(str(target))
        shutil.move(temporary, target)
        downloaded.append(row | {"installedPath": str(target)})
        save("installed-downloads.json", downloaded)
        print("Installed: " + str(target), flush=True)


def add_source(url):
    if not url.startswith("https://") or not filename(url).casefold().endswith(".zipmod"):
        raise ValueError("Expected a public HTTPS zipmod source")
    data = source_bytes(url)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        row = manifest(archive)
        if archive.testzip():
            raise ValueError("Source archive CRC failure")
        row.update(url=url, bytes=len(data), installAuthor=row["author"] or "Unknown",
                   studio=any("studio/" in name.casefold() for name in archive.namelist()))
    rows = load("extra-sources.json") if (WORK / "extra-sources.json").exists() else []
    rows = [old for old in rows if old["url"] != url] + [row]
    save("extra-sources.json", rows)
    print(json.dumps(row, ensure_ascii=False, indent=2), flush=True)


def refresh():
    sys.path.insert(0, str(APP))
    import ModCatalogServer as manager
    catalog = INSTALLATION / "catalog"
    before = json.loads((catalog / "library-summary.json").read_text("utf-8"))
    configuration = INSTALLATION / "config.json"
    server = SimpleNamespace(catalog=catalog, config_file=configuration)
    manager.apply_settings(server, manager.config.validate(
        manager.config.read(configuration), catalog))
    manager.initialize_catalog(server)
    after = json.loads((catalog / "library-summary.json").read_text("utf-8"))
    cards = json.loads((catalog / "library.json").read_text("utf-8"))
    missing = {}
    for card in cards:
        for guid in card.get("missingTokens", []):
            row = missing.setdefault(guid, {"guid": guid, "cards": [], "references": []})
            row["cards"].append({"path": card["path"], "kind": card["kind"]})
            for reference in card.get("references", []):
                if reference["guid"] == guid and reference not in row["references"]:
                    row["references"].append(reference)
    unresolved = sorted(missing.values(), key=lambda row: -len(row["cards"]))
    save("remaining-missing.json", unresolved)
    downloaded = load("installed-downloads.json")
    local = [
        {"file": "[73lac7c]Dark Magician Girl Set.zipmod", "guid": "73lac7c.DarkMagicianGirl"},
        {"file": "[73lac7c]Mana Set.zipmod", "guid": "73lac7c.ManaSet"},
        {"file": "[anse] blucher v1.zipmod", "guid": "anse_blucher_mod"},
    ]
    hits = load("matched-downloads.json") + load("extra-sources.json")
    done = {row["guid"].casefold() for row in downloaded}
    skipped = [row for row in hits if row["guid"].casefold() not in done]
    summary = {"before": before, "after": after,
               "missingGuidsBefore": len(load("missing-before-downloads.json")) + len(local),
               "missingGuidsAfter": len(unresolved), "localInstalled": local,
               "downloaded": downloaded, "skippedIncompatible": skipped,
               "uninstalledLocal": {
                   "path": r"C:\Users\Windows\Downloads\[Tigerjr] SW_Blingring_75 v1.0.zipmod",
                   "actualGuid": "SW_Blingring_75", "requiredGuid": "Tigerjr_SW_Blingring_75",
                   "reason": "Different GUID; AI_HeelsData and AI/HS2 list layout"},
               "publicIndex": BASE,
               "publicArchivesChecked": len(load("remote-manifests.json")),
               "blockedAuthorSources": [
                   {"url": "https://ux.getuploader.com/gyobobo/", "error": "HTTP 403",
                    "guid": "[gyobobo]Claire Set"}]}
    save("recovery-summary.json", summary)
    lines = [
        "# Mod recovery / 2026-10-23", "",
        f"- Local packages installed: {len(local)}",
        f"- Verified packages downloaded and installed: {len(downloaded)}",
        f"- Download size: {sum(row['bytes'] for row in downloaded) / 1048576:.1f} MB",
        f"- Distinct missing GUIDs: {summary['missingGuidsBefore']} -> {len(unresolved)}",
        f"- Resources with missing dependencies: {before['missing']} -> {after['missing']}",
        f"- Dependency records not recognized: {after['unknown']}",
        f"- Public KK archive manifests checked: {summary['publicArchivesChecked']}",
        "- Results cover parsed card/scene dependency records; unknown resources remain unverified.",
        "", "## Local package not installed", "",
        "`[Tigerjr] SW_Blingring_75 v1.0.zipmod` remains in Downloads.",
        "Its GUID is `SW_Blingring_75`, while the cards require `Tigerjr_SW_Blingring_75`.",
        "It contains AI_HeelsData and an AI/HS2 character list, rather than the required KK package.",
        "", "## Downloads", "", "| GUID | Version | Installed file | Source |",
        "| --- | --- | --- | --- |",
    ]
    lines.extend(f"| {row['guid']} | {row['version']} | {row['installedPath']} | {row['url']} |"
                 for row in downloaded)
    lines += ["", "## Found but incompatible with this KK installation", ""]
    lines.extend(f"- `{row['guid']}`: game={row['game'] or 'KKS-only source'}; {row['url']}"
                 for row in skipped)
    lines += ["", "The gyobobo author download page returned HTTP 403:",
              "https://ux.getuploader.com/gyobobo/",
              "Other remaining entries had no verified compatible download in the sources checked."]
    lines += ["", "## Remaining missing GUIDs", "",
              "| GUID | Affected resources | Author | Source hint |",
              "| --- | --- | --- | --- |"]
    for row in unresolved:
        authors = sorted({ref.get("author", "") for ref in row["references"] if ref.get("author")})
        websites = sorted({ref.get("website", "") for ref in row["references"] if ref.get("website")})
        lines.append(f"| {row['guid']} | {len(row['cards'])} | {', '.join(authors)} | {', '.join(websites)} |")
    (WORK / "recovery-report.md").write_text("\n".join(lines) + "\n", "utf-8")
    print(json.dumps({"localInstalled": len(local), "downloaded": len(downloaded),
                      "missingGuidsAfter": len(unresolved), "after": after},
                     ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["discover", "match", "install", "audit", "source", "refresh"])
    parser.add_argument("url", nargs="?")
    args = parser.parse_args()
    if args.action == "source":
        add_source(args.url or "")
    else:
        {"discover": discover, "match": match, "install": install,
         "audit": audit, "refresh": refresh}[args.action]()
