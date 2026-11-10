"""Offline checks for the missing-mod recovery helpers.

Everything that touches the network is out of scope here: the parts worth
pinning down are which GUIDs count as missing, where a downloaded archive is
allowed to land, and that a manifest is read the same way the installer reads it.
"""
import importlib.util
import io
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main():
    spec = importlib.util.spec_from_file_location(
        "recovery", Path(__file__).resolve().parent.parent / "ModRecovery.py")
    recovery = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(recovery)

    records = [
        {"path": "a.zipmod", "guid": "Installed.One"},
        {"path": "b.zipmod", "guid": ""},
    ]
    cards = [
        {"path": "chara/one.png", "kind": "角色卡", "references": [
            {"guid": "installed.one", "author": "", "website": ""},
            {"guid": "Missing.Two", "author": "Maker", "website": "https://example.invalid"},
        ]},
        {"path": "scene/two.png", "kind": "场景", "references": [
            {"guid": "missing.two", "author": "Maker", "website": ""},
            {"guid": "Third.Three", "author": "", "website": ""},
        ]},
    ]
    missing = recovery.missing_from(records, cards)
    assert [row["guid"] for row in missing] == ["Missing.Two", "Third.Three"], missing
    first = missing[0]
    assert first["cards"] == ["chara/one.png", "scene/two.png"], first
    assert first["authors"] == ["Maker"] and first["kinds"] == {"角色卡": 1, "场景": 1}
    assert not any(row["guid"].casefold() == "installed.one" for row in missing), \
        "an installed GUID is not missing however the card spells it"

    # Where a downloaded package is allowed to land.
    mods = Path("D:/games/mods")
    row = {"url": "https://sideload.betterrepack.com/download/KKEC/"
                  "Sideloader%20Modpack/Maker/%5BMaker%5D%20Hat.zipmod",
           "guid": "maker.hat", "author": "Maker"}
    target = recovery.target_for(row, mods)
    assert target.parent.parent.name == "ForCharactersLoading", target
    assert target.name == "[Maker] Hat.zipmod" and target.parent.name == "Maker"

    studio = dict(row, url=row["url"].replace("Sideloader%20Modpack/",
                                              "Sideloader%20Modpack%20-%20Studio/"))
    assert recovery.target_for(studio, mods).parent.parent.name == "ForStudioScenesLoading"

    for unsafe in ("https://sideload.betterrepack.com/download/KKEC/x/%2e%2e%2Fevil.zipmod",
                   "https://sideload.betterrepack.com/download/KKEC/x/not-a-package.txt",
                   "https://sideload.betterrepack.com/download/KKEC/x/%5C%5Cserver%5Cshare.zipmod"):
        try:
            recovery.target_for(dict(row, url=unsafe), mods)
        except ValueError:
            continue
        raise AssertionError("unsafe download target accepted: " + unsafe)

    # Anything outside the mirror is refused before a request is made.
    for url in ("https://example.com/a.zipmod", "http://sideload.betterrepack.com/a.zipmod"):
        try:
            recovery._request(url)
        except ValueError:
            continue
        raise AssertionError("host not restricted: " + url)

    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("manifest.xml",
                         "<manifest><guid>maker.hat</guid><name>Hat</name>"
                         "<author>Maker</author><version>1.2</version>"
                         "<game>Koikatsu</game></manifest>")
    with zipfile.ZipFile(io.BytesIO(payload.getvalue())) as archive:
        parsed = recovery.manifest(archive)
    assert parsed == {"guid": "maker.hat", "name": "Hat", "author": "Maker",
                      "version": "1.2", "game": "Koikatsu"}, parsed

    # A manifest without a GUID simply never matches; an archive without one at
    # all is not a zipmod.
    without_guid = io.BytesIO()
    with zipfile.ZipFile(without_guid, "w") as archive:
        archive.writestr("manifest.xml", "<manifest><name>No guid</name></manifest>")
    with zipfile.ZipFile(io.BytesIO(without_guid.getvalue())) as archive:
        assert recovery.manifest(archive)["guid"] == ""

    bare = io.BytesIO()
    with zipfile.ZipFile(bare, "w") as archive:
        archive.writestr("readme.txt", "nothing here")
    with zipfile.ZipFile(io.BytesIO(bare.getvalue())) as archive:
        try:
            recovery.manifest(archive)
        except ValueError:
            pass
        else:
            raise AssertionError("archive without manifest.xml was accepted")

    # "Not found" has three causes, and only one of them means the pack lacks
    # the mod. The scan is fed from the cache so this stays offline.
    import json
    import tempfile

    base = "https://sideload.betterrepack.com/download/KKEC/Sideloader%20Modpack/"
    files = [base + "Maker/%5BMaker%5D%20Hat.zipmod",
             base + "Sun/%5BSun%5D%20Skirt.zipmod",
             base + "KKS/Something%20KKS_Only.zipmod",
             base + "Broken/Broken.zipmod"]
    cache = {
        files[0]: {"guid": "Maker.Hat", "name": "Hat", "game": "Koikatsu",
                   "version": "1.0", "url": files[0]},
        files[1]: {"guid": "Sun.Skirt", "name": "Skirt", "game": "Koikatsu Sunshine",
                   "version": "1.0", "url": files[1]},
        files[2]: {"guid": "Kks.Only", "name": "Only", "game": "",
                   "version": "1.0", "url": files[2]},
        files[3]: {"url": files[3], "error": "The read operation timed out"},
    }
    with tempfile.TemporaryDirectory() as folder:
        Path(folder, "missing-manifests.json").write_text(
            json.dumps({"fetchedAt": 0, "rows": cache}), "utf-8")
        coverage = {}
        answered = recovery.resolve(
            [{"guid": "Maker.Hat"}, {"guid": "Sun.Skirt"},
             {"guid": "Kks.Only"}, {"guid": "Absent.One"}],
            files, folder, report=coverage)
        assert list(answered) == ["maker.hat"], answered
        assert coverage["scanned"] == 3, coverage
        assert [row["url"] for row in coverage["unreadable"]] == [files[3]], coverage
        reasons = {row["guid"]: row["reason"] for row in coverage["skipped"]}
        assert reasons == {"Sun.Skirt": "kks", "Kks.Only": "kks"}, coverage

        # A failed read is not an answer, so the next run tries that archive
        # again instead of treating it as "not in the pack" for good.
        original = recovery.inspect
        recovery.inspect = lambda url: {"guid": "Absent.One", "name": "Late",
                                        "game": "Koikatsu", "version": "1.0", "url": url}
        try:
            later = {}
            again = recovery.resolve([{"guid": "Absent.One"}], files, folder, report=later)
        finally:
            recovery.inspect = original
        assert list(again) == ["absent.one"], again
        assert not later["unreadable"], later

        # The same pack serves both games: the Sunshine build must be the one a
        # KKS install picks, and the Koikatsu one must be refused -- the reverse
        # of the pass above. An archive that declares no game is still usable,
        # but a declared Sunshine build wins even over a newer undeclared one.
        extra = base + "Sun/%5BSun%5D%20Skirt%20v2.zipmod"
        kks_files = files + [extra]
        kks_cache = {
            files[0]: dict(cache[files[0]]),
            files[1]: dict(cache[files[1]]),
            files[2]: {"guid": "Old.Both", "name": "Old", "game": "",
                       "version": "1.0", "url": files[2]},
            extra: {"guid": "Sun.Skirt", "name": "Skirt", "game": "",
                    "version": "2.0", "url": extra},
        }
        Path(folder, "missing-manifests-kks.json").write_text(
            json.dumps({"fetchedAt": 0, "rows": kks_cache}), "utf-8")
        kks_coverage = {}
        kks = recovery.resolve(
            [{"guid": "Maker.Hat"}, {"guid": "Sun.Skirt"}, {"guid": "Old.Both"}],
            kks_files, folder, report=kks_coverage, flavour="kks")
        assert kks["sun.skirt"]["url"] == files[1], kks
        assert kks["old.both"]["url"] == files[2], kks
        assert "maker.hat" not in kks, kks
        assert {row["guid"]: row["reason"] for row in kks_coverage["skipped"]} == {
            "Maker.Hat": "kk"}, kks_coverage

    # What the folder ships decides which build is the right one. Ambiguity and
    # an empty folder fall back to KK, which is what every older index assumed.
    with tempfile.TemporaryDirectory() as game:
        assert recovery.game_flavour(game) == "kk"
        Path(game, "Koikatsu.exe").write_text("", "utf-8")
        assert recovery.game_flavour(game) == "kk"
    with tempfile.TemporaryDirectory() as game:
        Path(game, "KoikatsuSunshine.exe").write_text("", "utf-8")
        assert recovery.game_flavour(game) == "kks"
        # A manager pointed at a folder that merely holds the game still lands.
        with tempfile.TemporaryDirectory() as parent:
            Path(parent, "KoikatsuSunshine").mkdir()
            Path(parent, "KoikatsuSunshine", "KoikatsuSunshine.exe").write_text("", "utf-8")
            assert recovery.game_flavour(parent) == "kks"

    print("PASS: missing GUID grouping, install target layout, path and host "
          "refusal, manifest parsing, what a scan did not answer, retrying the "
          "archives it could not read, and picking each game's own build")


if __name__ == "__main__":
    main()
