"""Refresh the configured catalog and record thumbnail recovery statistics."""
from datetime import datetime
import json
from pathlib import Path
import sys
from types import SimpleNamespace

APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP))
import ModCatalogServer as manager


def main():
    installation = APP / "release" / "KoikatsuModManager"
    catalog = installation / "catalog"
    configuration = installation / "config.json"
    work = APP / "mod-recovery" / "2026-10-28"
    previous_report = json.loads((work / "preview-recovery.json").read_text("utf-8")) \
        if (work / "preview-recovery.json").exists() else {}
    values = manager.config.validate(manager.config.read(configuration), catalog)
    previous = json.loads((catalog / "index.json").read_text("utf-8"))
    existing = [r for r in previous if (Path(values["mods"]) / r["path"]).is_file()]
    before_images = {r["path"]: len(r["images"]) for r in existing}
    server = SimpleNamespace(catalog=catalog, config_file=configuration)
    manager.apply_settings(server, values)
    last = None

    def progress(message):
        nonlocal last
        if message != last:
            print(message, flush=True)
            last = message

    server.on_progress = progress
    manager.initialize_catalog(server)
    records = server.read_records()
    added = [{"path": r["path"], "guid": r["guid"], "images": len(r["images"])}
             for r in records if not before_images.get(r["path"]) and r["images"]]
    newly_visible = {r["path"]: r for r in previous_report.get("newlyVisiblePackages", [])}
    newly_visible.update({r["path"]: r for r in added})
    report = {
        "createdAt": datetime.now().isoformat(timespec="seconds"),
        "packages": len(records),
        "packagesWithImagesBefore": previous_report.get(
            "packagesWithImagesBefore", sum(bool(n) for n in before_images.values())),
        "packagesWithImagesAfter": sum(bool(r["images"]) for r in records),
        "imagesBefore": previous_report.get("imagesBefore", sum(before_images.values())),
        "imagesAfter": sum(len(r["images"]) for r in records),
        "newlyVisiblePackages": list(newly_visible.values()),
        "remainingWithoutImages": [{"path": r["path"], "guid": r["guid"],
                                    "missingThumbBundles": r.get("missingThumbBundles", []),
                                    "previewErrors": r.get("previewErrors", []),
                                    "errors": r["errors"]}
                                   for r in records if not r["images"]],
    }
    work.mkdir(parents=True, exist_ok=True)
    (work / "preview-recovery.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps({k: v for k, v in report.items()
                      if k not in ("newlyVisiblePackages", "remainingWithoutImages")},
                     ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
