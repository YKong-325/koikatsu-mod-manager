"""Serve disposable Mod fixtures for browser interaction checks."""
import importlib.util
import io
import json
import sys
import tempfile
import threading
import zipfile
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
spec = importlib.util.spec_from_file_location(
    "manager", Path(__file__).resolve().parent.parent / "ModCatalogServer.py")
manager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manager)

with tempfile.TemporaryDirectory(prefix="kk-search-preview-") as folder:
    root = Path(folder)
    mods, catalog = root / "mods", root / "app/catalog"
    mods.mkdir()
    catalog.mkdir(parents=True)
    userdata = root / "UserData"
    userdata.mkdir()
    annotations = {}
    for index in range(40):
        filename = f"sample-{index:02}.zipmod"
        image = io.BytesIO()
        Image.new("RGB", (80, 80), ("#e5b3c4", "#9cd7de")[index % 2]).save(image, "PNG")
        with zipfile.ZipFile(mods / filename, "w") as archive:
            archive.writestr("manifest.xml", f"<manifest><guid>sample-{index}</guid>"
                             f"<name>Sample {index:02}</name>"
                             f"<author>{'Alice' if index % 2 == 0 else 'Bob'}</author></manifest>")
            archive.writestr("preview.png", image.getvalue())
        annotations[str(mods.resolve()) + "|" + filename] = {
            "work": "原神" if index < 20 else "崩坏",
            "character": ("胡桃" if index % 2 == 0 else "甘雨") if index < 20 else "琪亚娜",
            "author": ""}
    manager.library_builder.write_json(catalog / "mod-classifications.json", annotations)
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(manager.CatalogHandler, directory=str(catalog)))
    server.mods, server.catalog, server.userdata = mods, catalog, userdata
    server.records = root / "modtrash"
    server.records.mkdir()
    server.log = server.records / "delete-log.jsonl"
    server.authority = f"127.0.0.1:{server.server_port}"
    server.token = "disposable-preview-token"
    server.instance_key = "disposable-preview"
    server.mutation_lock = threading.Lock()
    server.settings, server.ready, server.config_error = {}, True, ""
    manager.initialize_catalog(server)
    print(json.dumps({"url": "http://" + server.authority + "/gallery.html"}), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
