"""Exercise the shipped executable against disposable game data."""
import argparse
import io
import json
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import msgpack
from PIL import Image


class TestRoot:
    """Disposable copy of the shipped program.

    ``TemporaryDirectory`` deletes the whole tree on exit, which is several
    hundred files in one call; file-safety guards can refuse that and take the
    process down with it. ``--keep`` skips the delete entirely so a failing run
    stays inspectable, and the default delete is best effort.
    """

    def __init__(self, parent, keep):
        self.parent = parent
        self.keep = keep

    def __enter__(self):
        self.path = tempfile.mkdtemp(prefix="kk-exe-test-", dir=self.parent)
        return self.path

    def __exit__(self, *exc_info):
        if self.keep:
            print("Test root kept:", self.path, flush=True)
        else:
            shutil.rmtree(self.path, ignore_errors=True)
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("exe", type=Path)
    parser.add_argument("--test-root", type=Path)
    parser.add_argument("--keep", action="store_true",
                        help="Leave the disposable program copy in place for inspection")
    parser.add_argument("--open-browser", action="store_true",
                        help="Verify real browser requests on first and repeat launch")
    parser.add_argument("--thumbnail-sample", type=Path,
                        help="Use a local mod's small thumbnail bundle to test packaged decoding")
    parser.add_argument("--base-list-sample", type=Path,
                        help="Verify packaged base-list resolution with a local game table")
    parser.add_argument("--render-sample", type=Path,
                        help="Copy a real mod in so the packaged renderer has meshes to draw")
    args = parser.parse_args()
    with TestRoot(args.test_root, args.keep) as temporary:
        root = Path(temporary)
        app = root / "便携程序"
        app.mkdir()
        exe = app / args.exe.name
        shutil.copyfile(args.exe, exe)
        # Match release metadata even when the disposable test folder is low-integrity.
        subprocess.run(["icacls.exe", str(exe), "/setintegritylevel", "M"],
                       check=True, capture_output=True)
        internal = args.exe.parent / "_internal"
        if internal.is_dir():
            shutil.copytree(internal, app / "_internal")
        process = None
        base = ""
        token = ""
        first_start = True

        def browser_requests():
            log = app / "runtime" / "manager.log"
            return log.read_text("utf-8").count('"GET /startup HTTP/1.1" 200')

        def wait_for_browser(previous):
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if browser_requests() > previous:
                    return
                time.sleep(0.15)
            raise AssertionError("EXE did not open the page in the real browser")

        def get(endpoint):
            with urllib.request.urlopen(base + endpoint, timeout=15) as response:
                return response.read()

        def post(endpoint, data, authorized=True):
            headers = {"Content-Type": "application/json", "Origin": base}
            if authorized:
                headers["X-Catalog-Token"] = token
            request = urllib.request.Request(
                base + endpoint, json.dumps(data).encode(), headers, method="POST")
            with urllib.request.urlopen(request, timeout=45) as response:
                return json.load(response)

        def start():
            nonlocal process, base, token, first_start
            open_browser = args.open_browser and first_start
            first_start = False
            process = subprocess.Popen([str(exe)] + ([] if open_browser else ["--no-open"]), cwd=app,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
            state_file = app / "runtime" / "server.json"
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise AssertionError("EXE exited before serving")
                try:
                    state = json.loads(state_file.read_text("utf-8"))
                    base = f'http://127.0.0.1:{state["port"]}'
                    token = json.loads(get("/api/session"))["token"]
                    config = json.loads(get("/api/config"))
                    if not config["starting"]:
                        shortcut = (app / "runtime" / "Open-Manager.url").read_text("utf-8")
                        assert base + "/startup" in shortcut
                        if open_browser:
                            wait_for_browser(0)
                        return config
                except (OSError, ValueError):
                    pass
                time.sleep(0.15)
            raise AssertionError("EXE startup timed out")

        def stop():
            post("/api/shutdown", {})
            process.wait(timeout=20)
            assert process.returncode == 0
            assert not (app / "runtime" / "server.json").exists()
            assert not (app / "runtime" / "Open-Manager.url").exists()

        try:
            assert not start()["ready"]
            setup = get("/settings").decode("utf-8")
            assert "保存并建立索引" in setup
            # The delete log is shown read-only: moving it would orphan history.
            # The archive folder beside it is editable, because the files really
            # live there and its own log travels with it.
            assert "删除与归档" in setup and "卡片回收" not in setup
            assert 'id="modtrash" type="text" readonly' in setup
            assert 'id="archive" type="text"' in setup
            assert "mods_archive" in get("/settings-ui.js").decode("utf-8")
            assert "UserData_trash" not in get("/settings-ui.js").decode("utf-8")
            assert b"startupStatus" in get("/startup")
            assert b"/api/shutdown" in get("/browser-ui.js")
            assert get("/assets/app-mark.png").startswith(b"\x89PNG")
            try:
                post("/api/shutdown", {}, authorized=False)
                raise AssertionError("Unauthenticated shutdown accepted")
            except urllib.error.HTTPError as error:
                assert error.code == 403
            stop()
            assert not start()["ready"]

            game = root / "测试游戏"
            mods = game / "mods"
            cards = game / "UserData" / "chara"
            mods.mkdir(parents=True)
            cards.mkdir(parents=True)
            scenes = game / "UserData" / "studio"
            outfits = game / "UserData" / "coordinate"
            scenes.mkdir()
            outfits.mkdir()
            image = io.BytesIO()
            Image.new("RGB", (64, 64), "#39b9c8").save(image, "PNG")
            with zipfile.ZipFile(mods / "sample.zipmod", "w") as archive:
                archive.writestr("manifest.xml", "<manifest><guid>sample</guid>"
                                 "<name>Sample</name></manifest>")
                archive.writestr("preview.png", image.getvalue())
                if args.thumbnail_sample:
                    import UnityPy
                    with zipfile.ZipFile(args.thumbnail_sample) as source:
                        entry = next(info for info in source.infolist()
                                     if "/thumb/" in info.filename.lower() and
                                     info.filename.lower().endswith(".unity3d"))
                        data = source.read(entry)
                    environment = UnityPy.load(data)
                    texture_name = next(obj.peek_name() for obj in environment.objects
                                        if obj.type.name == "Texture2D")
                    archive.writestr("abdata/chara/model.unity3d", data)
                    archive.writestr("abdata/list/characustom/top.csv",
                                     f"105\nID,Name,ThumbAB,ThumbTex\n"
                                     f"1,Top,chara/model.unity3d,{texture_name}\n")
            # A second package so the batch endpoint runs on a real selection.
            with zipfile.ZipFile(mods / "batch.zipmod", "w") as other:
                other.writestr("manifest.xml",
                               "<manifest><guid>batch</guid><name>Batch</name></manifest>")
            # A real archive, so the renderer has meshes and textures to read.
            if args.render_sample:
                shutil.copyfile(args.render_sample, mods / "render.zipmod")
            base_reference = b""
            if args.base_list_sample:
                import UnityPy
                target = game / "abdata/list/characustom/00.unity3d"
                target.parent.mkdir(parents=True)
                shutil.copyfile(args.base_list_sample, target)
                environment = UnityPy.load(target.read_bytes())
                for obj in environment.objects:
                    if obj.type.name != "TextAsset":
                        continue
                    raw = obj.read().m_Script
                    if isinstance(raw, str):
                        raw = raw.encode("utf-8", "surrogateescape")
                    table = msgpack.unpackb(raw, raw=False, strict_map_key=False)
                    if table.get("mark") == "【ChaListData】" and table.get("dictList"):
                        base_reference = msgpack.packb({
                            "ModID": "kks.fixture", "CategoryNo": table["categoryNo"],
                            "Slot": next(iter(table["dictList"])), "Property": "base.test"
                        }, use_bin_type=True)
                        break
                assert base_reference
            (cards / "card.png").write_bytes(image.getvalue() + msgpack.packb(
                {"ModID": "sample", "Slot": 12, "Property": "ChaFileFace.headId"},
                use_bin_type=True) + base_reference)
            (outfits / "outfit.png").write_bytes(image.getvalue() + msgpack.packb(
                {"ModID": "sample", "Slot": 22, "Property": "outfit.clothes"},
                use_bin_type=True))
            (scenes / "scene.png").write_bytes(image.getvalue() + msgpack.packb({
                "itemInfo": [msgpack.packb(
                    {"ModID": "sample", "Slot": 1, "SceneDicKey": 2}, use_bin_type=True)],
                "mapInfoGUID": "missing-map", "bgmInfoGUID": "missing-bgm",
                "patternInfo": [msgpack.packb({"ObjectPatternInfo": {
                    0: {"GUID": "missing-pattern", "Slot": 3, "LocalSlot": 100003}}},
                    use_bin_type=True)]
            }, use_bin_type=True))
            values = {
                "game": str(game), "mods": str(mods),
                "userdata": str(game / "UserData"), "chara": str(cards),
                "coordinate": str(outfits), "studio": str(scenes),
                "modtrash": str(game / "modtrash"),
                "archive": str(game / "mods_archive"),
            }
            assert post("/api/config", values)["ok"]
            token = json.loads(get("/api/session"))["token"]
            assert json.loads(get("/api/config"))["ready"]
            # Card moves and tags went away with the card view; the routes must
            # be gone, not merely unused.
            for retired in ("/api/library-move", "/api/library-restore", "/api/library-tags"):
                try:
                    post(retired, {"paths": ["chara/x.dat"], "id": "x",
                                   "path": "chara/x.dat", "tags": []})
                    raise AssertionError("retired endpoint still routed: " + retired)
                except urllib.error.HTTPError as error:
                    assert error.code == 404
            assert not (game / "UserData_trash").exists()
            gallery = get("/gallery.html")
            assert b"pagerTop" in gallery and b"pagerBottom" in gallery
            assert b'app-core.js' in gallery and b'deps.js' in gallery
            shell = get("/app-core.js")
            assert b'preserveViewport' in shell and b'registerView' in shell
            # The served page has to carry the render control, not just the server.
            assert "渲染缩略图".encode() in get("/view-mods.js")
            assert b'render-previews' in get("/view-mods.js")
            assert b'KK_MOD_SEARCH' in get("/mod-search.js")
            records = json.loads((app / "catalog" / "index.json").read_text("utf-8"))
            assert len(records) == (3 if args.render_sample else 2), records
            sample_record = next(r for r in records if r["path"] == "sample.zipmod")
            assert sample_record["guid"] == "sample"
            if args.thumbnail_sample:
                assert sample_record["images"] and not sample_record["previewErrors"], sample_record
                preview = get("/" + sample_record["images"][0]["src"])
                with Image.open(io.BytesIO(preview)) as decoded:
                    assert min(decoded.size) > 0
            library = json.loads(get("/api/library"))
            assert len(library) == 3
            if args.base_list_sample:
                base_card = next(resource for resource in library if resource["kind"] == "角色卡")
                assert base_card["baseGameTokens"] == ["kks.fixture"]
                assert not base_card["missing"]
                assert next(ref for ref in base_card["references"]
                            if ref["guid"] == "kks.fixture")["providedByBase"]
            assert all(resource["mods"] == ["sample.zipmod"] for resource in library)
            scene = next(resource for resource in library if resource["kind"] == "场景")
            assert set(scene["missingTokens"]) == {"missing-map", "missing-bgm", "missing-pattern"}
            assert scene["missing"]
            assert not next(resource for resource in library if resource["kind"] == "服装卡")["missing"]
            # One dependency verdict, served from one place.
            dependencies = json.loads(get("/api/deps"))
            assert dependencies["cards"][scene["path"]]["state"] == "missing"
            assert set(dependencies["cards"][scene["path"]]["missing"]) == {
                "missing-map", "missing-bgm", "missing-pattern"}
            assert dependencies["cards"][
                next(r["path"] for r in library if r["kind"] == "角色卡")]["state"] == "complete"
            # The card browser and the card<->mod relation view are retired: the
            # page carries the Mod grid and the dependency report only, and a
            # retired module must not be reachable over HTTP any more.
            page = get("/gallery.html")
            assert b"plugin-relations.js" not in page and b"view-library.js" not in page
            for retired in ("/plugin-relations.js", "/view-library.js"):
                try:
                    get(retired)
                except urllib.error.HTTPError as error:
                    assert error.code == 404, (retired, error.code)
                else:
                    raise AssertionError(retired + " is still served")
            report = get("/plugin-deps.js").decode("utf-8")
            assert "依赖检查" in report and "下载缺失 Mod" in report
            assert "missing-mods/job" in report
            # A download round can be narrowed to the ticked cards, so the
            # per-card tick and its two helper buttons must ship too.
            assert "downloadMissing" in report and "dependencyPickAll" in report
            assert "dependencyClearPick" in report and "dependencyPickNote" in report
            assert "state.picked" in report
            # A round that installs nothing has to say so. The outcome gets its
            # own block, because render() overwrites the summary and the status
            # line on every pass -- which made a finished round look dead.
            assert "dependencyReport" in report and "paintReport" in report
            assert "unmatched" in report and "复制全部 GUID" in report
            # The answer is two numbers: what the index matched, what was
            # installed. Which game a manifest declares, which index was used
            # and how many archives were read are method, and belong in the
            # log -- spelling them out made the line unreadable.
            assert "匹配 ${matched.toLocaleString()} 个" in report
            assert "下载 ${installed.toLocaleString()} 个" in report
            assert "只有 ${otherLabel} 版" not in report
            assert "job.flavour" not in report.split("function paintReport")[1].split("function fetchMissing")[0]
            assert "核对了 " not in report and "unreadable" not in report
            assert "job.scanned" not in report
            # The card coverage lives in the header; how a GUID gets matched is
            # method, not an answer, and it was taken off the page.
            assert "依赖已识别" in report
            assert "本体物品清单" not in report
            # A collision count nobody can open is the same as no information:
            # the header line has to be a control that lists the packages.
            assert "clash-count" in report and "function showClashes" in report
            assert "guid-group" in report and "guid-key" in report
            # Colliding packages are drawn as cards with their covers, like the
            # Mod grid: a cover is what tells two similar names apart.
            assert "KK.media.thumb(thumb.src" in report
            assert b"triangle-alert.svg" in get("/manager.css")
            assert get("/assets/icons/triangle-alert.svg"), \
                "the collision button needs its icon"
            # "Which packages no card refers to" belongs to the Mod grid, where a
            # package is actually disabled. The dependency view answers only
            # "which card is missing what", so its second mode is gone while the
            # card-reference facet still ships.
            assert "哪些 Mod 没被引用" not in report
            assert "unknownMods()" not in report
            assert "function modRow" not in report
            assert "id: 'usage'" in report and "卡片引用" in report
            assert "unknownReferences()" in report
            # Each card states it per kind -- character cards, outfit cards and
            # scenes are counted apart, on the default view.
            assert "张${kind}引用" in report and "reference-counts" in report
            # Each of those lines opens the cards behind it, so both the dialog
            # and the one query that fills it have to be in the shipped script.
            assert "card-refs" in report and "/api/library/cards" in report
            # The collision facet sits next to it, and is still offered
            # dynamically -- an option nothing can be in is not drawn.
            assert "id: 'conflict'" in report
            # The render control is the one red bulk action on the grid.
            assert b"bulk-render" in get("/view-mods.js")
            # A sweep that reaches the viewport edge keeps scrolling instead of
            # stopping at the fold.
            grid_js = get("/view-mods.js").decode("utf-8")
            assert "EDGE_SPEED" in grid_js and "nearEdge" in grid_js
            # The selection is already visible on the cards, so there is no
            # "picked only" entry among the preview states.
            assert '"picked"' not in grid_js and "已选中的 Mod" not in grid_js
            style = get("/manager.css")
            theme = get("/theme.css")
            assert b".bulk-render{color:#fff;background:#c5304a" in style, style[:400]
            assert b".detection-actions{display:flex" in style
            assert b".detection-report{" in style and b".detection-missing{" in style
            assert b".detection-plain{" in style
            # The result line belongs to the search area: it answers what the
            # filters left and what the running task is doing, so it lives
            # inside the header and wears the accent colour instead of hanging
            # below it as a stray grey sentence.
            assert page.index(b'id="status"') < page.index(b"</header>"), page[:2000]
            assert b".status {" in theme and b".status:empty {" in theme, theme[:600]
            # The batch bar rides at the top of the window, so the strip and its
            # sticky rule have to ship with the page.
            assert b'id="dock"' in get("/gallery.html")
            assert b".dock{position:sticky" in style, style[:400]
            # One interaction layer: shared tokens drive every control, card and
            # row, so the hover language cannot drift between the three views.
            assert b"--edge-hover:" in theme, theme[:400]
            assert b"--shadow-hover:" in theme
            assert b"button:not(:disabled):hover {" in theme
            assert b"translateY(-2px)" in theme, theme[:400]
            # The deleted tab used to render a solid square: its icon was never
            # shipped, so the mask had nothing to clip.
            assert b"trash-2.svg" in theme and get("/assets/icons/trash-2.svg"), \
                "the deleted tab needs its icon"
            # Same trap, second time: the archive tab shipped without its mask
            # and painted a filled square. The icon ships and the tab references it.
            assert b"archive.svg" in theme and get("/assets/icons/archive.svg"), \
                "the archive tab needs its icon"
            # The two card actions are icon-only: both draw a masked SVG, so a
            # missing mask shows a filled square instead of the glyph.
            assert b".delete::before" in theme and b".archive::before" in theme, \
                "card actions must paint their icons"
            assert b".detection-download{" in style
            # The line above the deletion and archive lists is one sentence, not
            # a glossary: the per-button list read like a manual and got removed.
            assert b".trash-help{" in style and b".page-hint" not in style
            # Archiving ships as its own tab beside the deletion list, with the
            # button that moves a package out of the mods tree on every card.
            assert b"view-archive.js" in page, page[:2000]
            assert b".archive {" in theme and b".archive-row{" in style
            archive_js = get("/view-archive.js").decode("utf-8")
            assert "/api/archives" in archive_js and "/api/unarchive" in archive_js
            # The page is a backup screen: it must never offer an erase.
            assert "trash-purge" not in archive_js and "trash-danger" not in archive_js
            # A page can outlive the process that served it. Saying so is part of
            # the shipped UI: the banner style and the wording must both travel.
            assert b".offline-banner{" in style
            # Hovering a cover floats it at full size; the panel and its rules
            # are part of the shipped page, not a local experiment.
            assert b".zoom-tip{" in style
            zoom = get("/preview-ui.js").decode("utf-8")
            assert "zoom-tip" in zoom and "mouseover" in zoom
            # Most previews are 128 px square, i.e. the size of a card cell, so
            # the panel has to scale them up instead of drawing at natural size.
            assert "naturalWidth" in zoom and "FRAME" in zoom
            assert "本地管理器已退出" in get("/app-core.js").decode("utf-8")
            assert "KK.api.OFFLINE" in get("/plugin-deps.js").decode("utf-8")
            # Deleted mods are renamed in place, so the shipped page must offer
            # the durable log-driven recovery screen, not just a tab-local undo.
            trash = get("/view-trash.js").decode("utf-8")
            assert b"view-trash.js" in page, "the shell must load the recovery view"
            assert "/api/deletions" in trash and "/api/restore" in trash
            assert "已删除" in trash
            # Rows carry a cover and a permanent-delete button; both are wired to
            # endpoints the frozen build has to route as well.
            assert "/api/deletion-thumb?id=" in trash and "/api/purge" in trash
            assert "彻底删除" in trash
            assert b".trash-thumb{" in style and b".bulk-danger{" in style
            # Restoring stays neutral; the permanent delete shares the batch
            # "render covers" accent instead of a second, muddier red.
            assert b".bulk-danger{color:#fff;background:#c5304a" in style
            assert b".trash-purge-one{min-width:72px;color:#fff;background:#c5304a" in style
            assert "bulk-action bulk-render" not in trash
            assert b".trash-row{" in style
            # Both long-list bars ride the same sticky strip as the Mod grid, so
            # each view has to claim the dock and the styles have to travel.
            assert "dock: controls" in trash, "the deleted-mods bar must dock"
            assert "dock: actions" in report, "the download row must dock"
            assert b".dock .detection-actions{padding:8px 0" in style, style[:400]
            assert b"#trashControls{display" not in style, "the header rule is gone"
            deletions = json.loads(get("/api/deletions"))
            assert "items" in deletions and "pending" in deletions
            try:
                get("/api/deletion-thumb?id=missing")
                raise AssertionError("an unknown deletion must not return a picture")
            except urllib.error.HTTPError as error:
                assert error.code == 404
            # Missing GUIDs are what the download job resolves against the
            # public index; here only the report itself is exercised.
            missing_mods = json.loads(get("/api/missing-mods"))
            assert {"total", "guids"} <= set(missing_mods)
            assert {"guid", "cards", "authors"} <= set(missing_mods["guids"][0])
            assert {row["guid"] for row in missing_mods["guids"]} == {
                "missing-map", "missing-bgm", "missing-pattern"}, missing_mods
            # The job has to carry what the scan covered, or the page cannot tell
            # "the pack lacks it" from "the manifest could not be read".
            idle_job = json.loads(get("/api/missing-mods/job"))
            assert {"scanned", "unreadable", "fallbacks"} <= set(idle_job), idle_job
            classification = {"work": "测试作品", "character": "测试角色", "author": "作者"}
            assert post("/api/mod-classification", {
                "path": "sample.zipmod", "classification": classification
            })["classification"] == classification
            deleted = post("/api/delete", {
                "paths": ["sample.zipmod", "batch.zipmod", "ghost.zipmod"]})
            assert deleted["count"] == 2 and len(deleted["ids"]) == 2
            assert [failure["path"] for failure in deleted["failures"]] == [
                "ghost.zipmod"], deleted
            assert not (mods / "sample.zipmod").exists()
            assert not (mods / "batch.zipmod").exists()
            disabled_file = mods / deleted["disabled"]
            assert disabled_file.is_file() and disabled_file.suffix == ".disabled"
            assert disabled_file.parent == mods
            disabled_files = [mods / name for name in deleted["disabledFiles"]]
            assert len(disabled_files) == 2
            assert all(path.is_file() and path.suffix == ".disabled"
                       for path in disabled_files)
            restored = post("/api/restore", {"ids": deleted["ids"]})
            assert restored["count"] == 2, restored
            assert next(record for record in restored["records"]
                        if record["path"] == "sample.zipmod")["classification"] == classification
            assert (mods / "sample.zipmod").exists()
            assert (mods / "batch.zipmod").exists()
            assert not any(path.exists() for path in disabled_files)
            # Permanent delete is the one irreversible route: it unlinks the
            # disabled copy, forgets the record, and never touches a live file.
            doomed = post("/api/delete", {"path": "batch.zipmod"})
            doomed_disabled = mods / doomed["disabled"]
            assert doomed_disabled.is_file()
            wiped = post("/api/purge", {"ids": [doomed["id"]]})
            assert wiped["purged"] == 1, wiped
            assert not doomed_disabled.exists()
            assert (mods / "sample.zipmod").is_file(), "purge must not touch live archives"
            # Archiving is the move: the package leaves the mods tree, keeps
            # every byte, and the folder is created on first use.
            sample_bytes = (mods / "sample.zipmod").read_bytes()
            assert not (game / "mods_archive").exists(), \
                "an unused archive folder must not be created at startup"
            archived = post("/api/archive", {"path": "sample.zipmod"})
            assert archived["count"] == 1, archived
            assert archived["archiveRoot"] == str(game / "mods_archive")
            assert not (mods / "sample.zipmod").exists(), "the file must leave mods"
            kept_copy = game / "mods_archive" / "sample.zipmod"
            assert kept_copy.read_bytes() == sample_bytes, "moved byte for byte"
            listed_archives = json.loads(get("/api/archives"))
            assert listed_archives["pending"] == 1, listed_archives
            assert listed_archives["archiveRoot"] == str(game / "mods_archive")
            back_again = post("/api/unarchive", {"ids": archived["ids"]})
            assert back_again["count"] == 1, back_again
            assert (mods / "sample.zipmod").read_bytes() == sample_bytes
            assert not kept_copy.exists()
            # An archive folder inside mods would hand the package back to the
            # game, so the settings refuse it outright.
            try:
                post("/api/config", dict(values, archive=str(mods / "archived")))
                raise AssertionError("an archive folder inside mods was accepted")
            except urllib.error.HTTPError as error:
                assert error.code == 400, error.code
            assert (mods / "sample.zipmod").is_file()
            assert all(row["id"] != doomed["id"]
                       for row in json.loads(get("/api/deletions"))["items"])
            # Without the token the endpoint must not erase anything at all.
            try:
                post("/api/purge", {"ids": [deleted["ids"][0]]}, authorized=False)
                raise AssertionError("an unauthorised purge must be refused")
            except urllib.error.HTTPError as error:
                assert error.code == 403, error.code
            saved_state = (app / "runtime" / "server.json").read_bytes()
            duplicate = subprocess.run([str(exe), "--no-open"], cwd=root,
                                       timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
            assert duplicate.returncode == 0
            assert (app / "runtime" / "server.json").read_bytes() == saved_state
            assert json.loads(get("/api/config"))["ready"]
            if args.open_browser:
                previous = browser_requests()
                duplicate = subprocess.run([str(exe)], cwd=root,
                                           timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
                assert duplicate.returncode == 0
                wait_for_browser(previous)
            stop()
            assert start()["ready"]
            saved = json.loads((app / "catalog" / "index.json").read_text("utf-8"))
            assert next(record for record in saved
                        if record["path"] == "sample.zipmod")["classification"] == classification
            assert json.loads(get("/api/library"))[0]["mods"] == ["sample.zipmod"]
            # The render job has to exist inside the frozen application as well.
            # These fixtures carry no meshes, so one failure and no error is the
            # expected outcome: it proves the module loaded and ran.
            assert json.loads(get("/api/render-previews"))["running"] is False
            assert post("/api/render-previews", {"paths": ["sample.zipmod"]})["total"] == 1
            for _ in range(300):
                render = json.loads(get("/api/render-previews?cursor=0"))
                if not render["running"]:
                    break
                time.sleep(0.05)
            assert render["done"] == 1 and render["failed"] == 1, render
            assert render["updates"] == [] and not render["error"], render["error"]
            if args.render_sample:
                # Real meshes, so this one has to produce a picture. It also proves
                # the packaged PIL and UnityPy can read materials and textures.
                assert post("/api/render-previews", {"paths": ["render.zipmod"]})["total"] == 1
                for _ in range(600):
                    drawn = json.loads(get("/api/render-previews?cursor=0"))
                    if not drawn["running"]:
                        break
                    time.sleep(0.1)
                assert not drawn["error"], drawn["error"]
                assert drawn["done"] == 1 and drawn["failed"] == 0, drawn
                assert len(drawn["updates"]) == 1, drawn["updates"]
                image = drawn["updates"][0]["images"][0]
                assert image["source"] == "mesh-render", image
                with Image.open(io.BytesIO(get("/" + image["src"]))) as drawn_image:
                    assert drawn_image.format in ("WEBP", "PNG"), drawn_image.format
                    assert drawn_image.size == (320, 320), drawn_image.size
                    assert drawn_image.convert("RGBA").getbbox(), "rendered image is empty"
                stored = json.loads((app / "catalog" / "index.json").read_text("utf-8"))
                assert next(record for record in stored if record["path"] == "render.zipmod")[
                    "images"][0]["source"] == "mesh-render"
            stop()
            print("PASS: portable EXE, Unicode path, setup, assets, indexing, card-Mod "
                  "association, batch delete/restore, archive out of the mods tree and "
                  "back, repeat launch, restart, authenticated shutdown")
            if args.open_browser:
                print("PASS: real browser page requests on initial and repeat EXE launch")
        finally:
            if process and process.poll() is None:
                try:
                    post("/api/shutdown", {})
                    process.wait(timeout=15)
                except Exception:
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   capture_output=True, check=False)
            log = app / "runtime" / "manager.log"
            if log.exists() and "Traceback" in log.read_text("utf-8"):
                print(log.read_text("utf-8"))


if __name__ == "__main__":
    main()
