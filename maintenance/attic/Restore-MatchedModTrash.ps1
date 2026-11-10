$ErrorActionPreference = 'Stop'
$appRoot = Split-Path -Parent $PSScriptRoot
$workRoot = Join-Path $appRoot 'mod-recovery\2026-10-23'
$installation = Join-Path $appRoot 'release\KoikatsuModManager'
$configuration = Get-Content -LiteralPath (Join-Path $installation 'config.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$audit = Get-Content -LiteralPath (Join-Path $workRoot 'modtrash-audit.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$modsRoot = [IO.Path]::GetFullPath($configuration.mods).TrimEnd('\') + '\'
$trashRoot = [IO.Path]::GetFullPath($configuration.modtrash).TrimEnd('\') + '\'
$restorePlan = @()
foreach ($match in $audit.matches) {
    if (@($match.archives).Count -ne 1) { throw "Ambiguous archive for $($match.guid)" }
    $archive = $match.archives[0]
    $sourcePath = [IO.Path]::GetFullPath($archive.path)
    if (-not $archive.originalPath) { throw "Missing original path for $($match.guid)" }
    $destinationPath = [IO.Path]::GetFullPath((Join-Path $modsRoot $archive.originalPath))
    if (-not $sourcePath.StartsWith($trashRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'Source outside modtrash' }
    if (-not $destinationPath.StartsWith($modsRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'Destination outside mods' }
    if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) { throw "Source missing: $sourcePath" }
    if (Test-Path -LiteralPath $destinationPath) { throw "Destination exists: $destinationPath" }
    $restorePlan += [pscustomobject]@{ guid = $match.guid; source = $sourcePath; destination = $destinationPath }
}
$restorePlan | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $workRoot 'trash-restore-plan.json') -Encoding UTF8
$python = 'C:\Users\Windows\AppData\Local\KoikatsuModManager\build-browser-313\Scripts\python.exe'
& $python -c 'import json,sys,zipfile,xml.etree.ElementTree as E; rows=json.load(open(sys.argv[1],encoding="utf-8-sig")); [(lambda z,r: (None if E.fromstring(z.read("manifest.xml")).findtext("guid","").casefold()==r["guid"].casefold() else sys.exit("GUID mismatch"), None if z.testzip() is None else sys.exit("CRC error")))(zipfile.ZipFile(r["source"]),r) for r in rows]; print("GUID and CRC verified:",len(rows))' (Join-Path $workRoot 'trash-restore-plan.json')
if ($LASTEXITCODE -ne 0) { throw 'Archive preflight failed' }
$restored = @()
foreach ($entry in $restorePlan) {
    [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($entry.destination)) | Out-Null
    Move-Item -LiteralPath $entry.source -Destination $entry.destination -ErrorAction Stop
    $restored += $entry
    $restored | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $workRoot 'trash-restored.json') -Encoding UTF8
}
Write-Output "Restored $($restored.Count) packages"

$recycleShell = New-Object -ComObject Shell.Application
$recycleFolder = $recycleShell.Namespace(10)
$inventory = @($recycleFolder.Items() | ForEach-Object {
    [pscustomobject]@{
        name = $_.Name
        path = $_.Path
        originalDirectory = $_.ExtendedProperty('System.Recycle.DeletedFrom')
        deletedAt = [string]$_.ExtendedProperty('System.Recycle.DateDeleted')
        physicalDirectory = [IO.Directory]::Exists($_.Path)
    }
})
$inventory | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $workRoot 'windows-recycle-inventory.json') -Encoding UTF8
Write-Output "Recycle bin entries inventoried: $($inventory.Count)"
