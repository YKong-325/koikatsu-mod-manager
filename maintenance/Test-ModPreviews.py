"""Regression checks for listed thumbnails, migration and persistent previews."""
import argparse
import importlib.util
import io
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
import zipfile

from PIL import Image

APP = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("preview_builder", APP / "Build-ModCatalog.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="kk-preview-test-") as temporary:
        root = Path(temporary)
        mods, catalog = root / "mods", root / "catalog"
        mods.mkdir()
        (catalog / "thumbs").mkdir(parents=True)
        image = io.BytesIO()
        Image.new("RGBA", (32, 32), "red").save(image, "PNG")
        archive_path = mods / "sample.zipmod"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("manifest.xml", "<manifest><guid>preview.test</guid></manifest>")
            archive.writestr("abdata/list/characustom/test.csv",
                             "105\nID,Name,ThumbAB,ThumbTex\n1,Top,CHARA\\MODEL.UNITY3D,preview\n")
            archive.writestr("abdata/chara/model.unity3d", b"placeholder")
            archive.writestr("abdata/chara/thumb/direct.png", image.getvalue())
        with zipfile.ZipFile(archive_path) as archive:
            record = {"items": [{"ThumbAB": "CHARA\\MODEL.UNITY3D", "ThumbTex": "preview"}],
                      "thumbBundles": []}
            wanted = builder.thumbnail_sources(archive, record)
            assert wanted == {"abdata/chara/model.unity3d": {"preview"}}
            assert record["thumbBundles"][0]["path"] == "abdata/chara/model.unity3d"
        if args.sample:
            # Use a small, real bundle locally without shipping game assets in tests.
            with zipfile.ZipFile(args.sample) as source:
                bundle = next(info for info in source.infolist()
                              if "/thumb/" in info.filename.lower() and info.filename.endswith(".unity3d"))
                data = source.read(bundle)
            import UnityPy
            environment = UnityPy.load(data)
            texture = next(obj.peek_name() for obj in environment.objects if obj.type.name == "Texture2D")
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("manifest.xml", "<manifest><guid>preview.test</guid></manifest>")
                archive.writestr("abdata/list/characustom/test.csv",
                                 f"105\nID,Name,ThumbAB,ThumbTex\n1,Top,chara/model.unity3d,{texture}\n")
                archive.writestr("abdata/chara/model.unity3d", data)
                archive.writestr("abdata/studio_thumbnails/disguised.png", data)
            record = builder.scan_one(archive_path, mods, catalog)
            assert not record["errors"] and not record["previewErrors"], record
            assert len(record["images"]) == 2
            assert {image["source"] for image in record["images"]} == {"unity-thumbnail"}
            for image in record["images"]:
                with Image.open(catalog / image["src"]) as preview:
                    assert max(preview.size) <= 512 and min(preview.size) > 0
            (catalog / "index.json").write_text(json.dumps([record]), "utf-8")
            with patch.object(builder, "scan_one", side_effect=AssertionError("Unexpected rescan")):
                assert builder.sync_records(mods, catalog)[0]["images"] == record["images"]
            (catalog / record["images"][0]["src"]).unlink()
            repaired = builder.sync_records(mods, catalog)[0]
            assert all((catalog / image["src"]).exists() for image in repaired["images"])
            # A schema bump alone must not re-decode packages that have nothing:
            # only ones that failed name matching can gain from looser matching.
            stamped = dict(repaired, images=[])
            stamped.pop("previewSchema")
            (catalog / "index.json").write_text(json.dumps([stamped]), "utf-8")
            assert not builder.sync_records(mods, catalog)[0]["images"]
            stale = dict(stamped, previewErrors=["list.unity3d: texture not found: nope"])
            (catalog / "index.json").write_text(json.dumps([stale]), "utf-8")
            migrated = builder.sync_records(mods, catalog)[0]
            assert migrated["images"] and migrated["previewSchema"] == builder.PREVIEW_SCHEMA
            loose = root / "abdata" / "chara" / "shared.unity3d"
            loose.parent.mkdir(parents=True)
            loose.write_bytes(data)
            shared = {"path": "shared.zipmod", "items": [
                {"ThumbAB": "chara/shared.unity3d", "ThumbTex": texture}],
                "images": [], "previewErrors": [],
                "missingThumbBundles": ["abdata/chara/shared.unity3d"]}
            builder.extract_game_thumbnails(shared, root, catalog)
            assert shared["images"] and shared["images"][0]["source"] == "game-thumbnail"
            with patch.object(builder, "decode_thumbnail_bundle", side_effect=AssertionError("Cache missed")):
                builder.extract_game_thumbnails(shared, root, catalog)
    print("PASS: explicit bundle references, path normalization, real Unity texture decoding, "
          "disguised Unity bundles, image persistence, cache migration and missing-image repair")


if __name__ == "__main__":
    main()
