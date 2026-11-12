"""Integration checks using disposable archives, never the user's mods."""
import importlib.util
import json
import hashlib
import io
import tempfile
import threading
import time
import sys
import urllib.error
import urllib.request
import zipfile
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlencode
import msgpack
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main():
    spec = importlib.util.spec_from_file_location(
        "manager", Path(__file__).resolve().parent.parent / "ModCatalogServer.py")
    manager = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(manager)
    # The download line has to make sense on its own: "3/16 MB" left it open
    # which number was the total, so each side carries its unit now.
    assert manager.download_progress("x", 3 * 1048576, 16 * 1048576) == \
        "正在下载 x · 3.0 / 16 MB（18%）"
    assert manager.download_progress("x", 1048576, 0) == "正在下载 x · 已下载 1.0 MB"
    assert manager.download_progress("x", 20 * 1048576, 16 * 1048576).endswith("（100%）")
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        mods = root / "mods"
        catalog = root / "ModManager" / "catalog"
        records_dir = root / "delete-records"
        for directory in (mods, catalog, records_dir):
            directory.mkdir(parents=True)
        userdata = root / "UserData"
        (userdata / "chara").mkdir(parents=True)
        library = manager.library_builder
        ref = {"ModID": "example", "Slot": 12, "Property": "ChaFileFace.headId"}
        encoded = msgpack.packb(ref, use_bin_type=True)
        assert library.resolver_records(encoded)[0]["guid"] == "example"
        assert not library.resolver_records(b"defaultlayer resolveitem example unknownMod")
        assert not library.resolver_records(msgpack.packb({"ModID": "example", "Slot": 1}))
        scene_data = {
            "itemInfo": [msgpack.packb({"ModID": "object", "Slot": 7,
                                      "SceneDicKey": 9}, use_bin_type=True)],
            "mapInfoGUID": "map", "filterInfoGUID": "filter",
            "rampInfoGUID": "ramp", "bgmInfoGUID": "bgm",
            "patternInfo": [msgpack.packb({
                "SceneDicKey": 1, "SceneObjectOrder": 0,
                "ObjectPatternInfo": {0: {"GUID": "pattern", "Slot": 3, "LocalSlot": 100001}}
            }, use_bin_type=True)],
        }
        scene_refs = library.resolver_records(msgpack.packb(scene_data, use_bin_type=True))
        assert {ref["guid"] for ref in scene_refs} == {
            "object", "map", "filter", "ramp", "bgm", "pattern"}
        assert not library.resolver_records(msgpack.packb(
            {"ObjectPatternInfo": {0: {"GUID": "invalid", "Slot": 1}}}, use_bin_type=True))
        assert not library.resolver_records(msgpack.packb(
            {"mapInfoGUID": ["invalid"]}, use_bin_type=True))
        # Identical tails must not make different files appear to be duplicates.
        tail = b"x" * (512 * 1024)
        (userdata / "chara/a.dat").write_bytes(b"a" + encoded + tail)
        (userdata / "chara/b.dat").write_bytes(b"b" + encoded + tail)
        indexed = library.build(userdata, catalog, [{"path": "example.zipmod", "guid": "example"}])
        assert len(indexed) == 2 and not any(r["duplicate"] for r in indexed)
        assert indexed[0]["sha256"] == hashlib.sha256((userdata / indexed[0]["path"]).read_bytes()).hexdigest()
        assert all(r["mods"] == ["example.zipmod"] for r in indexed)
        library.apply_dependencies(indexed, [])
        assert all(r["missingTokens"] == ["example"] for r in indexed)
        library.save_library(indexed, catalog)
        archive = mods / "example.zipmod"
        with zipfile.ZipFile(archive, "w") as fixture:
            fixture.writestr("manifest.xml", "<manifest><guid>example</guid></manifest>")
        archive_bytes = archive.read_bytes()
        (root / "outside.zipmod").write_bytes(b"outside")
        record = {"path": "example.zipmod", "file": "example.zipmod",
                  "items": [], "guid": "example", "name": "Example", "author": "",
                  "bytes": len(archive_bytes), "slots": [], "images": [], "thumbBundles": [],
                  "dataDirs": [], "errors": []}
        records = [record]
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0), partial(manager.CatalogHandler, directory=str(catalog)))
        server.mods, server.records = mods, records_dir
        server.log = records_dir / "delete-log.jsonl"
        # Archiving moves files here; the folder itself is created on first use.
        server.archive = root / "mods_archive"
        server.archive_log = server.archive / "archive-log.jsonl"
        server.catalog, server.userdata = catalog, userdata
        server.authority = f"127.0.0.1:{server.server_port}"
        server.token = "test-token"
        server.mutation_lock = threading.Lock()
        builder_spec = importlib.util.spec_from_file_location(
            "builder", Path(__file__).resolve().parent.parent / "Build-ModCatalog.py")
        builder = importlib.util.module_from_spec(builder_spec)
        builder_spec.loader.exec_module(builder)
        # The deletion thumbnail endpoint rescans a disabled archive itself.
        server.builder = builder

        builder.save(records, catalog, catalog.parent / "zipmod_inventory._mods.csv", 0)
        library.write_json(catalog / "sources.json",
                           {"mods": str(mods), "userdata": str(userdata), "resources": {}})
        progress_messages = []
        server.on_progress = progress_messages.append
        manager.initialize_catalog(server)
        assert progress_messages and server.progress["phase"] == "complete"
        records = server.read_records()
        dependency_snapshot = (catalog / "library.json").read_bytes()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = "http://" + server.authority

        def post(endpoint, body, authenticated=True):
            headers = {"Content-Type": "application/json", "Origin": base}
            if authenticated:
                headers["X-Catalog-Token"] = server.token
            request = urllib.request.Request(base + endpoint,
                                             json.dumps(body).encode(), headers)
            try:
                with urllib.request.urlopen(request) as response:
                    return response.status, json.load(response)
            except urllib.error.HTTPError as exc:
                return exc.code, json.load(exc)

        try:
            for endpoint, content_type in (("/theme.css", "text/css"),
                                           ("/assets/app-mark.png", "image/png"),
                                           ("/assets/icons/package.svg", "image/svg+xml")):
                with urllib.request.urlopen(base + endpoint) as response:
                    assert response.headers.get_content_type() == content_type
                    assert response.read()
            with urllib.request.urlopen(base + "/api/progress") as response:
                assert json.load(response)["phase"] == "complete"
            try:
                urllib.request.urlopen(base + "/assets/../ManagerConfig.py")
                raise AssertionError("Asset traversal accepted")
            except urllib.error.HTTPError as exc:
                assert exc.code == 403
            with urllib.request.urlopen(base + "/api/library") as response:
                assert len(json.load(response)) == 2
            # Dependency verdicts are computed once and served from /api/deps.
            with urllib.request.urlopen(base + "/api/deps") as response:
                dependencies = json.load(response)
            assert set(dependencies) == {"cards", "mods", "coverage", "coverageByKind", "summary"}
            assert dependencies["coverage"]["total"] == 2
            # The "referenced by N" lines on a Mod card are buttons, and the
            # list behind one is a single query keyed by package path and kind:
            # sending a thousand card paths back would not fit in a URL.
            with urllib.request.urlopen(base + "/api/library") as response:
                card_rows = json.load(response)
            ref_mod, ref_verdict = next(
                (path, entry) for path, entry in dependencies["mods"].items()
                if entry.get("cards"))
            query = urlencode({"mod": ref_mod, "kind": "cards"})
            with urllib.request.urlopen(base + "/api/library/cards?" + query) as response:
                answer = json.load(response)
            assert answer["total"] == len(ref_verdict["cards"])
            assert {row["path"] for row in answer["cards"]} == set(ref_verdict["cards"])
            assert all(set(row) == {"path", "file", "kind", "thumb"}
                       for row in answer["cards"]), "only the fields the list draws"
            assert {row["path"] for row in answer["cards"]} <= \
                {row["path"] for row in card_rows}
            try:
                urllib.request.urlopen(base + "/api/library/cards")
                raise AssertionError("A missing package path was accepted")
            except urllib.error.HTTPError as exc:
                assert exc.code == 400
            try:
                urllib.request.urlopen(base + "/api/library/cards?" + urlencode(
                    {"mod": ref_mod, "kind": "maps"}))
                raise AssertionError("An unknown card kind was accepted")
            except urllib.error.HTTPError as exc:
                assert exc.code == 400
            assert (catalog / "deps.js").is_file() and (catalog / "deps.json").is_file()
            assert b"window.KK_DEPS=" in (catalog / "deps.js").read_bytes()
            assert post("/api/delete", {"path": record["path"]}, False)[0] == 403
            assert archive.is_file()
            assert post("/api/delete", {"path": "../outside.zipmod"})[0] == 400
            assert (root / "outside.zipmod").is_file()
            classification = {"work": "作品", "character": "角色", "author": "作者"}
            request = {"path": record["path"], "classification": classification}
            assert post("/api/mod-classification", request, False)[0] == 403
            assert post("/api/mod-classification", dict(request, path="../outside.zipmod"))[0] == 400
            assert post("/api/mod-classification", dict(request, classification={"work": 42}))[0] == 400
            assert post("/api/mod-classification", dict(
                request, classification=dict(classification, work="x" * 121)))[0] == 400
            assert post("/api/mod-classification", request)[0] == 200
            assert server.read_records()[0]["classification"] == classification
            # Startup reuse, cache migration and directory changes must not lose/mix labels.
            manager.initialize_catalog(server)
            assert server.read_records()[0]["classification"] == classification
            current_mods = server.mods
            other_mods = root / "other-mods"
            other_mods.mkdir()
            server.mods = other_mods
            unlabelled = [dict(server.read_records()[0])]
            server.save_records(unlabelled)
            assert "classification" not in server.read_records()[0]
            server.mods = current_mods
            server.save_records(unlabelled)
            assert server.read_records()[0]["classification"] == classification
            records = server.read_records()
            # Restore must apply the latest annotations even when the event held old metadata.
            assert (catalog / "library.json").read_bytes() == dependency_snapshot
            status, result = post("/api/delete", {"path": record["path"]})
            assert status == 200 and not archive.exists() and not server.read_records()
            assert json.loads((catalog / "index.json").read_text(encoding="utf-8")) == []
            # The delete log is the durable undo list: the page's undo button only
            # remembers its own tab, so /api/deletions has to report what the file
            # system actually still holds.
            with urllib.request.urlopen(base + "/api/deletions") as response:
                listed = json.load(response)
            assert listed["pending"] == 1, listed
            entry = [row for row in listed["items"] if row["id"] == result["id"]][0]
            assert entry["available"] and not entry["restored"], entry
            assert entry["file"] == "example.zipmod" and entry["bytes"] == len(archive_bytes)
            # A delete must invalidate the cached verdicts, not leave them stale.
            with urllib.request.urlopen(base + "/api/deps") as response:
                assert json.load(response)["mods"] == {}
            assert (catalog / "library.json").read_bytes() == dependency_snapshot
            assert post("/api/refresh", {})[0] == 404
            # Card tags went with the card view; the endpoint must stay gone.
            assert post("/api/library-tags", {"path": "chara/a.dat", "tags": ["favorite"]})[0] == 404
            assert all(r["mods"] == ["example.zipmod"] for r in
                       json.loads((catalog / "library.json").read_text("utf-8")))
            disabled = mods / result["disabled"]
            assert disabled.read_bytes() == archive_bytes
            assert disabled.parent == archive.parent and disabled.suffix == ".disabled"
            assert not any(r["path"].endswith(".disabled") for r in
                           builder.sync_records(mods, catalog))
            archive.write_bytes(b"conflict")
            assert post("/api/restore", {"id": result["id"]})[0] == 400
            # A restore blocked by a same-named file must say so before the click.
            with urllib.request.urlopen(base + "/api/deletions") as response:
                blocked = [row for row in json.load(response)["items"]
                           if row["id"] == result["id"]][0]
            assert blocked["conflict"] and blocked["available"]
            assert blocked["id"] == result["id"]
            archive.unlink()
            assert post("/api/restore", {"id": result["id"]})[0] == 200
            with urllib.request.urlopen(base + "/api/deletions") as response:
                settled = json.load(response)
            assert settled["pending"] == 0
            assert [row for row in settled["items"] if row["id"] == result["id"]][0]["restored"]
            assert archive.read_bytes() == archive_bytes
            assert not disabled.exists()
            assert server.read_records() == records
            assert (catalog.parent / "zipmod_inventory._mods.csv").read_bytes() == (catalog / "packages.csv").read_bytes()
            assert (catalog.parent / "zipmod_inventory.csv").read_bytes() == (catalog / "items.csv").read_bytes()
            assert not list(root.glob("zipmod_inventory*.csv"))
            assert post("/api/restore", {"id": result["id"]})[0] == 400
            # A record written before the in-place rename cannot be resolved:
            # it must surface as a listed-but-unavailable row, not as a crash.
            with server.log.open("a", encoding="utf-8") as log:
                log.write(json.dumps({"action": "delete", "id": "legacy-test",
                    "source": "legacy.zipmod", "quarantine": "legacy/legacy.zipmod",
                    "record": dict(records[0], path="legacy.zipmod"),
                    "modsRoot": str(mods)}) + "\n")
            with urllib.request.urlopen(base + "/api/deletions") as response:
                legacy = [row for row in json.load(response)["items"]
                          if row["id"] == "legacy-test"][0]
            assert legacy["available"] is False and legacy["restored"] is False
            assert post("/api/restore", {"id": "legacy-test"})[0] == 400
            server.save_records(records)
            # Batch disable: one request, one index write, and a bad path is
            # reported per file instead of sinking the whole selection.
            batch_dir = mods / "batch"
            batch_dir.mkdir()
            batch = []
            for number in (1, 2, 3):
                (batch_dir / f"batch{number}.zipmod").write_bytes(
                    b"payload" + bytes([number]))
                batch.append({"path": f"batch/batch{number}.zipmod",
                              "file": f"batch{number}.zipmod"})
            server.save_records(records + [dict(records[0], **item) for item in batch])
            assert len(server.read_records()) == 4
            status, result = post("/api/delete", {"paths": [
                "batch/batch1.zipmod", "batch/batch2.zipmod", "batch/missing.zipmod"]})
            assert status == 200, result
            assert result["count"] == 2 and len(result["ids"]) == 2
            assert [failure["path"] for failure in result["failures"]] == [
                "batch/missing.zipmod"]
            assert not (batch_dir / "batch1.zipmod").exists()
            assert (batch_dir / "batch3.zipmod").is_file(), "unselected files stay"
            assert [r["path"] for r in server.read_records()] == [
                "example.zipmod", "batch/batch3.zipmod"]
            logged = [json.loads(line) for line in
                      server.log.read_text(encoding="utf-8").splitlines() if line]
            assert sum(1 for entry in logged if entry.get("action") == "delete" and
                       entry.get("id") in result["ids"]) == 2
            status, back = post("/api/restore", {"ids": result["ids"]})
            assert status == 200 and back["count"] == 2, back
            assert (batch_dir / "batch1.zipmod").read_bytes() == b"payload\x01"
            assert (batch_dir / "batch2.zipmod").read_bytes() == b"payload\x02"
            assert len(server.read_records()) == 4
            assert post("/api/restore", {"ids": result["ids"]})[0] == 400
            assert post("/api/delete", {"paths": []})[0] == 400
            assert post("/api/delete", {"paths": ["../outside.zipmod"]})[0] == 400
            assert (root / "outside.zipmod").is_file()
            for item in sorted(batch_dir.iterdir()):
                item.unlink()
            batch_dir.rmdir()
            server.save_records(records)
            # Archiving is the other intent: the package leaves the mods tree
            # instead of being renamed in place, so the backup folder holds a
            # copy the game cannot see.
            status, moved = post("/api/archive", {"path": "example.zipmod"})
            assert status == 200, moved
            assert moved["count"] == 1 and moved["archiveRoot"] == str(root / "mods_archive")
            assert not archive.exists(), "archiving must move the file out of mods"
            kept = root / "mods_archive" / "example.zipmod"
            assert kept.read_bytes() == archive_bytes, "moved byte for byte"
            assert all(r["path"] != "example.zipmod" for r in server.read_records())
            with urllib.request.urlopen(base + "/api/archives") as response:
                listed = json.load(response)
            assert listed["pending"] == 1, listed
            assert listed["archiveRoot"] == str(root / "mods_archive")
            row = [r for r in listed["items"] if r["id"] == moved["id"]][0]
            assert row["available"] and not row["restored"] and not row["conflict"]
            assert row["file"] == "example.zipmod" and row["bytes"] == len(archive_bytes)
            # A same-named file already back in mods blocks the return, and the
            # page has to know before the button is pressed.
            archive.write_bytes(b"conflict")
            with urllib.request.urlopen(base + "/api/archives") as response:
                blocked = [r for r in json.load(response)["items"]
                           if r["id"] == moved["id"]][0]
            assert blocked["conflict"] and blocked["available"]
            assert post("/api/unarchive", {"id": moved["id"]})[0] == 400
            archive.unlink()
            status, back = post("/api/unarchive", {"id": moved["id"]})
            assert status == 200 and back["count"] == 1, back
            assert archive.read_bytes() == archive_bytes
            assert not kept.exists()
            assert post("/api/unarchive", {"id": moved["id"]})[0] == 400
            # An archive folder inside mods would hand the package straight back
            # to the game, so the server refuses the move outright.
            server.archive = mods / "archived"
            server.archive_log = server.archive / "archive-log.jsonl"
            assert post("/api/archive", {"path": "example.zipmod"})[0] == 400
            assert archive.is_file(), "a refused archive changes nothing"
            server.archive = root / "mods_archive"
            server.archive_log = server.archive / "archive-log.jsonl"
            assert post("/api/archive", {"paths": []})[0] == 400
            assert post("/api/archive", {"path": "../outside.zipmod"})[0] == 400
            assert post("/api/archive", {"path": "missing.zipmod"})[0] == 400
            assert (root / "outside.zipmod").is_file()
            server.save_records(records)
            # A deleted package keeps its cover. The thumbnail endpoint serves the
            # preview the index already wrote, and scans the disabled archive once
            # when a record carries no images - which is what a record rebuilt
            # from the file names alone looks like.
            picture = mods / "pictured.zipmod"
            picture_bytes = io.BytesIO()
            Image.new("RGB", (16, 16), "teal").save(picture_bytes, format="PNG")
            with zipfile.ZipFile(picture, "w") as package:
                package.writestr("manifest.xml", "<manifest><guid>pictured</guid></manifest>")
                package.writestr("abdata/chara/thumb/pictured.png", picture_bytes.getvalue())
            pictured = builder.scan_one(picture, mods, catalog)
            assert pictured["images"], "the fixture has to ship a preview"
            server.save_records(list(server.read_records()) + [pictured])
            status, removed = post("/api/delete", {"path": "pictured.zipmod"})
            assert status == 200, removed
            with server.log.open("a", encoding="utf-8") as log:
                log.write(json.dumps({"action": "delete", "id": "thumb-fallback",
                    "source": "pictured.zipmod", "disabled": removed["disabled"],
                    "record": {"file": "pictured.zipmod", "bytes": 1},
                    "modsRoot": str(mods)}) + "\n")
            for event_id in (removed["id"], "thumb-fallback"):
                with urllib.request.urlopen(
                        base + "/api/deletion-thumb?id=" + event_id) as response:
                    assert response.status == 200, event_id
                    assert response.headers["Content-Type"].startswith("image/")
                    assert response.read()[:4] in (b"RIFF", b"\x89PNG"), event_id
            try:
                urllib.request.urlopen(base + "/api/deletion-thumb?id=missing")
                raise AssertionError("an unknown id must not return a picture")
            except urllib.error.HTTPError as error:
                assert error.code == 404
            # Purging is the one irreversible endpoint: it unlinks the disabled
            # copy, forgets the record, and refuses anything it cannot vouch for.
            assert post("/api/purge", {"ids": []})[0] == 400
            assert post("/api/purge", {"ids": ["no-such-record"]})[0] == 400
            assert (mods / removed["disabled"]).is_file(), "a refused purge keeps the file"
            status, wiped = post("/api/purge", {"ids": [removed["id"], "thumb-fallback"]})
            assert status == 200 and wiped["purged"] == 2, wiped
            assert not (mods / removed["disabled"]).exists()
            assert not picture.exists()
            with urllib.request.urlopen(base + "/api/deletions") as response:
                left = {row["id"] for row in json.load(response)["items"]}
            assert removed["id"] not in left and "thumb-fallback" not in left
            # A restored record has no disabled copy left; purging it must only
            # drop the entry and never delete the archive that is back in place.
            status, again = post("/api/delete", {"path": "example.zipmod"})
            assert status == 200, again
            assert post("/api/restore", {"id": again["id"]})[0] == 200
            assert archive.is_file()
            assert post("/api/purge", {"ids": [again["id"]]})[0] == 200
            assert archive.is_file(), "purge must never touch a restored archive"
            server.save_records(records)
            # Every card-view endpoint is retired: moving, restoring and tagging
            # cards all left with that view.
            for retired in ("/api/library-move", "/api/library-restore"):
                assert post(retired, {"paths": ["chara/a.dat"]})[0] == 404
            assert post("/api/library-tags", {"path": "chara/a.dat", "tags": ["favorite"]})[0] == 404
            assert (userdata / "chara/a.dat").is_file(), "retired endpoints must not touch the file"
            rebuilt = library.build(userdata, catalog, records)
            assert "tags" not in next(r for r in rebuilt if r["path"] == "chara/a.dat")
            # The next launch discovers external removals and additions.
            archive.unlink()
            (mods / "new.zipmod").write_bytes(b"new-disposable-archive")
            manager.initialize_catalog(server)
            assert [r["path"] for r in server.read_records()] == ["new.zipmod"]
            assert all(r["missingTokens"] == ["example"] for r in
                       json.loads((catalog / "library.json").read_text("utf-8")))
            check_directory_setup(manager, server, post, root)
            # Same archive names in different Mod folders must show their own images.
            preview_sources = []
            for number, color in enumerate(("red", "blue")):
                archive_root = root / ("preview-mods-" + str(number))
                archive_root.mkdir()
                image_bytes = io.BytesIO()
                Image.new("RGB", (8, 8), color).save(image_bytes, format="PNG")
                with zipfile.ZipFile(archive_root / "same.zipmod", "w") as archive_file:
                    archive_file.writestr("abdata/chara/thumb/test.png", image_bytes.getvalue())
                scanned = builder.scan_one(archive_root / "same.zipmod", archive_root, catalog)
                preview_sources.append(scanned["images"][0]["src"])
            assert preview_sources[0] != preview_sources[1]

            # A package with no mesh cannot be rendered, but the job must still
            # finish, report the failure, and leave the index alone.
            def get_json(endpoint):
                request = urllib.request.Request(
                    base + endpoint, headers={"X-Catalog-Token": server.token})
                with urllib.request.urlopen(request) as response:
                    return response.status, json.load(response)

            status, idle = get_json("/api/render-previews")
            assert status == 200 and idle["running"] is False and idle["total"] == 0
            assert post("/api/render-previews/cancel", {})[0] == 400
            assert post("/api/render-previews", {"paths": []})[0] == 400
            assert post("/api/render-previews", {"paths": ["../outside.zipmod"]})[0] == 400
            targets = [record["path"] for record in server.read_records()]
            status, queued = post("/api/render-previews", {"paths": targets})
            assert status == 200 and queued["total"] == len(targets)
            assert post("/api/render-previews", {"paths": targets})[0] == 400, \
                "only one render job may run at a time"
            for _ in range(400):
                _, state = get_json("/api/render-previews?cursor=0")
                if not state["running"]:
                    break
                time.sleep(0.05)
            assert state["running"] is False
            assert state["done"] == queued["total"] and state["failed"] == queued["total"]
            assert state["updates"] == [], "nothing renderable, so nothing to report"
            assert all(image.get("source") != "mesh-render"
                       for record in server.read_records() for image in record["images"])

            # A job that really draws: the renderer is stubbed, because what is
            # under test here is the index write, not the picture. Checkpointing
            # is forced to every package so the mid-run flush runs too.
            class StubRenderer:
                @staticmethod
                def render_package(path, size=320):
                    return Image.new("RGBA", (320, 320), (24, 120, 140, 255))

            original = manager.CatalogHandler.CHECKPOINT
            manager.CatalogHandler.CHECKPOINT = 1
            server.preview_renderer = StubRenderer
            try:
                status, queued = post("/api/render-previews", {"paths": targets})
                assert status == 200 and queued["total"] == len(targets)
                for _ in range(400):
                    _, drawn = get_json("/api/render-previews?cursor=0")
                    if not drawn["running"]:
                        break
                    time.sleep(0.05)
                assert drawn["done"] == len(targets) and drawn["failed"] == 0, drawn
                assert not drawn["error"], drawn["error"]
                assert len(drawn["updates"]) == len(targets)
                assert all(update["images"][0]["source"] == "mesh-render"
                           for update in drawn["updates"])
                written = {record["path"]: record["images"]
                           for record in json.loads(
                               (catalog / "index.json").read_text("utf-8"))}
                assert all(written[update["path"]][0]["source"] == "mesh-render"
                           for update in drawn["updates"])
                assert all((catalog / image["src"]).is_file()
                           for images in written.values() for image in images)

                # Repainting: a package whose picture is already on disk keeps the
                # same key, so the job has to overwrite it — otherwise a renderer
                # fix never reaches the packages drawn before it.
                first = drawn["updates"][0]
                original_bytes = urllib.request.urlopen(
                    base + "/" + first["images"][0]["src"]).read()

                class RepaintRenderer:
                    @staticmethod
                    def render_package(path, size=320):
                        return Image.new("RGBA", (320, 320), (200, 40, 60, 255))

                server.preview_renderer = RepaintRenderer
                status, requeued = post("/api/render-previews",
                                        {"paths": [first["path"]]})
                assert status == 200 and requeued["total"] == 1
                for _ in range(400):
                    _, redrawn = get_json("/api/render-previews?cursor=0")
                    if not redrawn["running"]:
                        break
                    time.sleep(0.05)
                assert redrawn["done"] == 1 and redrawn["failed"] == 0, redrawn
                same_src = redrawn["updates"][0]["images"][0]["src"]
                assert same_src == first["images"][0]["src"], "the key must not move"
                assert urllib.request.urlopen(base + "/" + same_src).read() \
                    != original_bytes, "an existing preview was left as it was"
            finally:
                manager.CatalogHandler.CHECKPOINT = original
                server.preview_renderer = None

            # Missing-mod fetching. The search and the download are stubbed: what
            # has to hold is the contract around them — what counts as missing,
            # what the job reports, and that the archive reaches the index.
            status, report = get_json("/api/missing-mods")
            assert status == 200 and isinstance(report["guids"], list), report
            assert all(row["guid"] for row in report["guids"])
            _, idle = get_json("/api/missing-mods/job")
            assert idle["running"] is False and idle["updates"] == []
            assert post("/api/missing-mods/job/cancel", {})[0] == 400

            guid = "test.recover"

            class StubRecovery:
                @staticmethod
                def missing_from(records, cards):
                    # Three holes: one the pack can fill, one it cannot, and one
                    # it only ships as a Sunshine build. A round that answers
                    # half of its questions has to say which was which --
                    # "installed 0" alone reads as a broken download.
                    return [{"guid": guid, "authors": ["Test"], "websites": [],
                             "cards": ["chara/a.png"], "kinds": {"角色卡": 1}},
                            {"guid": "test.absent", "authors": ["Ghost"],
                             "websites": ["https://example.invalid/x"],
                             "cards": ["chara/a.png", "chara/b.png"],
                             "kinds": {"角色卡": 2}},
                            {"guid": "test.kks", "authors": ["Sunny"],
                             "websites": [], "cards": ["chara/a.png"],
                             "kinds": {"角色卡": 1}}]

                @staticmethod
                def game_flavour(root=None):
                    return "kk"

                @staticmethod
                def crawl(catalog, progress=None, cancel=None, flavour="kk", report=None):
                    if report is not None:
                        report.update({"packs": ["Sideloader Modpack"], "unreadPacks": []})
                    return ["https://sideload.betterrepack.com/download/KKEC/pack/a.zipmod"]

                @staticmethod
                def resolve(rows, files, catalog, progress=None, cancel=None,
                            report=None, flavour="kk"):
                    if report is not None:
                        report.update({
                            "scanned": 7469,
                            "unreadable": [{"url": files[0], "error": "timed out"}],
                            "fallbacks": [{"guid": "test.kks", "name": "Sunny",
                                           "version": "1.0", "game": "Koikatsu Sunshine",
                                           "url": files[0] + "KKS_", "reason": "kks"}]})
                    return {row["guid"].casefold(): {
                                "guid": row["guid"], "url": files[0], "bytes": 0,
                                "name": "Stub", "version": "1.0",
                                "game": "koikatu", "author": "Test"}
                            for row in rows if row["guid"] in (guid, "test.kks")}

                @staticmethod
                def target_for(row, mods):
                    return (Path(mods) / "ForCharactersLoading" / "Test"
                            / (row["guid"] + ".zipmod"))

                @staticmethod
                def download(row, destination, progress=None, cancel=None):
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with zipfile.ZipFile(destination, "w") as archive:
                        archive.writestr("manifest.xml",
                                         f"<manifest><guid>{row['guid']}</guid>"
                                         "<name>Stub</name><version>1.0</version></manifest>")
                    return destination

            server.recovery_module = StubRecovery
            try:
                _, synthetic = get_json("/api/missing-mods")
                assert synthetic["total"] == 3 and synthetic["guids"][0]["guid"] == guid
                assert post("/api/missing-mods/job", {"guids": []})[0] == 400
                assert post("/api/missing-mods/job",
                            {"guids": ["not-a-real.guid"]})[0] == 400, \
                    "only GUIDs that are actually missing may be requested"
                status, started = post("/api/missing-mods/job",
                                       {"guids": [guid, "test.absent", "test.kks"]})
                assert status == 200 and started["total"] == 3
                # The job says which game's builds it went looking for, because
                # "only the other version exists" means the opposite thing
                # depending on the install.
                assert started["flavour"] == "kk", started
                for _ in range(400):
                    _, fetched = get_json("/api/missing-mods/job?cursor=0")
                    if not fetched["running"]:
                        break
                    time.sleep(0.05)
                assert not fetched["error"], fetched["error"]
                assert fetched["flavour"] == "kk", fetched
                assert fetched["stage"] == "完成" and fetched["done"] == 2, fetched
                assert fetched["requested"] == 3, fetched
                assert fetched["matched"] == 2 and fetched["failed"] == 0, fetched
                # The GUID the pack cannot answer is reported, with the author
                # the card recorded, so the page can name it instead of leaving
                # the user staring at "installed 0".
                assert [row["guid"] for row in fetched["unmatched"]] == \
                    ["test.absent"], fetched
                assert fetched["unmatched"][0]["authors"] == ["Ghost"]
                assert fetched["unmatched"][0]["websites"] == ["https://example.invalid/x"]
                assert fetched["unmatched"][0]["reason"] == "absent", fetched
                # The Sunshine-only build is downloaded all the same -- whether
                # it loads is the game's call -- so it is reported as a
                # fallback and no longer counted among the unanswered ones.
                assert [row["guid"] for row in fetched["fallbacks"]] == ["test.kks"], fetched
                assert fetched["scanned"] == 7469 and fetched["unreadable"] == 1, fetched
                assert len(fetched["updates"]) == 2, fetched["updates"]
                landed = next(row for row in fetched["updates"] if row["guid"] == guid)
                assert landed["file"] == guid + ".zipmod"
                assert (mods / landed["path"]).is_file()
                assert next(record for record in json.loads(
                    (catalog / "index.json").read_text("utf-8"))
                    if record["path"] == landed["path"])["guid"] == guid
            finally:
                server.recovery_module = None
            _, after = get_json("/api/missing-mods")
            assert guid not in {row["guid"] for row in after["guids"]}, \
                "a freshly installed package must drop out of the missing list"

            print("PASS: authentication, traversal rejection, in-place disable, index sync, "
                  "restore conflict, restore, duplicate restore rejection, batch disable, "
                  "archive out of the mods tree and back, refused archive inside mods, "
                  "full-file hashes, resolver parsing, batch preflight, "
                  "retired card endpoints, deleted covers, permanent purge, "
                  "startup-only dependency status, startup filesystem reconciliation, "
                  "directory setup, independent roots, config persistence and picker cancellation, "
                  "render job life cycle and checkpointed index writes, "
                  "missing-mod fetch and install")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


def check_directory_setup(manager, server, post, root):
    config = manager.config
    server.config_file = root / "settings.json"
    server.picker_lock = threading.Lock()
    values = config.defaults(root)
    values.update({"chara": str(root / "external-cards"),
                   "coordinate": str(root / "external-outfits"),
                   "studio": "", "modtrash": str(root / "modtrash")})
    cards = Path(values["chara"])
    outfits = Path(values["coordinate"])
    cards.mkdir()
    outfits.mkdir()
    (cards / "sample.dat").write_bytes(b"external-card")
    (outfits / "sample.dat").write_bytes(b"external-outfit")
    assert post("/api/config", values, False)[0] == 403
    invalid = dict(values, mods=str(root / "nonexistent"))
    assert post("/api/config", invalid)[0] == 400
    invalid = dict(values, modtrash=str(cards / "trash"))
    assert post("/api/config", invalid)[0] == 400
    # An archive folder inside the mods tree would hand the archived package
    # straight back to the game, which is the one thing archiving must not do.
    invalid = dict(values, archive=str(Path(values["mods"]) / "archived"))
    assert post("/api/config", invalid)[0] == 400
    assert not server.config_file.exists()
    # First-use setup does not allow mutation until a valid selection is saved.
    server.ready = False
    assert post("/api/delete", {"path": "new.zipmod"})[0] == 400
    with patch.object(manager.subprocess, "run",
                      return_value=SimpleNamespace(returncode=0, stdout='{"path": ""}')):
        assert post("/api/pick-folder", {"initial": str(root)}) == (200, {"path": ""})
    picker_outputs = []
    def frozen_picker(command, **kwargs):
        assert command[1] == "--pick-folder"
        output = Path(command[3])
        output.write_text('{"path": ""}', "utf-8")
        picker_outputs.append(output)
        return SimpleNamespace(returncode=0)
    with patch.object(manager.sys, "frozen", True, create=True), \
            patch.object(manager.subprocess, "run", side_effect=frozen_picker):
        assert post("/api/pick-folder", {"initial": str(root)}) == (200, {"path": ""})
    assert picker_outputs and not picker_outputs[0].exists()
    assert post("/api/config", values)[0] == 200
    assert server.ready and config.read(server.config_file)["chara"] == str(cards)
    indexed = json.loads((server.catalog / "library.json").read_text("utf-8"))
    assert {r["path"] for r in indexed} == {"chara/sample.dat", "coordinate/sample.dat"}
    assert {r["kind"] for r in indexed} == {"角色卡", "服装卡"}
    # No tag bookkeeping survives in the rebuilt index.
    assert all("tags" not in r and "folderTags" not in r for r in indexed)
    manager.initialize_catalog(server)
    indexed = json.loads((server.catalog / "library.json").read_text("utf-8"))
    assert all("tags" not in r and "folderTags" not in r for r in indexed)
    # A different physical folder using the same virtual filename gets fresh data.
    other = root / "other-cards"
    other.mkdir()
    (other / "sample.dat").write_bytes(b"another-card")
    assert post("/api/config", dict(values, chara=str(other)))[0] == 200
    indexed = json.loads((server.catalog / "library.json").read_text("utf-8"))
    card = next(r for r in indexed if r["path"] == "chara/sample.dat")
    assert card["sha256"] == hashlib.sha256(b"another-card").hexdigest()
    assert "tags" not in card
    assert post("/api/config", values)[0] == 200
    indexed = json.loads((server.catalog / "library.json").read_text("utf-8"))
    assert "tags" not in next(r for r in indexed if r["path"] == "chara/sample.dat")


if __name__ == "__main__":
    main()
