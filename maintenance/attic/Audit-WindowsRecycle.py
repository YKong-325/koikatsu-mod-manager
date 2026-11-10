"""Read-only Windows Recycle Bin dependency audit using a Shell inventory."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import importlib.util
import json
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
WORK = APP / "mod-recovery" / "2026-10-23"
INSTALL = APP / "release" / "KoikatsuModManager"
spec = importlib.util.spec_from_file_location("trash_manifest", APP / "maintenance" / "Audit-ModTrash.py")
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


def main():
    config = json.loads((INSTALL / "config.json").read_text("utf-8-sig"))
    inventory = json.loads((WORK / "windows-recycle-inventory.json").read_text("utf-8-sig"))
    installed_paths = [p for p in Path(config["mods"]).rglob("*")
                       if p.is_file() and p.suffix.casefold() in (".zipmod", ".zip")]
    with ThreadPoolExecutor(max_workers=8) as pool:
        installed = list(pool.map(helper.inspect, installed_paths))
    present = {row["guid"].casefold() for row in installed if row.get("guid")}
    library = json.loads((INSTALL / "catalog" / "library.json").read_text("utf-8-sig"))
    missing = {}
    card_missing = {}
    for card in library:
        parts = Path(card["path"]).parts
        actual = (Path(config[parts[0]]).joinpath(*parts[1:])
                  if parts[0] in ("chara", "coordinate", "studio")
                  else Path(config["userdata"]) / card["path"])
        if not actual.is_file():
            continue
        for ref in card.get("references", []):
            guid = ref.get("guid", "")
            key = guid.casefold()
            if not key or key in present:
                continue
            row = missing.setdefault(key, {"guid": guid, "cards": {}})
            row["cards"][card["path"]] = {"path": card["path"], "kind": card["kind"]}
            card_missing.setdefault(card["path"], set()).add(key)
    candidates = {}
    traversal_errors = []
    for entry in inventory:
        path = Path(entry["path"])
        if entry["physicalDirectory"]:
            try:
                for item in path.rglob("*"):
                    if item.is_file() and item.suffix.casefold() in (".zipmod", ".zip"):
                        candidates[str(item)] = {
                            **entry, "member": str(item.relative_to(path)),
                            "originalPath": str(Path(entry["originalDirectory"] or "") /
                                                entry["name"] / item.relative_to(path))}
            except OSError as error:
                traversal_errors.append({"path": str(path), "error": str(error)})
        elif path.suffix.casefold() in (".zipmod", ".zip"):
            candidates[str(path)] = {**entry, "member": "",
                                    "originalPath": str(Path(entry["originalDirectory"] or "") / entry["name"])}
    print(f"Installed: {len(installed)}; remaining missing: {len(missing)}; "
          f"recycle archive candidates: {len(candidates)}", flush=True)
    with ThreadPoolExecutor(max_workers=8) as pool:
        scanned = list(pool.map(helper.inspect, map(Path, candidates)))
    matches = {}
    skipped = []
    errors = []
    for archive in scanned:
        entry = candidates[archive["path"]]
        if "error" in archive:
            if Path(archive["path"]).suffix.casefold() == ".zip" and archive["error"] == "Missing or ambiguous manifest":
                skipped.append(archive)
            else:
                errors.append(archive)
            continue
        archive.update(originalPath=entry["originalPath"], deletedAt=entry["deletedAt"],
                       displayName=entry["name"], member=entry["member"])
        key = archive["guid"].casefold()
        if key in missing:
            row = matches.setdefault(key, {"guid": missing[key]["guid"],
                                          "cards": list(missing[key]["cards"].values()),
                                          "archives": []})
            row["archives"].append(archive)
    matched_cards = set().union(*(set(missing[k]["cards"]) for k in matches)) if matches else set()
    result = {
        "createdAt": datetime.now().isoformat(timespec="seconds"),
        "installedArchives": len(installed), "remainingMissingGuids": len(missing),
        "resourcesWithMissingDependencies": len(card_missing),
        "recycleEntries": len(inventory), "recycleArchivesChecked": len(scanned),
        "matchedGuids": len(matches), "matchedArchives": sum(len(r["archives"]) for r in matches.values()),
        "affectedResources": len(matched_cards),
        "resourcesWithAllMissingGuidsInRecycle": sum(s <= matches.keys() for s in card_missing.values()),
        "matches": sorted(matches.values(), key=lambda r: (-len(r["cards"]), r["guid"])),
        "remainingMissing": [{"guid": row["guid"], "cards": list(row["cards"].values())}
                             for row in missing.values()],
        "archiveErrors": errors, "traversalErrors": traversal_errors,
        "installedArchiveErrors": [r for r in installed if "error" in r],
        "ordinaryZipArchivesSkipped": skipped,
    }
    (WORK / "windows-recycle-audit.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
    lines = [
        "# Windows 回收站缺失 Mod 核对", "", f"检查时间：{result['createdAt']}", "",
        "已从 modtrash 恢复此前匹配的 10 个包，并核对 GUID 与 ZIP CRC。",
        f"当前剩余缺失 {len(missing)} 个 GUID，涉及 {len(card_missing)} 个资源。",
        f"检查 Windows 回收站 {len(inventory)} 个顶层条目及文件夹内文件，读取 {len(scanned)} 个候选压缩包。",
        f"回收站匹配 {len(matches)} 个缺失 GUID，共 {result['matchedArchives']} 个包，涉及 {len(matched_cards)} 个资源。",
        "", "Windows 回收站中的文件仅核对，未恢复、移动或删除。",
        "按包内 GUID 匹配；仅覆盖索引已解析的依赖，GUID 匹配不保证版本内包含全部所需项目。", "",
    ]
    for row in result["matches"]:
        lines.extend([f"## {row['guid']}", "", f"受影响资源：{len(row['cards'])}", ""])
        for archive in row["archives"]:
            lines.extend([f"- 原位置：`{archive['originalPath']}`",
                          f"- 回收站文件：`{archive['path']}`",
                          f"- 版本：{archive['version'] or '未注明'}；游戏：{archive['game'] or '未注明'}",
                          f"- 删除时间：{archive['deletedAt']}", ""])
        lines.extend(f"- {c['kind']}：`{c['path']}`" for c in row["cards"])
        lines.append("")
    lines.extend(["## 检查异常", "",
                  f"目录读取异常：{len(traversal_errors)}；回收站包读取异常：{len(errors)}；"
                  f"无 Mod 清单的普通 ZIP：{len(skipped)}。", ""])
    lines.extend(f"- `{r['path']}`：{r['error']}" for r in errors + traversal_errors)
    (WORK / "windows-recycle-audit.md").write_text("\n".join(lines), "utf-8")
    print(json.dumps({k: v for k, v in result.items()
                      if k not in ("matches", "remainingMissing", "ordinaryZipArchivesSkipped")},
                     ensure_ascii=False, indent=2))
    for row in result["matches"]:
        print(json.dumps(row, ensure_ascii=False))


if __name__ == "__main__":
    main()
