"""Local manager for the generated Koikatsu mod catalog.

The delete endpoint renames an archive in place to
"<name>.modmanager-<stamp>.disabled", which keeps the game from loading it
without moving the file anywhere, and appends one line to delete-log.jsonl.
The restore endpoint renames it back. The purge endpoint is the only one that
erases an archive for good, and only ever a ".disabled" file. None of them
accepts an absolute path or a path outside the configured mods directory.
"""

import argparse
import datetime as dt
import importlib.util
import json
import mimetypes
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import traceback
import urllib.request
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, unquote_plus, urlparse
from AppPaths import APP_ROOT as DATA_ROOT, RESOURCE_ROOT
from BrowserOpen import open_url


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


library_builder = load_module("library_builder", "Build-LibraryIndex.py")
config = load_module("manager_config", "ManagerConfig.py")
APP_ROOT = RESOURCE_ROOT
GAME_ROOT = DATA_ROOT.parent
# Packages are streamed straight to disk, so the only real limit is free space.
# A fixed 800 MB ceiling used to reject larger archives outright -- which is
# exactly where the big shared map packs live (the two Sunshine map packs a
# card wanted here are 1.0 GB and 1.6 GB), and the failure was something the
# user could neither understand nor act on.
DISK_MARGIN = 512 * 1024 * 1024


def render_snapshot(server, cursor=0):
    """Progress of the thumbnail render job, plus the results the page has not read."""
    job = getattr(server, "render_job", None)
    if not job:
        return {"running": False, "total": 0, "done": 0, "failed": 0,
                "current": "", "updates": [], "cursor": 0, "error": ""}
    updates = job["updates"]
    cursor = max(0, min(cursor, len(updates)))
    return {"running": job["running"], "total": job["total"], "done": job["done"],
            "failed": job["failed"], "current": job["current"],
            "updates": updates[cursor:], "cursor": len(updates),
            "error": job.get("error", "")}


def missing_snapshot(server, cursor=0):
    """Progress of the missing-mod fetch job."""
    job = getattr(server, "fetch_job", None)
    if not job:
        return {"running": False, "stage": "", "message": "", "total": 0, "done": 0,
                "failed": 0, "matched": 0, "current": "", "updates": [], "cursor": 0,
                "error": "", "problems": [], "scanned": 0, "unreadable": 0,
                "fallbacks": [], "packs": [], "unreadPacks": [], "flavour": ""}
    updates = job["updates"]
    cursor = max(0, min(cursor, len(updates)))
    return {"running": job["running"], "stage": job["stage"], "message": job["message"],
            "requested": job.get("requested", 0),
            "total": job["total"], "done": job["done"], "failed": job["failed"],
            "matched": job["matched"], "current": job["current"],
            "updates": updates[cursor:], "cursor": len(updates),
            "error": job.get("error", ""), "problems": job.get("problems", [])[:20],
            "unmatched": job.get("unmatched", [])[:500],
            "scanned": job.get("scanned", 0),
            "unreadable": job.get("unreadable", 0),
            "fallbacks": job.get("fallbacks", [])[:200],
            "packs": job.get("packs", []),
            "unreadPacks": job.get("unreadPacks", []),
            "flavour": job.get("flavour", "")}


def confined(root, relative):
    if not isinstance(relative, str) or not relative or ":" in relative:
        raise ValueError("invalid relative path")
    path = Path(relative)
    resolved = (root / path).resolve()
    if path.is_absolute() or ".." in path.parts or root not in resolved.parents:
        raise ValueError("path outside configured directory")
    return resolved


def download_progress(name, received, total):
    """What is left to download, in the units a reader can check.

    The old line printed ``3/16 MB``: two bare numbers sharing one unit, so it
    read as "3 of 16 megabytes" only if you already knew which side was which,
    and it sat next to the package counter in the button ("下载 3/42"). Say the
    unit twice and add the percentage; without a Content-Length, say only what
    has arrived instead of inventing a denominator."""
    arrived = received / 1048576
    if total and total > 0:
        percent = min(100, int(received * 100 / total))
        return f"正在下载 {name} · {arrived:.1f} / {total / 1048576:.0f} MB（{percent}%）"
    return f"正在下载 {name} · 已下载 {arrived:.1f} MB"


class CatalogHandler(SimpleHTTPRequestHandler):
    server_version = "KoikatsuModCatalog/3.0"
    # Packages drawn between two index writes while rendering. Writing the index
    # costs about a second, so checkpoint rarely but often enough that an
    # interrupted hour-long run keeps nearly everything it drew.
    CHECKPOINT = 200

    def do_GET(self):
        if self.headers.get("Host") != self.server.authority:
            self.send_error(403)
            return
        endpoint = urlparse(self.path).path
        if endpoint == "/api/session":
            self._json(200, {"token": self.server.token,
                             "instance": getattr(self.server, "instance_key", "")})
        elif endpoint == "/api/config":
            self._json(200, {"values": self.server.settings,
                             "ready": self.server.ready,
                             "starting": getattr(self.server, "starting", False),
                             "error": self.server.config_error})
        elif endpoint == "/api/progress":
            self._json(200, getattr(self.server, "progress", {"message": "正在准备索引…"}))
        elif endpoint in ("/settings", "/startup", "/settings-ui.js", "/browser-ui.js",
                          "/theme.css") or endpoint.startswith("/assets/"):
            name = {"/settings": "Setup.html", "/startup": "BrowserLoading.html"}.get(
                endpoint, endpoint.lstrip("/"))
            try:
                target = (confined(APP_ROOT / "assets", name[len("assets/"):])
                          if endpoint.startswith("/assets/") else confined(APP_ROOT, name))
            except ValueError:
                self.send_error(403)
                return
            if not target.is_file():
                self.send_error(404)
                return
            raw = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(name)[0] or
                             "application/octet-stream")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        elif not getattr(self.server, "ready", True):
            self.send_response(303)
            self.send_header("Location", "/settings")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif endpoint == "/api/library":
            with self.server.mutation_lock:
                self._json(200, self._read_library())
        elif endpoint == "/api/library/cards":
            with self.server.mutation_lock:
                self._referencing_cards()
        elif endpoint == "/api/deps":
            with self.server.mutation_lock:
                self._json(200, self._dependencies())
        elif endpoint == "/api/deletions":
            with self.server.mutation_lock:
                self._deletions()
        elif endpoint == "/api/deletion-thumb":
            self._deletion_thumb()
        elif endpoint == "/api/render-previews":
            self._json(200, render_snapshot(self.server, self._cursor()))
        elif endpoint == "/api/missing-mods":
            self._missing_mods()
        elif endpoint == "/api/missing-mods/job":
            self._json(200, missing_snapshot(self.server, self._cursor()))
        else:
            with self.server.mutation_lock:
                super().do_GET()

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _cursor(self):
        """Only the render endpoint takes a query argument: how far the page read."""
        cursor = 0
        for part in urlparse(self.path).query.split("&"):
            if part.startswith("cursor=") and part[7:].isdigit():
                cursor = int(part[7:])
        return cursor

    def _query(self, name):
        """One query argument, decoded as form data.

        Paths carry spaces, so ``+`` has to become a space: a caller that
        builds the query with urlencode (or a browser that does the same) would
        otherwise send a package path nothing can match.
        """
        prefix = name + "="
        for part in urlparse(self.path).query.split("&"):
            if part.startswith(prefix):
                return unquote_plus(part[len(prefix):])
        return ""

    def _json(self, status, payload):
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        if (self.headers.get("Host") != self.server.authority or
            self.headers.get("Origin") != "http://" + self.server.authority or
            self.headers.get("X-Catalog-Token") != self.server.token):
            self._json(403, {"error": "request not authorized"})
            return
        endpoint = urlparse(self.path).path
        if endpoint not in {"/api/delete", "/api/restore", "/api/purge",
                            "/api/mod-classification",
                            "/api/render-previews", "/api/render-previews/cancel",
                            "/api/missing-mods/job", "/api/missing-mods/job/cancel",
                            "/api/config", "/api/pick-folder", "/api/shutdown"}:
            self._json(404, {"error": "unknown endpoint"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length < 256 * 1024:
                raise ValueError("invalid request size")
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("request must be an object")
            if endpoint == "/api/pick-folder":
                self._pick_folder(data)
                return
            with self.server.mutation_lock:
                if self.headers.get("X-Catalog-Token") != self.server.token:
                    raise ValueError("目录配置已改变，请重新打开页面")
                if endpoint == "/api/shutdown":
                    self._json(200, {"ok": True})
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                elif endpoint == "/api/config":
                    self._configure(data)
                elif not getattr(self.server, "ready", True):
                    raise ValueError("请先完成目录配置")
                elif endpoint == "/api/restore":
                    self._restore(data)
                elif endpoint == "/api/purge":
                    self._purge(data)
                elif endpoint == "/api/mod-classification":
                    self._mod_classification(data)
                elif endpoint == "/api/render-previews":
                    self._start_render(data)
                elif endpoint == "/api/render-previews/cancel":
                    self._cancel_render()
                elif endpoint == "/api/missing-mods/job":
                    self._start_fetch(data)
                elif endpoint == "/api/missing-mods/job/cancel":
                    self._cancel_fetch()
                else:
                    self._delete(data)
        except Exception as exc:
            self._json(400, {"ok": False, "error": str(exc)})

    def _pick_folder(self, data):
        initial = data.get("initial", "")
        if not isinstance(initial, str) or len(initial) > 32767:
            raise ValueError("invalid initial folder")
        if not self.server.picker_lock.acquire(blocking=False):
            raise ValueError("已有文件夹选择窗口打开")
        try:
            with tempfile.TemporaryDirectory(prefix="kk-folder-") as temporary:
                output = Path(temporary) / "selection.json"
                command = ([sys.executable, "--pick-folder", initial, str(output)]
                           if getattr(sys, "frozen", False) else
                           [sys.executable, "-B", str(APP_ROOT / "FolderPicker.py"), initial])
                result = subprocess.run(
                    command, capture_output=True, text=True, encoding="utf-8",
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if result.returncode:
                    raise ValueError("文件夹选择窗口无法打开，请直接填写完整路径")
                raw = output.read_text("utf-8") if getattr(sys, "frozen", False) else result.stdout
                selection = json.loads(raw)
            self._json(200, selection)
        finally:
            self.server.picker_lock.release()

    def _configure(self, data):
        values = config.validate(data, self.server.catalog)
        self.server.ready = False
        apply_settings(self.server, values)
        try:
            initialize_catalog(self.server)
            config.save(values, self.server.config_file)
        except Exception as exc:
            self.server.config_error = "建立索引失败：" + str(exc)
            raise
        self.server.config_error = ""
        self.server.ready = True
        # Old tabs must reload before they can mutate the newly selected folders.
        self.server.token = secrets.token_urlsafe(32)
        self._json(200, {"ok": True})

    def _delete(self, data):
        """Disable one archive, or a whole selection, in a single index write.

        Rewriting the index costs about a second, so a batch of twenty packages
        has to be one write: looping per file would take twenty seconds and
        leave the index half updated if one of them failed.
        """
        requested = data["paths"] if "paths" in data else data.get("path")
        if isinstance(requested, str):
            requested = [requested]
        if not isinstance(requested, list) or not requested:
            raise ValueError("没有指定要停用的 Mod")
        if len(requested) > 500:
            raise ValueError("一次最多停用 500 个 Mod")
        mods_root = self.server.mods.resolve()
        records = self.server.read_records()
        by_path = {record["path"]: record for record in records}
        now = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        prepared, failures, seen = [], [], set()
        for raw in requested:
            relative = str(raw).replace("\\", "/")
            if relative in seen:
                continue
            seen.add(relative)
            try:
                if not relative or Path(relative).is_absolute() or relative.startswith("../"):
                    raise ValueError("invalid relative path")
                source = (self.server.mods / Path(relative)).resolve()
                if source == mods_root or mods_root not in source.parents:
                    raise ValueError("path outside mods directory")
                if source.suffix.lower() not in {".zip", ".zipmod"}:
                    raise ValueError("only zip and zipmod files can be removed")
                if not source.is_file():
                    raise FileNotFoundError(relative)
                if relative not in by_path:
                    raise ValueError("archive is not in current catalog")
                disabled = source.with_name(
                    source.name + ".modmanager-" + now + ".disabled")
                if disabled.exists():
                    raise ValueError("disabled destination already exists")
                prepared.append((relative, source, disabled))
            except Exception as exc:
                failures.append({"path": relative, "error": str(exc)})
        if not prepared:
            raise ValueError(failures[0]["error"] if failures else "没有可停用的文件")

        events = []
        for relative, source, disabled in prepared:
            source.rename(disabled)
            events.append(({
                "time": dt.datetime.now().isoformat(timespec="seconds"),
                "action": "delete",
                "id": secrets.token_hex(16),
                "source": relative,
                "disabled": disabled.relative_to(mods_root).as_posix(),
                "record": by_path[relative],
                "modsRoot": str(mods_root),
            }, source, disabled))
        log_size = self.server.log.stat().st_size if self.server.log.exists() else 0
        try:
            with self.server.log.open("a", encoding="utf-8") as log:
                for event, _, _ in events:
                    log.write(json.dumps(event, ensure_ascii=False) + "\n")
                log.flush()
                os.fsync(log.fileno())
        except Exception:
            for _, source, disabled in reversed(events):
                if disabled.exists() and not source.exists():
                    disabled.rename(source)
            try:
                with self.server.log.open("r+", encoding="utf-8") as log:
                    log.truncate(log_size)
            except OSError:
                pass
            raise
        removed = {relative for relative, _, _ in prepared}
        warning = None
        try:
            self.server.save_records([r for r in records if r["path"] not in removed])
        except Exception as exc:
            warning = "Mod 已停用，索引更新失败：" + str(exc)
        ids = [event["id"] for event, _, _ in events]
        self._json(200, {
            "ok": True,
            "id": ids[0],
            "ids": ids,
            "disabled": events[0][2].relative_to(mods_root).as_posix(),
            "disabledFiles": [event["disabled"] for event, _, _ in events],
            "count": len(events),
            "failures": failures,
            "warning": warning,
        })

    def _start_render(self, data):
        """Queue a thumbnail render job; the page polls /api/render-previews.

        Rendering one archive takes a second or two, so this cannot answer in
        the request itself: a thousand packages would sit on the socket for an
        hour. The job writes previews as it goes and rewrites the index once.
        """
        job = getattr(self.server, "render_job", None)
        if job and job["running"]:
            raise ValueError("已有渲染任务正在进行，请先取消")
        records = self.server.read_records()
        requested = data.get("paths")
        if requested is None:
            targets = [record["path"] for record in records if not record.get("images")]
        else:
            if not isinstance(requested, list) or not requested:
                raise ValueError("没有指定要渲染的 Mod")
            if len(requested) > 3000:
                raise ValueError("一次最多渲染 3000 个 Mod")
            known = {record["path"] for record in records}
            targets = []
            for raw in requested:
                relative = str(raw).replace("\\", "/")
                if relative in known and relative not in targets:
                    targets.append(relative)
            if not targets:
                raise ValueError("选中的 Mod 不在索引里")
        if not targets:
            raise ValueError("没有需要渲染缩略图的 Mod")
        job = {"running": True, "total": len(targets), "done": 0, "failed": 0,
               "current": "", "cancel": False, "updates": [], "error": ""}
        self.server.render_job = job
        threading.Thread(target=self._render_worker, args=(job, targets), daemon=True).start()
        self._json(200, {"ok": True, "total": len(targets)})

    def _cancel_render(self):
        job = getattr(self.server, "render_job", None)
        if not job or not job["running"]:
            raise ValueError("没有正在进行的渲染任务")
        job["cancel"] = True
        self._json(200, {"ok": True})

    def _render_worker(self, job, targets):
        server = self.server
        try:
            renderer = getattr(server, "preview_renderer", None)
            if renderer is None:
                renderer = load_module("preview_renderer", "PreviewRenderer.py")
                server.preview_renderer = renderer
            builder = server.builder
        except Exception as exc:
            job.update({"running": False, "error": "渲染模块不可用：" + str(exc)})
            return
        records = server.read_records()
        by_path = {record["path"]: record for record in records}
        icons = server.catalog / "thumbs"
        icons.mkdir(parents=True, exist_ok=True)
        pending = {}      # relative -> images, not yet written to the index
        saved = 0         # how many of them the index already has

        def flush():
            """Write the pictures collected so far, then keep going.

            A full run takes an hour or more, so the loop checkpoints instead of
            waiting for the end: closing the window must not throw away the work
            already done. The index is re-read first, because a delete that
            happened during the run must not be resurrected by a stale copy.
            """
            nonlocal saved
            if len(pending) == saved:
                return
            live = {record["path"]: record for record in server.read_records()}
            for path, images in pending.items():
                record = live.get(path)
                if record is not None:
                    record["images"] = images
            with server.mutation_lock:
                server.save_records(list(live.values()))
            saved = len(pending)

        for relative in targets:
            if job["cancel"]:
                break
            job["current"] = relative
            record = by_path.get(relative)
            try:
                image = renderer.render_package(server.mods / relative) if record else None
                if image is None:
                    job["failed"] += 1
                else:
                    key = builder.digest(f"{relative}|mesh-render|{record.get('bytes', 0)}")
                    # Always repaint: a package that was already drawn keeps its
                    # key, but the drawing itself changes when the renderer does.
                    stale = builder.preview_file(icons, key)
                    target = builder.write_preview(image, icons, key)
                    if stale is not None and stale != target:
                        try:
                            stale.unlink()
                        except OSError:
                            pass
                    pending[relative] = [{"src": "thumbs/" + target.name,
                                          "name": "模型渲染（来自包内网格）",
                                          "source": "mesh-render"}]
                    job["updates"].append({"path": relative, "images": pending[relative]})
            except Exception:
                job["failed"] += 1
            finally:
                job["done"] += 1
            if len(pending) - saved >= self.CHECKPOINT:
                try:
                    flush()
                except Exception as exc:
                    job["error"] = "缩略图已生成，索引更新失败：" + str(exc)
        job["current"] = ""
        try:
            flush()
        except Exception as exc:
            job["error"] = "缩略图已生成，索引更新失败：" + str(exc)
        job["running"] = False

    def _recovery(self):
        module = getattr(self.server, "recovery_module", None)
        if module is None:
            module = load_module("mod_recovery", "ModRecovery.py")
            self.server.recovery_module = module
        return module

    def _missing_mods(self):
        """GUIDs that cards ask for and no installed package provides."""
        with self.server.mutation_lock:
            rows = self._recovery().missing_from(self.server.read_records(),
                                                 self._read_library())
        cached = (self.server.catalog / "missing-index.json").is_file()
        self._json(200, {"total": len(rows), "guids": rows, "indexed": cached})

    def _start_fetch(self, data):
        """Queue a search-and-download job for the missing packages.

        Resolving one GUID means reading the manifest of every archive the
        public index lists, which cannot happen inside a request. The job
        reports its stage; the page polls /api/missing-mods/job.
        """
        job = getattr(self.server, "fetch_job", None)
        if job and job["running"]:
            raise ValueError("已有下载任务正在进行，请先停止")
        rows = self._recovery().missing_from(self.server.read_records(),
                                             self._read_library())
        requested = data.get("guids")
        if requested is not None:
            if not isinstance(requested, list) or not requested:
                raise ValueError("没有指定要查找的 GUID")
            if len(requested) > 3000:
                raise ValueError("一次最多查找 3000 个 GUID")
            wanted = {str(guid).casefold() for guid in requested}
            rows = [row for row in rows if row["guid"].casefold() in wanted]
            if not rows:
                raise ValueError("这些 GUID 已经不在缺失列表里")
        if not rows:
            raise ValueError("没有缺失的 Mod")
        flavour = self._game_flavour()
        job = {"running": True, "stage": "索引", "message": "正在读取公共索引…",
               "requested": len(rows), "total": len(rows), "done": 0, "failed": 0,
               "matched": 0, "current": "", "cancel": False, "updates": [],
               "error": "", "problems": [], "unmatched": [], "flavour": flavour,
               "scanned": 0, "unreadable": 0, "fallbacks": [],
               "packs": [], "unreadPacks": []}
        self.server.fetch_job = job
        threading.Thread(target=self._fetch_worker, args=(job, rows, flavour),
                         daemon=True).start()
        self._json(200, {"ok": True, "total": len(rows), "flavour": flavour})

    def _game_flavour(self):
        """Whether the configured folder is Koikatsu or Sunshine, once.

        The two games ship the same GUIDs in different builds, so this only
        decides which copy is preferred. It used to decide which index was
        crawled as well, and a wrong answer there looked like "the download
        finds nothing"; now the evidence is logged so a report carries it.
        """
        cached = getattr(self.server, "game_flavour", None)
        if cached:
            return cached
        evidence = {}
        try:
            evidence = self._recovery().flavour_evidence(
                (getattr(self.server, "settings", None) or {}).get("game"))
            flavour = evidence.get("flavour") or "kk"
        except Exception:
            flavour = "kk"
        self.server.game_flavour = flavour
        found = evidence.get("found") or {}
        detail = "；".join(f"{name} 依据 {hit['path']}" for name, hit in found.items())
        print(f"[missing-mods] 目标游戏 {flavour.upper()}："
              f"{detail or '没有找到游戏标记，按 KK 处理'}"
              + ("（两个游戏的标记都在，按 KK 处理）" if evidence.get("ambiguous") else ""),
              flush=True)
        return flavour

    def _cancel_fetch(self):
        job = getattr(self.server, "fetch_job", None)
        if not job or not job["running"]:
            raise ValueError("没有正在进行的下载任务")
        job["cancel"] = True
        self._json(200, {"ok": True})

    def _fetch_worker(self, job, rows, flavour="kk"):
        server = self.server

        def trace(message):
            # The page only ever sees job["error"], and a user's manager.log is
            # the one artifact a report can carry back. Without this line every
            # "download failed" report is undiagnosable: a network refusal and a
            # parser bug both read as "it did not work".
            try:
                print("[missing-mods] " + message, flush=True)
            except Exception:
                pass

        try:
            recovery = self._recovery()
            builder = server.builder
        except Exception as exc:
            job.update({"running": False, "stage": "失败",
                        "error": "下载模块不可用：" + str(exc)})
            trace("下载模块不可用：\n" + traceback.format_exc())
            return

        trace(f"开始查找 {len(rows)} 个 GUID（目标游戏 {flavour.upper()}）")

        def say(message, stage=None):
            job["message"] = message
            if stage:
                job["stage"] = stage

        def cancelled():
            return job["cancel"]

        try:
            say("正在读取公共索引…", "索引")
            crawl_report = {}
            files = recovery.crawl(server.catalog, progress=say, cancel=cancelled,
                                   flavour=flavour, report=crawl_report)
            # Which packs the index actually covers is the difference between
            # "this mod is not public" and "one pack folder failed to list",
            # and the second one used to be invisible.
            job["packs"] = crawl_report.get("packs") or []
            job["unreadPacks"] = crawl_report.get("unreadPacks") or []
            if job["unreadPacks"]:
                trace("整合包目录读取失败：" + "；".join(job["unreadPacks"]))
            say(f"索引里共 {len(files)} 个归档（{len(job['packs'])} 个整合包），"
                "开始读取包内清单", "匹配")
            coverage = {}
            found = recovery.resolve(rows, files, server.catalog,
                                     progress=say, cancel=cancelled,
                                     report=coverage, flavour=flavour)
            chosen = [found[key] for key in sorted(found)]
            job["matched"] = len(chosen)
            job["total"] = len(chosen)
            # "Not in the pack" has two causes that look alike from here:
            # nothing shipped, or the archive's manifest could not be read.
            # Carry the coverage so the page can state which one applies
            # instead of claiming the pack lacks it.
            job["scanned"] = coverage.get("scanned", 0)
            job["unreadable"] = len(coverage.get("unreadable") or [])
            # Answered, but only by the build the other game declares. It is
            # downloaded anyway -- the user asked for that archive, and a shared
            # mods folder or a second install is a real reason to want it -- and
            # the job says so, because the game itself may skip it on load.
            job["fallbacks"] = coverage.get("fallbacks") or []
            # A GUID the public pack does not ship is the common case, not an
            # error, and a bare "installed 0" reads like a failure. Keep the
            # ones that went unanswered -- with the author and the page the card
            # recorded -- so the page can say what is still missing and where to
            # look for it.
            job["unmatched"] = [
                {"guid": row["guid"], "authors": row.get("authors") or [],
                 "websites": row.get("websites") or [],
                 "cards": len(row.get("cards") or []), "reason": "absent"}
                for row in rows if row["guid"].casefold() not in found][:500]
            if not chosen:
                job["message"] = "公共索引里没有找到这些 Mod"
            else:
                # Which game a manifest declares is the game's business; the
                # progress line only has to say that work has started.
                job["message"] = f"找到 {len(chosen)} 个，开始下载"
            installed = []
            for row in chosen:
                if cancelled():
                    break
                label = row.get("name") or row["guid"]
                job["current"] = label
                try:
                    size = row.get("bytes") or 0
                    if size:
                        free = shutil.disk_usage(server.mods).free
                        if free < size + DISK_MARGIN:
                            raise ValueError(
                                f"磁盘空间不足：这个包 {size / 1048576:.0f} MB，"
                                f"加上余量需要 {(size + DISK_MARGIN) / 1048576:.0f} MB，"
                                f"当前只剩 {free / 1048576:.0f} MB")
                    destination = recovery.target_for(row, server.mods)
                    recovery.download(
                        row, destination, cancel=cancelled,
                        progress=lambda received, total, name=label: job.update(
                            message=download_progress(name, received, total)))
                    record = builder.scan_one(destination, server.mods, server.catalog)
                    installed.append(record)
                    job["updates"].append({
                        "guid": row["guid"], "file": destination.name,
                        "path": record["path"], "bytes": record["bytes"]})
                except KeyboardInterrupt:
                    break
                except Exception as exc:
                    job["failed"] += 1
                    job["problems"].append(f"{row['guid']}：{exc}")
                    trace(f"{row['guid']} 下载失败：{exc!r}")
                finally:
                    job["done"] += 1
            if installed:
                say("正在重建索引…", "重建")
                with server.mutation_lock:
                    records = list(server.read_records())
                    known = {record["path"] for record in records}
                    records.extend(record for record in installed
                                   if record["path"] not in known)
                    server.save_records(records)
        except Exception as exc:
            job["error"] = str(exc)
            trace("任务失败（"
                  f"阶段 {job['stage']}，请求 {job.get('requested')} 个 GUID）：\n"
                  + traceback.format_exc())
        finally:
            job["current"] = ""
            job["running"] = False
            job["stage"] = "失败" if job["error"] else "完成"
            trace(f"任务结束：匹配 {job.get('matched')}，安装 {len(job.get('updates') or [])}，"
                  f"失败 {job.get('failed')}，未找到 {len(job.get('unmatched') or [])}"
                  + (f"，错误：{job['error']}" if job["error"] else ""))

    def _log_entries(self):
        """The delete log, parsed once per write it receives.

        Listing the deletions and then asking for one thumbnail per row would
        otherwise re-read and re-parse the whole file a hundred times per screen.
        """
        path = self.server.log
        try:
            stat = path.stat()
        except OSError:
            return []
        stamp = (stat.st_mtime_ns, stat.st_size)
        cached = getattr(self.server, "log_cache", None)
        if cached is not None and cached[0] == stamp:
            return cached[1]
        entries = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entries.append(json.loads(line))
            except ValueError:
                continue
        self.server.log_cache = (stamp, entries)
        return entries

    def _deletions(self):
        """List every deletion that may still be undone.

        A delete renames the archive in place to
        "<name>.modmanager-<stamp>.disabled", so undoing one needs only the log
        entry plus that file. The page's undo button remembers just the batches
        of this browser session; the log is the durable list, and this endpoint
        is what makes it reachable after the tab is closed.
        """
        entries = self._log_entries()
        undone = {entry.get("id") for entry in entries
                  if entry.get("action") == "restore"}
        root = self.server.mods.resolve()
        mods_now = str(self.server.mods)
        rows = []
        for entry in entries:
            if entry.get("action") != "delete" or not entry.get("id"):
                continue
            record = entry.get("record") or {}
            row = {
                "id": entry["id"],
                "time": entry.get("time", ""),
                "source": entry.get("source", ""),
                "file": record.get("file") or Path(entry.get("source", "")).name,
                "bytes": record.get("bytes") or 0,
                "name": record.get("name", ""),
                "author": record.get("author", ""),
                "guid": record.get("guid", ""),
                "restored": entry["id"] in undone,
                # A restore is refused for these three, so the page can say why
                # instead of offering a button that only returns an error.
                "otherRoot": entry.get("modsRoot", mods_now) != mods_now,
                "conflict": False,
                "available": False,
            }
            try:
                # A record without a "disabled" path predates the in-place
                # rename; its file cannot be located any more, so the row stays
                # listed but unavailable.
                source = confined(root, entry["disabled"])
                row["available"] = source.is_file()
                row["conflict"] = (root / entry["source"]).exists()
            except (ValueError, OSError, KeyError):
                pass
            rows.append(row)
        rows.sort(key=lambda item: item["time"], reverse=True)
        self._json(200, {
            "items": rows,
            "pending": sum(1 for row in rows
                           if row["available"] and not row["restored"]
                           and not row["otherRoot"] and not row["conflict"]),
            "modsRoot": mods_now,
        })

    def _deletion_thumb(self):
        """Serve the cover picture of one deleted archive.

        The picture usually already sits in catalog/thumbs: preview keys are
        derived from the archive's relative path and its entries, and disabling
        a mod changes neither. A record rebuilt from file names alone carries no
        images, so the disabled archive is scanned here once - with its original
        path, so the keys match the ones the last index build wrote - and the
        answer is remembered for the rest of the run.
        """
        event_id = self._query("id")
        entry = next((item for item in self._log_entries()
                      if item.get("id") == event_id
                      and item.get("action") == "delete"), None)
        if entry is None:
            self.send_error(404)
            return
        cache = getattr(self.server, "trash_thumbs", None)
        if cache is None:
            cache = self.server.trash_thumbs = {}
        if event_id not in cache:
            cache[event_id] = self._deleted_preview(entry)
        relative = cache[event_id]
        if not relative:
            self.send_error(404)
            return
        try:
            raw = (self.server.catalog / relative).read_bytes()
        except OSError:
            cache[event_id] = None
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(relative)[0] or
                         "application/octet-stream")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _deleted_preview(self, entry):
        """Return the catalog-relative preview of a deletion, or None."""
        found = self._first_image(entry.get("record") or {})
        if found:
            return found
        try:
            root = self.server.mods.resolve()
            source = confined(root, entry["disabled"])
            if not source.is_file():
                return None
            # One archive at a time: decoding a thumbnail bundle is CPU work and
            # a whole screen of rows would otherwise start at the same moment.
            lock = getattr(self.server, "thumb_lock", None)
            if lock is None:
                lock = self.server.thumb_lock = threading.Lock()
            with lock:
                record = self.server.builder.scan_one(
                    source, root, self.server.catalog, relative=entry["source"])
        except Exception:
            return None
        return self._first_image(record)

    def _first_image(self, record):
        for image in record.get("images") or []:
            source = str(image.get("src") or "")
            if source.startswith("thumbs/") and (self.server.catalog / source).is_file():
                return source
        return None

    def _purge(self, data):
        """Erase the disabled archives of a selection, then forget the records.

        This is the only endpoint that removes a mod file for good, and it only
        ever unlinks a ".disabled" file whose name matches its own log entry. A
        restored record has no such file - the archive is back in place - so
        purging it merely drops the entry and never touches live content.
        """
        requested = data["ids"] if "ids" in data else data.get("id")
        if isinstance(requested, str):
            requested = [requested]
        if not isinstance(requested, list) or not requested:
            raise ValueError("没有指定要彻底删除的记录")
        if len(requested) > 500:
            raise ValueError("一次最多彻底删除 500 条记录")
        entries = self._log_entries()
        deletions = {entry["id"]: entry for entry in entries
                     if entry.get("action") == "delete" and "id" in entry}
        root = self.server.mods.resolve()
        mods_now = str(self.server.mods)
        purged, failures, taken = [], [], set()
        for event_id in requested:
            if not isinstance(event_id, str):
                failures.append({"id": str(event_id), "error": "invalid id"})
                continue
            event = deletions.get(event_id)
            if event is None or event_id in taken:
                failures.append({"id": event_id, "error": "这条记录已经不在删除日志里"})
                continue
            taken.add(event_id)
            try:
                if event.get("modsRoot", mods_now) != mods_now:
                    raise ValueError("请切回删除该 Mod 时使用的目录后再彻底删除")
                disabled = event.get("disabled")
                if disabled:
                    target = confined(root, event["source"])
                    source = confined(root, disabled)
                    if (source.parent != target.parent or
                            not source.name.startswith(target.name + ".modmanager-")
                            or source.suffix != ".disabled"):
                        raise ValueError("invalid disabled path")
                    if source.exists():
                        source.unlink()
            except Exception as exc:
                failures.append({"id": event_id, "error": str(exc)})
                continue
            purged.append(event_id)
        if not purged and failures:
            raise ValueError(failures[0]["error"])
        remaining = entries
        if purged:
            drop = set(purged)
            remaining = [entry for entry in entries if entry.get("id") not in drop]
            path = self.server.log
            temporary = path.with_name(path.name + ".tmp")
            with temporary.open("w", encoding="utf-8") as handle:
                for entry in remaining:
                    handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            thumbs = getattr(self.server, "trash_thumbs", None)
            if thumbs is not None:
                for event_id in purged:
                    thumbs.pop(event_id, None)
        self._json(200, {"ok": True, "purged": len(purged),
                         "failures": failures, "remaining": len(remaining)})

    def _restore(self, data):
        """Restore one deletion, or a whole batch, in a single index write."""
        requested = data["ids"] if "ids" in data else data.get("id")
        if isinstance(requested, str):
            requested = [requested]
        if not isinstance(requested, list) or not requested:
            raise ValueError("没有指定要恢复的删除记录")
        if len(requested) > 500:
            raise ValueError("一次最多恢复 500 个 Mod")
        entries = [json.loads(line) for line in self.server.log.read_text(
            encoding="utf-8").splitlines() if line]
        deletions = {entry["id"]: entry for entry in entries
                     if entry.get("action") == "delete" and "id" in entry}
        restored = {entry.get("id") for entry in entries if entry.get("action") == "restore"}
        root = self.server.mods.resolve()
        prepared, failures, taken = [], [], set()
        for event_id in requested:
            try:
                event = deletions.get(event_id)
                if event is None:
                    raise ValueError("no pending deletion found")
                if event_id in restored or event_id in taken:
                    raise ValueError("这条删除记录已经恢复过")
                if event.get("modsRoot", config.defaults()["mods"]) != str(self.server.mods):
                    raise ValueError("请切回删除该 Mod 时使用的目录后再恢复")
                target = confined(root, event["source"])
                if "disabled" not in event:
                    raise ValueError("这条记录来自旧版本，原文件位置已无法确定")
                source = confined(root, event["disabled"])
                if (source.parent != target.parent or not source.name.startswith(
                        target.name + ".modmanager-") or source.suffix != ".disabled"):
                    raise ValueError("invalid disabled path")
                if target.exists():
                    raise ValueError("restore destination already exists")
                if not source.exists():
                    raise FileNotFoundError("原文件已不在")
                taken.add(event_id)
                prepared.append((event, source, target))
            except Exception as exc:
                failures.append({"id": event_id, "error": str(exc)})
        if not prepared:
            raise ValueError(failures[0]["error"] if failures else "没有可恢复的记录")

        moved = []
        try:
            for event, source, target in prepared:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(target))
                moved.append((source, target))
        except Exception:
            for source, target in reversed(moved):
                if target.exists() and not source.exists():
                    shutil.move(str(target), str(source))
            raise
        log_size = self.server.log.stat().st_size if self.server.log.exists() else 0
        try:
            with self.server.log.open("a", encoding="utf-8") as log:
                for event, _, _ in prepared:
                    log.write(json.dumps({"action": "restore", "id": event["id"],
                                          "time": dt.datetime.now().isoformat()}) + "\n")
                log.flush()
                os.fsync(log.fileno())
        except Exception:
            for source, target in reversed(moved):
                if target.exists() and not source.exists():
                    shutil.move(str(target), str(source))
            try:
                with self.server.log.open("r+", encoding="utf-8") as log:
                    log.truncate(log_size)
            except OSError:
                pass
            raise
        back = {event["source"]: event["record"] for event, _, _ in prepared}
        records = [r for r in self.server.read_records() if r["path"] not in back]
        records.extend(back.values())
        warning = None
        try:
            self.server.save_records(records)
        except Exception as exc:
            warning = "File restored; catalog sync failed: " + str(exc)
        self._json(200, {
            "ok": True,
            "id": prepared[0][0]["id"],
            "record": prepared[0][0]["record"],
            "records": [event["record"] for event, _, _ in prepared],
            "restored": [event["source"] for event, _, _ in prepared],
            "count": len(prepared),
            "failures": failures,
            "warning": warning,
        })

    def _read_library(self):
        reader = getattr(self.server, "read_library", None)
        if reader is not None:
            return reader()
        path = self.server.catalog / "library.json"
        return json.loads(path.read_text("utf-8")) if path.exists() else []

    def _dependencies(self):
        # Recomputed from the current index and library, so a delete or a move
        # shows up immediately instead of leaving a stale "installed" verdict.
        cached = getattr(self.server, "deps_cache", None)
        if cached is None:
            records = self.server.read_records() if hasattr(self.server, "read_records") else []
            cached = library_builder.dependencies(records, self._read_library())
            self.server.deps_cache = cached
        return cached

    def _referencing_cards(self):
        """The cards behind a "referenced by N" line on a Mod card.

        The dependency data already says which paths name a package; the library
        holds the thumbnails that make those paths recognisable. Only the fields
        the list draws are sent back -- a thousand card records with their full
        reference lists would be larger than the answer is worth.
        """
        mod = self._query("mod")
        kind = self._query("kind") or "cards"
        if not mod:
            self._json(400, {"error": "缺少 mod 参数"})
            return
        if kind not in {"cards", "outfits", "scenes"}:
            self._json(400, {"error": "未知的资源类型"})
            return
        entry = (self._dependencies().get("mods") or {}).get(mod) or {}
        paths = entry.get(kind) or []
        if not paths:
            self._json(200, {"total": 0, "cards": []})
            return
        wanted = set(paths)
        rows = [{"path": row.get("path"), "file": row.get("file"),
                 "kind": row.get("kind"), "thumb": row.get("thumb")}
                for row in self._read_library() if row.get("path") in wanted]
        rows.sort(key=lambda row: row.get("path") or "")
        self._json(200, {"total": len(paths), "cards": rows})

    def _mod_classification(self, data):
        relative, classification = data.get("path"), data.get("classification")
        keys = {"work", "character", "author"}
        if (not isinstance(classification, dict) or set(classification) != keys or
                any(not isinstance(value, str) or len(value) > 120 or
                    any(ord(char) < 32 for char in value)
                    for value in classification.values())):
            raise ValueError("作品、角色和作者必须为不超过 120 字的文本")
        records = self.server.read_records()
        record = next((r for r in records if r["path"] == relative), None)
        if record is None:
            raise ValueError("Mod 不在当前索引中")
        classification = {key: value.strip() for key, value in classification.items()}
        path = self.server.catalog / "mod-classifications.json"
        annotations = json.loads(path.read_text("utf-8")) if path.exists() else {}
        annotations[str(self.server.mods.resolve()) + "|" + relative] = classification
        library_builder.write_json(path, annotations)
        record["classification"] = classification
        self.server.save_records(records)
        self._json(200, {"ok": True, "classification": classification})

def apply_settings(server, values):
    server.settings = values
    server.mods = Path(values["mods"])
    server.userdata = Path(values["userdata"] or Path(values["game"]) / "UserData")
    server.resource_roots = {key: Path(values[key]) if values[key] else None
                             for key in config.RESOURCE_KEYS}
    # Holds delete-log.jsonl only; disabled archives stay next to the original.
    server.records = Path(values["modtrash"])
    server.records.mkdir(parents=True, exist_ok=True)
    server.log = server.records / "delete-log.jsonl"


def attach_indexes(server):
    """Keep one parsed copy of each large index per process.

    index.json and library.json are 24 MB and 38 MB of JSON, and every single
    mutation used to re-parse both from disk before touching them. Every writer
    replaces the cached value, and the only other writer is a full index rebuild,
    which calls this again.
    """
    cached = {"records": None, "library": None}

    def read_records():
        if cached["records"] is None:
            cached["records"] = json.loads(
                (server.catalog / "index.json").read_text(encoding="utf-8"))
        return cached["records"]

    def read_library():
        if cached["library"] is None:
            path = server.catalog / "library.json"
            cached["library"] = (json.loads(path.read_text("utf-8"))
                                 if path.exists() else [])
        return cached["library"]

    server.read_records = read_records
    server.read_library = read_library
    return cached


def initialize_catalog(server):
    """Reconcile files and dependency status once, before serving requests."""
    def progress(phase, current, total):
        label = {"mods": "Mod", "library": "卡片与场景"}[phase]
        message = f"正在扫描{label}：{current:,} / {total:,}"
        server.progress = {"phase": phase, "current": current, "total": total,
                           "message": message}
        callback = getattr(server, "on_progress", None)
        if callback:
            callback(message)

    builder = load_module("catalog_builder", "Build-ModCatalog.py")
    server.builder = builder
    server.catalog.mkdir(parents=True, exist_ok=True)
    (server.catalog / "thumbs").mkdir(exist_ok=True)
    cached = attach_indexes(server)

    def save_records(records):
        server.deps_cache = None
        classification_file = server.catalog / "mod-classifications.json"
        annotations = (json.loads(classification_file.read_text("utf-8"))
                       if classification_file.exists() else {})
        for record in records:
            classification = annotations.get(str(server.mods.resolve()) + "|" + record["path"])
            if classification is None:
                record.pop("classification", None)
            else:
                record["classification"] = classification
        builder.save(records, server.catalog,
                     server.catalog.parent / "zipmod_inventory._mods.csv", 0)
        # Keep the file:// fallback in step with the live verdicts, and hand the
        # result to the next /api/deps instead of letting it recompute.
        deps = library_builder.dependencies(records, server.read_library())
        library_builder.save_dependencies(deps, server.catalog)
        server.deps_cache = deps
        cached["records"] = records
        shutil.copyfile(server.catalog / "items.csv",
                        server.catalog.parent / "zipmod_inventory.csv")
        shutil.copyfile(server.catalog / "packages.csv",
                        server.catalog.parent / "zipmod_inventory._mods.csv")

    server.save_records = save_records
    source_file = server.catalog / "sources.json"
    sources = {"mods": str(server.mods), "userdata": str(server.userdata),
               "resources": {key: str(value) if value else "" for key, value in
                             getattr(server, "resource_roots", {}).items()}}
    # Existing catalogs from before directory configuration used these defaults.
    legacy = config.defaults()
    previous = json.loads(source_file.read_text("utf-8")) if source_file.exists() else {
        "mods": legacy["mods"], "userdata": legacy["userdata"],
        "resources": {key: legacy[key] for key in config.RESOURCE_KEYS}}
    records = builder.sync_records(server.mods, server.catalog,
                                   reuse=previous.get("mods") == sources["mods"],
                                   progress=lambda current, total: progress("mods", current, total),
                                   game=Path(getattr(server, "settings", {}).get(
                                       "game", server.mods.parent)))
    server.save_records(records)
    reuse_library = (previous.get("userdata") == sources["userdata"] and
                     previous.get("resources") == sources["resources"])
    library_builder.build(server.userdata, server.catalog, records,
                          resource_roots=getattr(server, "resource_roots", None),
                          game=Path(getattr(server, "settings", {}).get(
                              "game", server.userdata.parent)),
                          reuse=reuse_library,
                          progress=lambda current, total: progress("library", current, total))
    # The rebuild just replaced library.json and deps.* on disk; re-read it once
    # now so the first delete or restore does not pay for the 38 MB parse.
    cached["library"] = None
    server.deps_cache = None
    server.read_records()
    server.read_library()
    library_builder.write_json(source_file, sources)
    server.progress = {"message": "索引已完成", "phase": "complete"}


def main(argv=None, on_started=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--mods", type=Path)
    parser.add_argument("--userdata", type=Path)
    parser.add_argument("--chara", type=Path)
    parser.add_argument("--coordinate", type=Path)
    parser.add_argument("--studio", type=Path)
    parser.add_argument("--config", type=Path, default=config.CONFIG_FILE)
    parser.add_argument("--catalog", type=Path,
                        default=DATA_ROOT / "catalog")
    # Kept under the historical name because config.json still stores it as
    # "modtrash"; the folder itself holds delete-log.jsonl, not archives.
    parser.add_argument("--records", "--quarantine", dest="records", type=Path)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args(argv)
    args.catalog = args.catalog.resolve()
    config_error = ""
    try:
        values = config.read(args.config)
    except (OSError, ValueError) as exc:
        values = config.defaults()
        config_error = "无法读取目录配置：" + str(exc)
    if args.userdata:
        values["userdata"] = str(args.userdata.resolve())
        for key in config.RESOURCE_KEYS:
            values[key] = str(args.userdata.resolve() / key)
    for key in ("mods", "chara", "coordinate", "studio"):
        if getattr(args, key):
            values[key] = str(getattr(args, key).resolve())
    if args.records:
        values["modtrash"] = str(args.records.resolve())
    handler = partial(CatalogHandler, directory=str(args.catalog))
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    except OSError:
        if args.open:
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{args.port}/api/session", timeout=2
                ) as response:
                    if response.headers.get("Server", "").startswith(CatalogHandler.server_version):
                        webbrowser.open(f"http://127.0.0.1:{args.port}/gallery.html")
                        return
            except Exception:
                pass
        # Leave other listeners alone and use an available local port.
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        args.port = server.server_port
    server.catalog = args.catalog
    server.config_file = args.config.resolve()
    server.settings = values
    server.config_error = config_error
    server.ready = False
    server.authority = f"127.0.0.1:{server.server_port}"
    server.token = secrets.token_urlsafe(32)
    server.instance_key = str(server.config_file)
    server.mutation_lock = threading.Lock()
    server.picker_lock = threading.Lock()
    server.deps_cache = None
    def initialize():
        explicit = any((args.mods, args.userdata, args.chara, args.coordinate, args.studio))
        if (args.config.exists() or explicit) and not config_error:
            try:
                validated = config.validate(values, args.catalog)
                apply_settings(server, validated)
                print("Loading startup catalog and card dependencies...", flush=True)
                initialize_catalog(server)
                server.ready = True
            except Exception as exc:
                server.config_error = str(exc)
        return "gallery.html" if server.ready else "settings"

    server.starting = True
    def startup():
        try:
            with server.mutation_lock:
                initialize()
        except Exception as exc:
            server.config_error = "启动失败：" + str(exc)
        finally:
            server.starting = False
    threading.Thread(target=startup, name="modmanager-index", daemon=True).start()
    if on_started:
        on_started(server)
    print(f"Catalog: http://{server.authority}/startup", flush=True)
    if args.open:
        open_url(f"http://{server.authority}/startup")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        with server.mutation_lock:
            pass
        server.server_close()


if __name__ == "__main__":
    main()
