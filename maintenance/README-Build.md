# Build

Use Windows and 64-bit Python 3.11 or newer.

From the project directory, in an isolated Python environment:

```powershell
python -m pip install -r modmanager-requirements.txt "pyinstaller>=6,<7"
powershell -ExecutionPolicy Bypass -File maintenance/Build-Exe.ps1 -Python python
```

The release ZIP contains an EXE, its `_internal` runtime directory and user
documentation. Python, Pillow and msgpack are included. Folder selection
uses the Windows shell. pywebview and WebView2 are not used. Build intermediates are
stored in runtime/build. The folder layout avoids temporary extraction
at startup. Keep the EXE and `_internal` together.

The build script packages only the EXE and documentation, excluding local
configuration, catalogs, previews, logs, game files and trash directories.

## Rebuilding over an installed copy

`Build-Exe.ps1` refuses to build when `release/KoikatsuModManager` already
contains `config.json`, `catalog` or `runtime`, because PyInstaller would
delete them. Move them aside first, then restore them afterwards:

```powershell
# before
Move-Item release/KoikatsuModManager/{config.json,catalog,runtime,zipmod_inventory*.csv} release/_userdata_stage/
Rename-Item release/KoikatsuModManager _old_build
# build, then
Move-Item release/_userdata_stage/* release/KoikatsuModManager/
```

Renaming the stale package directory also avoids PyInstaller's `--clean`
delete step, which can be blocked by file-safety hooks.

The packaged page assets live in `catalog/` and are rewritten on the next
index build, so restoring an old `catalog` keeps working; stale modules from
removed features (`library-ui.js`, `relations-ui.js`, `dependency-checks.js`)
must be deleted by hand.

`pandas`, `pytz` and `lxml` are excluded. UnityPy's Sprite/CSV exporters
import pandas optionally and `numpy` is already excluded, so pandas could
never import at runtime; keeping it added ~19 MB to the archive.

## Preview images

Previews are stored as WebP (`Build-ModCatalog.encode_preview`): 512 px edge,
quality 82, alpha kept. The same files as RGBA PNG cost about six times the
bytes (1.1 GB for 47k previews), because full-colour art is PNG's worst case.
Flat icons 128 px and smaller still keep whichever of WebP/PNG is smaller.

Card covers in `library_thumbs/` are WebP as well (`card_thumbnail()`, 320 px).
Both readers accept `.webp` and the older `.png`/`.jpg`, so an untouched
catalog keeps working.

Existing catalogs are converted in place, without re-reading any `.zipmod`:

```powershell
python maintenance/Compress-Previews.py --catalog release/KoikatsuModManager/catalog
```

It encodes into `thumbs_new/`, renames `thumbs` to `_thumbs_png_old`, then
rewrites `index.json`/`data.js` and `library.json`/`library.js`. Re-running
resumes; `--rewrite-only` just repairs the indexes after an interrupted swap,
and `--purge` deletes the retired folders (blocked by the file-safety hook above
50 files per turn, so expect to remove them by hand).

