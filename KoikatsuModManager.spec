# Build a portable Windows application without startup-time extraction.
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, copy_metadata

root = Path(SPECPATH)
resources = [
    "Build-ModCatalog.py", "Build-LibraryIndex.py", "ManagerConfig.py",
    "PreviewRenderer.py", "ModRecovery.py",
    "ModManager.html", "BrowserLoading.html", "Setup.html", "settings-ui.js",
    "browser-ui.js", "app-core.js", "media-thumbs.js", "view-mods.js",
    "view-trash.js", "view-archive.js", "card-refs.js",
    "plugin-deps.js", "plugin-conflicts.js", "preview-ui.js", "mod-search.js",
    "manager.css", "theme.css", "THIRD_PARTY_NOTICES.md",
]
datas = [(str(root / name), ".") for name in resources]
datas.append((str(root / "assets"), "assets"))
datas += copy_metadata("pillow") + copy_metadata("msgpack") + copy_metadata("pyinstaller")
datas += copy_metadata("UnityPy", recursive=True)
binaries = []
thumbnail_imports = []
for package in ("UnityPy", "astc_encoder", "etcpak", "archspec", "fmod_toolkit"):
    package_datas, package_binaries, package_imports = collect_all(package, include_py_files=False)
    datas += package_datas
    binaries += package_binaries
    thumbnail_imports += package_imports
python_license = Path(sys.base_prefix) / "LICENSE.txt"
if not python_license.exists():
    python_license = Path(sys.base_prefix) / "LICENSE_PYTHON.txt"
if not python_license.exists():
    raise RuntimeError("Python license was not found")
datas.append((str(python_license), "licenses/Python"))
a = Analysis(
    [str(root / "BrowserLauncher.pyw")],
    pathex=[str(root)], binaries=binaries, datas=datas,
    hiddenimports=["PIL.Image", "PIL.ImageChops", "PIL.ImageDraw", "PIL.ImageEnhance",
                   "msgpack", "xml.etree.ElementTree", "csv", "mmap",
                   # ModRecovery.py is shipped as a data file and loaded at run
                   # time, so the analyser never sees its imports and drops the
                   # standard-library modules only it needs. Without them the
                   # download button dies with "No module named 'html.parser'".
                   "html.parser", "concurrent.futures", "concurrent.futures.thread",
                   "zipfile", "urllib.request", "urllib.error",
                   "texture2ddecoder"] + thumbnail_imports,
    excludes=["webview", "clr", "pythonnet", "numpy", "scipy", "matplotlib",
              "PyQt5", "PyQt6", "PySide2", "PySide6", "tkinter",
              # UnityPy's Sprite/CSV exporters import pandas optionally, and the
              # toolkit's fmod reader does too. numpy is excluded above, so pandas
              # could not run here anyway; dropping it saves ~19 MB.
              "pandas", "pytz", "dateutil"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    name="KoikatsuModManager", debug=False, strip=False, upx=False,
    console=False, icon=str(root / "assets" / "app.ico"), exclude_binaries=True,
)
collection = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False,
                     name="KoikatsuModManager")
