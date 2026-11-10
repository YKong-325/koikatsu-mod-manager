"""Dependency analysis lives in one place: Build-LibraryIndex.dependencies().

Ports the assertions of the former dependency-checks.js test so the browser no
longer needs a second implementation of the same rules.
"""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load():
    spec = importlib.util.spec_from_file_location(
        "library_builder", ROOT / "Build-LibraryIndex.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


dependencies = load().dependencies


def mod(path, guid, data_dirs=("chara",), items=None):
    return {"path": path, "guid": guid, "dataDirs": list(data_dirs),
            "items": list(items or [])}


def card(path, guids, kind="角色卡"):
    return {"path": path, "kind": kind, "references": [{"guid": g} for g in guids]}


mods = [
    mod("used.zipmod", "EXAMPLE"),
    mod("duplicate.zipmod", "example"),
    mod("outfit.zipmod", "outfit"),
    mod("scene-used.zipmod", "scene"),
    mod("unmatched.zipmod", "alone"),
    mod("no-guid.zipmod", ""),
    mod("studio.zipmod", "studio", ["studio"]),
    mod("studio-with-chara-thumbs.zipmod", "studio-thumbs", items=[
        {"Category": "工作室", "ListFile": "abdata/studio/info/example.csv"}]),
    mod("mixed.zipmod", "mixed", ["studio"], items=[
        {"Category": "捏脸/衣橱", "ListFile": "abdata/list/characustom/example.csv"}]),
]
resources = [
    card("chara/a.png", ["example", "EXAMPLE", "missing", "MISSING"]),
    card("chara/b.png", ["example"]),
    card("chara/unknown.png", []),
    card("coordinate/a.png", ["outfit"], "服装卡"),
    card("coordinate/missing.png", ["absent-outfit"], "服装卡"),
    card("coordinate/unknown.png", [], "服装卡"),
    card("studio/a.png", ["scene", "SCENE"], "场景"),
    card("studio/missing.png", ["absent-map", "scene"], "场景"),
    card("studio/unknown.png", [], "场景"),
]

result = dependencies(mods, resources)

assert result["coverage"] == {"total": 3, "parsed": 2, "unknown": 1,
                             "missing": 1, "complete": 1}, result["coverage"]
assert result["mods"]["used.zipmod"]["state"] == "matched"
assert result["mods"]["duplicate.zipmod"]["duplicateGuid"] is True
assert len(result["mods"]["used.zipmod"]["cards"]) == 2
assert result["mods"]["outfit.zipmod"]["state"] == "unmatched"
assert len(result["mods"]["outfit.zipmod"]["outfits"]) == 1
assert len(result["mods"]["scene-used.zipmod"]["scenes"]) == 2
for kind in ("服装卡", "场景"):
    assert result["coverageByKind"][kind] == {"total": 3, "parsed": 2, "unknown": 1,
                                              "missing": 1, "complete": 1}, kind

assert result["cards"]["studio/missing.png"] == {"state": "missing",
                                                 "missing": ["absent-map"]}
assert result["cards"]["coordinate/a.png"]["state"] == "complete"
assert result["cards"]["coordinate/missing.png"]["state"] == "missing"
assert result["cards"]["studio/unknown.png"]["state"] == "unknown"
assert result["mods"]["studio.zipmod"]["state"] == "other"
assert result["mods"]["studio-with-chara-thumbs.zipmod"]["state"] == "other"
assert result["mods"]["mixed.zipmod"]["state"] == "unmatched"
assert result["mods"]["no-guid.zipmod"]["state"] == "unknown"
assert result["cards"]["chara/a.png"] == {"state": "missing", "missing": ["MISSING"]}
assert result["cards"]["chara/unknown.png"]["state"] == "unknown"
assert dependencies(mods, [])["mods"]["unmatched.zipmod"]["state"] == "unknown"
assert dependencies(mods, [card("chara/unknown.png", [])])["summary"]["unmatched"] == 0

base_card = {"path": "chara/base.png", "kind": "角色卡",
             "references": [{"guid": "kks.test", "providedByBase": True}]}
assert dependencies([], [base_card])["cards"][base_card["path"]] == {
    "state": "complete", "missing": []}
base_card["references"].append({"guid": "kks.test", "providedByBase": False})
assert dependencies([], [base_card])["cards"][base_card["path"]] == {
    "state": "missing", "missing": ["kks.test"]}

# Results are a snapshot: later edits to the inputs must not rewrite them.
mods.pop(0)
resources.clear()
assert len(result["mods"]["used.zipmod"]["cards"]) == 2
assert result["coverage"]["missing"] == 1

# A mod that supplies nothing a card wants is not counted as character content.
assert dependencies([mod("x.zipmod", "x", ["studio"])], [])["summary"]["characterMods"] == 0

print("PASS: dependency analysis covers character scope, case-insensitive GUID "
      "matching, duplicate GUIDs, missing dependencies, unknown coverage, "
      "outfit/scene references and snapshot isolation")
sys.exit(0)
