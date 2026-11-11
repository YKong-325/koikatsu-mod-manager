"""Fetch the zipmods that cards ask for but this install does not have.

A missing mod is a dead end: the card opens, the item is invisible, and the
only clue is a GUID. Public integration packs ship most of those packages, and
their index is a plain HTTP directory listing, so a GUID can be turned back
into a download without any account or API key.

The cost of that is scanning: the index holds thousands of archives. Rather
than downloading them, only the ZIP central directory and `manifest.xml` are
read through HTTP range requests, which costs a few kilobytes per package.
Results are cached, so the expensive pass happens once.

    guids = missing(catalog, mods)                 # what is missing, from cards
    files = crawl()                                # every archive the index lists
    rows  = resolve(guids, files, progress=...)    # guid -> archive, via manifests
    install(rows, mods, progress=...)              # download, verify, place

Only `sideload.betterrepack.com` is ever contacted: the URL always comes from
its own listing, never from the page.
"""
import json
import re
import shutil
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from pathlib import Path

BASE = "https://sideload.betterrepack.com/download/KKEC/"
HOST = "sideload.betterrepack.com"
HEADERS = {"User-Agent": "KoikatsuModManager/4.0 (+local mod manager)"}
# Some mirrors answer the toolkit agent with a challenge page and a browser with
# the real directory listing. The index read is retried once with this one so a
# hostile-to-bots edge does not turn into "the pack is empty" for the user.
BROWSER_HEADERS = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                                  "Chrome/124.0.0.0 Safari/537.36")}
PACKS = (
    "Sideloader Modpack",
    "Sideloader Modpack - Studio",
    "Sideloader Modpack - Exclusive KK",
    "Sideloader Modpack - Exclusive KK KKS",
    "Sideloader Modpack - Maps",
    "Sideloader Modpack - Fixes",
    "Sideloader Modpack - Bleeding Edge",
)
# Sunshine keeps its own exclusive packs beside the shared ones, so a KKS
# install needs both lists or it downloads nothing from the three KKS_ folders.
PACKS_KKS = (
    "Sideloader Modpack",
    "Sideloader Modpack - Studio",
    "Sideloader Modpack - Exclusive KKS",
    "Sideloader Modpack - Exclusive KK KKS",
    "Sideloader Modpack - KKS_Maps",
    "Sideloader Modpack - KKS_MaterialEditor",
    "Sideloader Modpack - KKS_UncensorSelector",
    "Sideloader Modpack - Fixes",
    "Sideloader Modpack - Bleeding Edge",
)
FLAVOURS = {"kk": PACKS, "kks": PACKS_KKS}
# Manifest <game> values, split into the games they name. An empty field is the
# common case (6370 of 7548 archives) and means "shipped before the field
# existed", so it is accepted by both games -- rejecting it would leave a KKS
# user with only the 801 archives that bother to declare themselves.
#
# The field is a comma-separated list in the packs that bother to fill it, and
# "Koikatsu Sunshine" contains "Koikatsu", so the words are classified per name
# instead of compared as substrings: Sunshine is its own game, and an archive
# that lists both is usable by both.
GAME_WORDS = {"kks": ("sunshine", "kks"),
              "kk": ("koikatu", "koikatsu", "koikatsu party", "kk")}
# What the folder itself says it is. A Sunshine install ships its own executable
# and data folder; neither game directory is ever the other's parent.
MARKERS = {"kks": ("KoikatsuSunshine.exe", "KoikatsuSunshine_Data",
                   "KoikatsuSunshineVR.exe"),
           "kk": ("Koikatsu.exe", "Koikatsu_Data", "KoikatsuVR.exe")}
INDEX_TTL = 7 * 24 * 3600      # the listing changes slowly; a week is fine
WORKERS = 16            # the mirror answers in ~6 s; 16 in flight is the knee
PARTIAL = 8 * 1024 * 1024      # manifest reads are bounded to this


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = set()

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.links.update(value for key, value in attrs if key == "href" and value)


def _request(url, extra=None, timeout=25, headers=None):
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or parts.hostname != HOST:
        raise ValueError("只能访问公共整合站：" + url)
    merged = (headers or HEADERS) | (extra or {})
    return urllib.request.urlopen(
        urllib.request.Request(url, headers=merged), timeout=timeout)


def listing(url, headers=None):
    """The hrefs under one directory.

    The redirect target is what the links are resolved against: a 301 that
    moves the listing to another path would otherwise make every href look
    foreign to `startswith` and the directory would read as empty.
    """
    parser = _Links()
    with _request(url, headers=headers) as response:
        base = response.geturl()
        parser.feed(response.read(4 * 1024 * 1024).decode("utf-8", "replace"))
    prefix = base if base.endswith("/") else base + "/"
    found = []
    for href in parser.links:
        target = urllib.parse.urljoin(prefix, href)
        if (target.startswith(prefix) and target != prefix and "?" not in target
                and "/theme/" not in target
                and (target.endswith("/") or target.lower().endswith((".zipmod", ".zip")))):
            found.append(target)
    return sorted(found)


def probe(url):
    """Describe what a plain GET actually answers, for an error message.

    "The site is up but the index is empty" is only actionable with the status,
    where the request ended up, and whether the body held any links at all.
    """
    try:
        with _request(url) as response:
            body = response.read(65536).decode("utf-8", "replace")
            return (f"HTTP {response.status}，最终地址 {response.geturl()}，"
                    f"正文 {len(body)} 字节、{body.lower().count('<a ')} 个链接")
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"


def filename(url):
    return urllib.parse.unquote(urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1])


class _Remote:
    """Reads a ZIP's directory and manifest through bounded range requests."""

    def __init__(self, url):
        self.url = url
        self.position = 0
        self.blocks = []
        self.length = 0
        self._total = 0
        self._fetch(0, 16384)
        self.length = self._total
        if self.length > 16384:
            tail = max(0, self.length - 65536)
            self._fetch(tail, self.length - tail)

    def _fetch(self, start, size):
        with _request(self.url, {"Range": f"bytes={start}-{start + size - 1}"}) as response:
            if response.status != 206:
                raise OSError("服务器不支持分段读取")
            self._total = int(response.headers["Content-Range"].rsplit("/", 1)[1])
            self.blocks.append((start, response.read(size)))

    def seekable(self):
        return True

    def readable(self):
        return True

    def seek(self, offset, whence=0):
        self.position = (offset if whence == 0 else
                         self.position + offset if whence == 1 else
                         self.length + offset)
        return self.position

    def tell(self):
        return self.position

    def read(self, size=-1):
        if size < 0:
            size = self.length - self.position
        size = min(size, self.length - self.position)
        if size <= 0:
            return b""
        if size > PARTIAL:
            raise OSError("归档元数据超过分段读取上限")
        start = self.position
        for begin, block in self.blocks:
            if begin <= start and start + size <= begin + len(block):
                self.position += size
                return block[start - begin:start - begin + size]
        self._fetch(start, size)
        return self.read(size)


def manifest(archive):
    names = [name for name in archive.namelist() if name.casefold() == "manifest.xml"]
    if len(names) != 1 or archive.getinfo(names[0]).file_size > 1024 * 1024:
        raise ValueError("缺少 manifest.xml")
    root = ET.fromstring(archive.read(names[0]))
    values = {child.tag.casefold(): (child.text or "").strip()
              for child in root if not list(child)}
    return {"guid": values.get("guid", ""), "name": values.get("name", ""),
            "author": values.get("author", ""), "version": values.get("version", ""),
            "game": values.get("game", "")}


def inspect(url):
    """GUID and size of a remote archive, without downloading its payload."""
    stream = _Remote(url)
    with zipfile.ZipFile(stream) as archive:
        row = manifest(archive)
    row.update(url=url, bytes=stream.length)
    return row


def _read(path, default):
    try:
        return json.loads(Path(path).read_text("utf-8"))
    except (OSError, ValueError):
        return default


def _write(path, value):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), "utf-8")
    temporary.replace(target)


def missing(catalog, mods):
    """GUIDs referenced by cards that no installed package provides."""
    catalog = Path(catalog)
    return missing_from(_read(catalog / "index.json", []),
                        _read(catalog / "library.json", []))


def missing_from(records, cards):
    """Same, from the already-parsed index and library the server keeps.

    Keeps the author and website each card recorded, because a GUID alone is
    not something a person can act on: the author is what makes the search term.
    """
    installed = {(record.get("guid") or "").casefold() for record in records}
    rows = {}
    for card in cards:
        for reference in card.get("references", []):
            guid = (reference.get("guid") or "").strip()
            if not guid or guid.casefold() in installed:
                continue
            row = rows.setdefault(guid.casefold(), {
                "guid": guid, "authors": [], "websites": [], "cards": [], "kinds": {}})
            for key, field in (("authors", "author"), ("websites", "website")):
                value = (reference.get(field) or "").strip()
                if value and value not in row[key]:
                    row[key].append(value)
            if card["path"] not in row["cards"]:
                row["cards"].append(card["path"])
                kind = card.get("kind", "")
                row["kinds"][kind] = row["kinds"].get(kind, 0) + 1
    return sorted(rows.values(), key=lambda row: (-len(row["cards"]), row["guid"]))


def game_flavour(root=None):
    """Which game this install is: "kk" or "kks".

    Read from the markers the game folder ships, one level deep so a manager
    pointed at a parent folder still resolves. Ambiguous or bare folders fall
    back to "kk", which is what every catalogue built before this existed
    assumed -- the caller prints the answer, so a wrong guess is visible
    instead of silent.
    """
    found = set()
    if root:
        places = [Path(root)]
        try:
            places += [child for child in Path(root).iterdir() if child.is_dir()]
        except OSError:
            pass
        for place in places:
            for flavour, markers in MARKERS.items():
                if any((place / marker).exists() for marker in markers):
                    found.add(flavour)
    return found.pop() if len(found) == 1 else "kk"


def pack_roots(flavour="kk"):
    """The pack folders the index lists.

    Read once with the toolkit agent and, if that page turns out to carry no
    pack folders, once more as a plain browser: an edge that challenges the
    agent but serves browsers looks identical to "the pack is gone" here, and
    that difference decides whether the user can download anything at all.
    """
    packs = FLAVOURS.get(flavour, PACKS)
    links, failure = [], None
    for headers in (None, BROWSER_HEADERS):
        try:
            links = listing(BASE, headers=headers)
        except Exception as exc:
            failure = exc
            continue
        roots = [url for url in links if filename(url.rstrip("/")) in packs]
        if roots:
            return roots
    if links:
        raise OSError("公共整合站首页能打开，但没有列出整合包目录（"
                      + probe(BASE) + "）")
    raise OSError("无法连接公共整合站 " + BASE + "：" + str(failure))


def _cache_name(stem, flavour):
    return stem + ("" if flavour == "kk" else "-" + flavour) + ".json"


def crawl(catalog, progress=None, cancel=None, flavour="kk"):
    """Every archive the public index lists, cached for a week."""
    cache = Path(catalog) / _cache_name("missing-index", flavour)
    stored = _read(cache, {})
    if stored.get("files") and time.time() - stored.get("fetchedAt", 0) < INDEX_TTL:
        return stored["files"]
    roots = pack_roots(flavour)
    folders, files, visited = set(), set(), set()
    unread = []
    for root in roots:
        # One unreadable pack folder must not cost the other six: a single
        # renamed directory used to abort the whole crawl before anything was
        # cached, so every retry died the same way.
        try:
            for url in listing(root):
                (folders if url.endswith("/") else files).add(url)
        except Exception as exc:
            unread.append(f"{filename(root.rstrip('/'))}（{exc}）")
        if progress:
            progress(f"公共索引：{len(files)} 个归档")
    if not files and unread:
        raise OSError("读取整合包目录失败：" + "；".join(unread[:3]))
    for _ in range(4):
        pending = sorted(folders - visited)
        if not pending or (cancel and cancel()):
            break
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = {pool.submit(listing, url): url for url in pending}
            for future in as_completed(futures):
                url = futures[future]
                visited.add(url)
                try:
                    for target in future.result():
                        (folders if target.endswith("/") else files).add(target)
                except Exception:
                    continue
        if progress:
            progress(f"公共索引：{len(visited)} 个作者目录，{len(files)} 个归档")
    files = sorted(url for url in files
                   if "/Sideloader Modpack" in urllib.parse.unquote(url))
    _write(cache, {"fetchedAt": time.time(), "files": files})
    return files


def declares(row, flavour):
    """"yes", "no" or "unsaid" about the game an archive is for.

    An empty field is the common case (6370 of 7548 archives) and means
    "shipped before the field existed", so it is accepted by both games --
    rejecting it would leave a KKS user with only the 801 archives that bother
    to declare themselves.

    Each comma-separated name is classified on its own words. Substring
    matching would be wrong twice over: "Koikatsu Sunshine" contains
    "Koikatsu", and one archive in the index declares both games at once
    ("Koikatsu, Koikatsu Sunshine") and would otherwise fall between the lists.
    A name that belongs to neither game (AI Girl, Honey Select 2) is a "no".
    """
    named = set()
    for name in re.split(r"[,;/|]", row.get("game") or ""):
        words = set(re.sub(r"[^a-z]+", " ", name.casefold()).split())
        if not words:
            continue
        # Sunshine first: its name contains "Koikatsu", so one name must be
        # classified once, not counted for both games.
        for game in ("kks", "kk"):
            if any(set(marker.split()) <= words for marker in GAME_WORDS[game]):
                named.add(game)
                break
    if not named:
        return None if not (row.get("game") or "").strip() else False
    return flavour in named


def resolve(wanted, files, catalog, progress=None, cancel=None, report=None,
            flavour="kk"):
    """Map wanted GUIDs to archives by reading only each archive's manifest.

    The public index lists thousands of archives and one manifest costs a couple
    of range requests, so the order matters more than the count: archives whose
    file name already spells a wanted GUID are read first, and the scan stops as
    soon as every GUID has been found. A full pass is only paid when the names
    gave nothing away, and even then it resumes from the cache.

    `flavour` only decides which build of a mod is *preferred*: the pack ships
    the same GUID for both games, so a KK install picks the Koikatsu build when
    one exists. The other game's build is still returned, marked in `report`,
    because the archive belongs to whoever asked for it -- a shared `mods`
    folder, a second install, or a migration -- and whether the game will load
    it is the game's own decision (Sideloader skips a mismatched manifest with
    "because it's meant for ..."), not this module's.

    An unanswered GUID has two causes -- the pack does not ship it, or its
    archive could not be read -- and they look alike here. `report`, when given,
    is filled with the counts, the builds that only exist for the other game,
    and the archives that failed, so the caller can say which happened.
    """
    cache = Path(catalog) / _cache_name("missing-manifests", flavour)
    stored = _read(cache, {})
    # A read that failed is not an answer: keeping it would hide that archive
    # from every later run, and a timeout is exactly the kind of thing that
    # works on the second try.
    scanned = {url: row for url, row in stored.get("rows", {}).items()
               if "error" not in row}
    for old in [url for url in scanned if url not in files]:
        scanned.pop(old, None)
    pending = [url for url in files if url not in scanned]
    wanted_keys = {row["guid"].casefold() for row in wanted}
    # Archives whose filename already names the GUID are worth reading first,
    # and the experimental pack repeats the main one, so it goes last.
    keys = [re.sub(r"[^a-z0-9]", "", row["guid"].casefold()) for row in wanted]
    keys += [re.sub(r"[^a-z0-9]", "", author.casefold())
             for row in wanted for author in row.get("authors", [])]
    keys = [key for key in keys if len(key) >= 5]

    def rank(url):
        name = re.sub(r"[^a-z0-9]", "", filename(url).casefold())
        hits = sum(1 for key in keys if key in name)
        return (hits, "bleeding" not in url.casefold())

    pending.sort(key=rank, reverse=True)

    def matched():
        return {row["guid"].casefold() for row in scanned.values()
                if (row.get("guid") or "").casefold() in wanted_keys}

    done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(inspect, url): url for url in pending}
        for future in as_completed(futures):
            url = futures[future]
            try:
                scanned[url] = future.result()
            except Exception as error:
                scanned[url] = {"url": url, "error": str(error)}
            done += 1
            if done % 25 == 0 or done == len(pending):
                _write(cache, {"fetchedAt": time.time(), "rows": scanned})
                if progress:
                    progress(f"读取包内清单：{done}/{len(pending)} · "
                             f"已找到 {len(matched())}/{len(wanted)}")
            if cancel and cancel():
                break
            if done % 25 == 0 and len(matched()) == len(wanted_keys):
                break
    _write(cache, {"fetchedAt": time.time(), "rows": scanned})
    other = "kk" if flavour == "kks" else "kks"
    found = {}
    for row in scanned.values():
        key = (row.get("guid") or "").casefold()
        if key not in wanted_keys:
            continue
        declared = declares(row, flavour)
        # No declared game, but the file name says which build it is. That hint
        # is the only one those archives carry; it decides the ranking, never
        # whether the download is offered.
        name_says_kks = "KKS_" in urllib.parse.unquote(row["url"])
        compatible = declared is not False and not (
            declared is None and name_says_kks and flavour == "kk")
        # A declared build beats an undeclared one, and an undeclared one beats
        # the other game's build: the two are the same mod for two games, and
        # installing the wrong copy is worse than installing an old unversioned
        # one. Version only breaks ties inside the same rank.
        version = tuple(int(part) for part in re.findall(r"\d+", row.get("version", "")))
        score = (1 if compatible else 0, 1 if declared is not None else 0, version)
        if key not in found or score > found[key][0]:
            found[key] = (score, row)
    if report is not None:
        unreadable = [{"url": url, "error": row.get("error", "")}
                      for url, row in scanned.items() if row.get("error")]
        report.update({
            "scanned": len(scanned) - len(unreadable),
            "unreadable": unreadable,
            # Answered, but only by the build the other game declares. The
            # caller downloads it anyway and says so; silent is the one thing
            # this must not be, because the game may skip it on load.
            "fallbacks": [{"guid": row.get("guid", ""), "name": row.get("name", ""),
                           "version": row.get("version", ""),
                           "game": row.get("game", ""), "url": row["url"],
                           "reason": other}
                          for score, row in found.values() if not score[0]],
        })
    return {key: row for key, (_score, row) in found.items()}


def target_for(row, mods):
    """Where a downloaded archive belongs, following the game's own layout."""
    mods = Path(mods)
    name = filename(row["url"])
    if not name.casefold().endswith((".zipmod", ".zip")) or Path(name).name != name:
        raise ValueError("下载文件名不安全：" + name)
    author = (row.get("author") or urllib.parse.unquote(
        urllib.parse.urlsplit(row["url"]).path.split("/")[-2] or "")).strip()
    author = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", author).strip(". ") or "Unknown"
    decoded = urllib.parse.unquote(row["url"])
    if "/Sideloader Modpack - Studio/" in decoded:
        folder = "ForStudioScenesLoading"
    elif ("/Sideloader Modpack - Maps/" in decoded
          or "/Sideloader Modpack - KKS_Maps/" in decoded):
        folder = "KK_-ExtraMaps"
    else:
        folder = "ForCharactersLoading"
    destination = mods / folder / author / name
    if not destination.resolve().is_relative_to(mods.resolve()):
        raise ValueError("下载目标不在 mods 目录内")
    return destination


def download(row, destination, progress=None, cancel=None):
    """Fetch one archive, prove it is the package it claims to be, then place it."""
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError("已存在同名文件：" + destination.name)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".partial")
    try:
        with _request(row["url"], timeout=60) as response, partial.open("wb") as stream:
            total = int(response.headers.get("Content-Length") or row.get("bytes") or 0)
            received = 0
            while True:
                if cancel and cancel():
                    raise KeyboardInterrupt
                block = response.read(1024 * 1024)
                if not block:
                    break
                stream.write(block)
                received += len(block)
                if progress:
                    progress(received, total)
        if row.get("bytes") and partial.stat().st_size != row["bytes"]:
            raise OSError("下载大小与索引不符")
        with zipfile.ZipFile(partial) as archive:
            found = manifest(archive)
            if found["guid"].casefold() != row["guid"].casefold():
                raise ValueError("包内 GUID 与期望不符")
            if archive.testzip():
                raise ValueError("压缩包校验失败")
        partial.replace(destination)
        return destination
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
