# Retired views and resources

Nothing here is loaded by the build, the spec or the tests. It is kept only as
reference for what the application used to do.

## Views removed on 2026-10-31

- `view-library.js` — the card browser (角色卡 / 场景 / 服装卡). It listed the
  card index with thumbnails, tags and move-to-trash. The dependency view already
  answers the only question that mattered there ("which card needs a mod I do not
  have"), and card records are now fetched from `/api/library` on demand instead
  of being shipped as a 38 MB `library.js`.
- `plugin-relations.js` — the two-way 资源卡 ↔ Mod view. Both directions were
  already covered by 依赖检查: cards missing mods, and mods no card references.

`app-core.js` no longer exposes `cardThumb`, `onLibraryCard` or
`applyLibraryHooks`; the Mod grid shows a placeholder instead of borrowing a
card cover, which the render job then fills with the package's own picture.

## Endpoints that went with those views

`/api/library-move`, `/api/library-restore` and `/api/library-tags` are gone and
return 404. Tags were the last survivor: the card view was their only writer and
no view ever read `record.tags`, so `library-tags.json`, the `tags` and
`folderTags` fields in `library.json`, and the annotation merge in
`Build-LibraryIndex.py` were removed as well.

## Files added on 2026-11-01

- `icons/users-round.svg` — the library tab icon.
- `icons/link-2.svg` — the relations view icon.
- `Audit-ModTrash.py`, `Audit-WindowsRecycle.py` — one-off audits from
  2026-10-23 that read archives inside `modtrash` and the Windows Recycle Bin.
  `modtrash` no longer stores archives (it holds `delete-log.jsonl` only), and
  the `mod-recovery/2026-10-23` work folder they point at is gone.
- `Restore-MatchedModTrash.ps1`, `Restore-WindowsRecycleMods.ps1` — the
  companion restore passes for those audits.
