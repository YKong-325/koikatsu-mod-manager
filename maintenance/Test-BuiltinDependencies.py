"""Regression checks for base-game coverage without masking real missing mods."""
import importlib.util
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
from types import SimpleNamespace
import msgpack

APP = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("library", APP / "Build-LibraryIndex.py")
library = importlib.util.module_from_spec(spec)
spec.loader.exec_module(library)


def main():
    def ref(guid="kks.test", category=105, slot=300):
        return {"guid": guid, "category": category, "slot": slot}

    cases = [
        [ref()], [ref(category=106)], [ref(slot=301)], [ref(category=None)],
        [ref(guid="ordinary.mod")], [ref(), ref(slot=301)],
        [ref(category=True, slot=300)], [ref(guid="KKS.TEST")],
    ]
    records = [{"path": str(i), "references": refs} for i, refs in enumerate(cases)]
    library.apply_dependencies(records, [], {(105, 300)})
    assert [r["missing"] for r in records] == [False, True, True, True, True, True, True, False]
    assert records[5]["missingTokens"] == ["kks.test"]
    assert records[0]["baseGameTokens"] == ["kks.test"]
    library.apply_dependencies(records, [])
    assert all(r["missing"] for r in records)
    library.apply_dependencies(records, [{"guid": "KKS.TEST", "path": "installed.zipmod"}], {(105, 300)})
    assert records[0]["mods"] == ["installed.zipmod"]
    assert not records[0]["references"][0]["providedByBase"]
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        bundle = root / "abdata/list/characustom/00.unity3d"
        bundle.parent.mkdir(parents=True)
        bundle.write_bytes(b"test fixture")
        catalog = root / "catalog"
        catalog.mkdir()
        raw = msgpack.packb({"mark": "【ChaListData】", "categoryNo": 105,
                            "dictList": {300: ["300", "Top"]}}, use_bin_type=True)
        asset = SimpleNamespace(type=SimpleNamespace(name="TextAsset"),
                                read=lambda: SimpleNamespace(m_Script=raw.decode("utf-8", "surrogateescape")))
        with patch("UnityPy.load", return_value=SimpleNamespace(objects=[asset])) as load:
            assert library.builtin_items(root, catalog) == {(105, 300)}
            assert library.builtin_items(root, catalog) == {(105, 300)}
            assert load.call_count == 1
            bundle.write_bytes(b"updated fixture")
            assert library.builtin_items(root, catalog) == {(105, 300)}
            assert load.call_count == 2
        cache = json.loads((catalog / "builtin-items.json").read_text("utf-8"))
        assert not cache["errors"]
    print("PASS: exact category/ID, partial GUID coverage, unknown category, ordinary mods, "
          "installed precedence, stale flags, base list decoding and cache invalidation")


if __name__ == "__main__":
    main()
