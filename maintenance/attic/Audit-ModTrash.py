"""Read archive manifests and compare quarantined mods with card dependencies."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile


APP = Path(__file__).resolve().parent.parent
INSTALL = APP / "release" / "KoikatsuModManager"


def inspect(path):
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            matches = [n for n in names if n.casefold() == "manifest.xml"]
            if not matches:
                matches = [n for n in names if n.casefold().endswith("/manifest.xml")]
            if len(matches) != 1:
                raise ValueError("Missing or ambiguous manifest")
            info = archive.getinfo(matches[0])
            if info.file_size > 1024 * 1024:
                raise ValueError("Oversized manifest")
            root = ET.fromstring(archive.read(info))
            values = {node.tag.rsplit("}", 1)[-1].casefold(): (node.text or "").strip()
                      for node in root if not list(node)}
            if not values.get("guid"):
                raise ValueError("Empty GUID")
            return {"path": str(path), **{key: values.get(key, "")
                    for key in ("guid", "name", "version", "game", "author")}}
    except Exception as error:
        return {"path": str(path), "error": str(error)}


def main():
    configuration = json.loads((INSTALL / "config.json").read_text("utf-8-sig"))
    mods = Path(configuration["mods"])
    trash = Path(configuration["modtrash"])
    cards = json.loads((INSTALL / "catalog" / "library.json").read_text("utf-8-sig"))
    indexed = json.loads((INSTALL / "catalog" / "index.json").read_text("utf-8-sig"))
    extensions = {".zipmod", ".zip"}
    installed_paths = [p for p in mods.rglob("*") if p.suffix.casefold() in extensions and p.is_file()]
    trash_paths = [p for p in trash.rglob("*") if p.suffix.casefold() in extensions and p.is_file()]
    with ThreadPoolExecutor(max_workers=8) as pool:
        installed = list(pool.map(inspect, installed_paths))
        print(f"Read {len(installed)} installed manifests", flush=True)
        quarantined = list(pool.map(inspect, trash_paths))
        print(f"Read {len(quarantined)} quarantined manifests", flush=True)
    present = {row["guid"].casefold() for row in installed if row.get("guid")}
    missing = {}
    card_missing = {}
    skipped_cards = 0
    for card in cards:
        parts = Path(card["path"]).parts
        prefix = parts[0].casefold()
        if prefix in ("chara", "coordinate", "studio"):
            actual = Path(configuration[prefix]).joinpath(*parts[1:])
        else:
            actual = Path(configuration["userdata"]) / card["path"]
        if not actual.is_file():
            skipped_cards += 1
            continue
        for reference in card.get("references", []):
            guid = reference.get("guid", "")
            key = guid.casefold()
            if not key or key in present:
                continue
            row = missing.setdefault(key, {"guid": guid, "cards": {}})
            row["cards"][card["path"]] = {"path": card["path"], "kind": card["kind"]}
            card_missing.setdefault(card["path"], set()).add(key)
    deletion_log = {}
    log_errors = []
    log_path = trash / "delete-log.jsonl"
    if log_path.exists():
        for line_number, line in enumerate(log_path.read_text("utf-8-sig").splitlines(), 1):
            try:
                entry = json.loads(line)
                if entry.get("quarantine"):
                    deletion_log[str(trash / entry["quarantine"]).casefold()] = entry
            except Exception as error:
                log_errors.append({"line": line_number, "error": str(error)})
    matches = {}
    for archive in quarantined:
        key = archive.get("guid", "").casefold()
        if key not in missing:
            continue
        entry = deletion_log.get(archive["path"].casefold(), {})
        archive["originalPath"] = entry.get("source", "")
        archive["deletedAt"] = entry.get("time", "")
        row = matches.setdefault(key, {"guid": missing[key]["guid"],
                                      "cards": list(missing[key]["cards"].values()),
                                      "archives": []})
        row["archives"].append(archive)
    matched_cards = set().union(*(set(missing[k]["cards"]) for k in matches)) if matches else set()
    prior = json.loads((APP / "mod-recovery" / "2026-10-23" / "remaining-missing.json").read_text("utf-8"))
    previous_keys = {row["guid"].casefold() for row in prior}
    errors = [row for row in installed + quarantined if "error" in row]
    indexed_paths = {r["path"].replace("\\", "/").casefold() for r in indexed}
    report = {
        "createdAt": datetime.now().isoformat(timespec="seconds"),
        "modsRoot": str(mods), "modtrash": str(trash),
        "installedArchives": len(installed), "trashArchives": len(quarantined),
        "missingGuidCount": len(missing), "missingResourceCount": len(card_missing),
        "matchedGuidCount": len(matches),
        "matchedArchiveCount": sum(len(row["archives"]) for row in matches.values()),
        "affectedResourceCount": len(matched_cards),
        "resourcesWithAllMissingGuidsInTrash": sum(
            values <= matches.keys() for values in card_missing.values()),
        "addedMissingSincePreviousAudit": [missing[k]["guid"] for k in missing.keys() - previous_keys],
        "resolvedSincePreviousAudit": sorted(previous_keys - missing.keys()),
        "skippedDeletedResources": skipped_cards,
        "unindexedInstalledArchives": [row["path"] for row in installed
            if str(Path(row["path"]).relative_to(mods)).replace("\\", "/").casefold()
            not in indexed_paths],
        "matches": sorted(matches.values(), key=lambda row: (-len(row["cards"]), row["guid"])),
        "remainingNotInTrash": [missing[k]["guid"] for k in missing.keys() - matches.keys()],
        "errors": errors, "logErrors": log_errors,
    }
    output = APP / "mod-recovery" / "2026-10-23"
    (output / "modtrash-audit.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    lines = [
        "# modtrash 缺失 Mod 核对", "",
        f"检查时间：{report['createdAt']}", "",
        f"当前缺失 {len(missing)} 个 GUID，涉及 {len(card_missing)} 个资源。",
        f"检查 modtrash 中 {len(quarantined)} 个压缩包，找到 {len(matches)} 个缺失 GUID，"
        f"共 {report['matchedArchiveCount']} 个包，涉及 {len(matched_cards)} 个资源。",
        f"其中 {report['resourcesWithAllMissingGuidsInTrash']} 个资源的已识别缺失 GUID 全部可在回收目录找到。",
        "", "按包内 GUID 匹配，未恢复或移动任何文件。GUID 命中不保证版本内包含所有所需项目。",
        "缺失统计仅覆盖现有索引已解析的依赖，未重新解析角色卡、服装卡或场景。", "",
    ]
    for row in report["matches"]:
        lines.extend([f"## {row['guid']}", "",
                      f"涉及 {len(row['cards'])} 个资源：" +
                      "，".join(f"{k} {v}" for k, v in Counter(c["kind"] for c in row["cards"]).items()), ""])
        for archive in row["archives"]:
            lines.extend([f"- 文件：`{archive['path']}`",
                          f"- 版本：{archive['version'] or '未注明'}；游戏：{archive['game'] or '未注明'}",
                          f"- 原位置：`{archive['originalPath']}`",
                          f"- 删除时间：{archive['deletedAt']}", ""])
        lines.extend(["受影响资源：", ""])
        lines.extend(f"- {card['kind']}：`{card['path']}`" for card in row["cards"])
        lines.append("")
    lines.extend(["## 检查异常", "", f"压缩包读取异常：{len(errors)}；删除日志异常：{len(log_errors)}。", ""])
    lines.extend(f"- `{row['path']}`：{row['error']}" for row in errors)
    (output / "modtrash-audit.md").write_text("\n".join(lines), "utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in
                      ("matches", "remainingNotInTrash", "errors")}, ensure_ascii=False, indent=2))
    for row in report["matches"]:
        print(json.dumps({"guid": row["guid"], "resources": len(row["cards"]),
                          "files": [a["path"] for a in row["archives"]]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
