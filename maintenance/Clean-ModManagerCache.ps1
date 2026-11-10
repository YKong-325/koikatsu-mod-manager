param([switch]$Apply)

$ErrorActionPreference = 'Stop'
$appRoot = (Get-Item -LiteralPath $PSScriptRoot).Parent.FullName
$workspace = $appRoot
$catalog = Join-Path $appRoot 'catalog'
$gameRoot = (Get-Item -LiteralPath $appRoot).Parent.FullName
$settingsPath = Join-Path $appRoot 'config.json'
$settings = if (Test-Path -LiteralPath $settingsPath) { Get-Content -LiteralPath $settingsPath -Raw -Encoding utf8 | ConvertFrom-Json } else { $null }
$mods = Get-Content -LiteralPath (Join-Path $catalog 'index.json') -Raw | ConvertFrom-Json
$library = Get-Content -LiteralPath (Join-Path $catalog 'library.json') -Raw | ConvertFrom-Json
if (-not $mods.Count -or -not $library.Count) {
    throw 'Both indexes must be populated before cleaning cached thumbnails.'
}
$used = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
foreach ($mod in $mods) {
    foreach ($image in $mod.images) { [void]$used.Add($image.src.Replace('\', '/')) }
}
foreach ($resource in $library) {
    if ($resource.thumb) { [void]$used.Add($resource.thumb.Replace('\', '/')) }
}
$modTrash = if ($settings -and $settings.modtrash) { $settings.modtrash } else { Join-Path $gameRoot 'modtrash' }
$modLog = Join-Path $modTrash 'delete-log.jsonl'
if (Test-Path -LiteralPath $modLog) {
    foreach ($line in Get-Content -LiteralPath $modLog -Encoding utf8) {
        if (-not $line.Trim()) { continue }
        $event = $line | ConvertFrom-Json
        foreach ($image in $event.record.images) {
            if ($image.src) { [void]$used.Add($image.src.Replace('\', '/')) }
        }
    }
}
$libraryTrash = if ($settings -and $settings.libraryTrash) { $settings.libraryTrash } else { Join-Path $gameRoot 'UserData_trash' }
$libraryLog = Join-Path $libraryTrash 'move-log.jsonl'
if (Test-Path -LiteralPath $libraryLog) {
    foreach ($line in Get-Content -LiteralPath $libraryLog -Encoding utf8) {
        if (-not $line.Trim()) { continue }
        $event = $line | ConvertFrom-Json
        foreach ($file in $event.files) {
            if ($file.record.thumb) { [void]$used.Add($file.record.thumb.Replace('\', '/')) }
        }
    }
}
if ($used.Count -eq 0) { throw 'No thumbnail references found; cleanup cancelled.' }
$candidates = [Collections.Generic.List[IO.FileInfo]]::new()
foreach ($folder in @('thumbs', 'library_thumbs')) {
    $directory = Get-Item -LiteralPath (Join-Path $catalog $folder)
    if ($directory.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "Refusing linked cache directory: $($directory.FullName)"
    }
    foreach ($file in Get-ChildItem -LiteralPath $directory.FullName -File) {
        $relative = $folder + '/' + $file.Name
        if ($file.Extension -in @('.png', '.jpg') -and -not $used.Contains($relative)) {
            $candidates.Add($file)
        }
    }
}
$cachePath = Join-Path $appRoot '__pycache__'
if (Test-Path -LiteralPath $cachePath) {
    if ((Get-Item -LiteralPath $cachePath).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "Refusing linked cache directory: $cachePath"
    }
    foreach ($file in Get-ChildItem -LiteralPath $cachePath -File) {
        if ($file.Name -match '^(Build-LibraryIndex|Build-ModCatalog|ModCatalogServer)\.cpython-\d+\.pyc$') {
            $candidates.Add($file)
        }
    }
}
$bytes = 0L
foreach ($file in $candidates) {
    if (-not $file.FullName.StartsWith($workspace + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Target outside workspace: $($file.FullName)"
    }
    $bytes += $file.Length
    if ($Apply) { Remove-Item -LiteralPath $file.FullName }
}
[pscustomobject]@{
    Applied = [bool]$Apply
    Files = $candidates.Count
    MiB = [Math]::Round($bytes / 1MB, 2)
    ThumbnailReferences = $used.Count
}
