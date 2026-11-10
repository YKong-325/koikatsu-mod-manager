$ErrorActionPreference = 'Stop'
$appRoot = Split-Path -Parent $PSScriptRoot
$workRoot = Join-Path $appRoot 'mod-recovery\2026-10-23'
$installation = Join-Path $appRoot 'release\KoikatsuModManager'
$configuration = Get-Content -LiteralPath (Join-Path $installation 'config.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$audit = Get-Content -LiteralPath (Join-Path $workRoot 'windows-recycle-audit.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$modsRoot = [IO.Path]::GetFullPath($configuration.mods).TrimEnd('\') + '\'
$recycleShell = New-Object -ComObject Shell.Application
$recycleFolder = $recycleShell.Namespace(10)
$recycleItems = @($recycleFolder.Items())
$restorePlan = @()
$shellItems = @{}
foreach ($match in $audit.matches) {
    if (@($match.archives).Count -ne 1) { throw "Ambiguous archive for $($match.guid)" }
    $archive = $match.archives[0]
    if ($archive.member) { throw 'Nested archives require separate recovery' }
    $sourcePath = [IO.Path]::GetFullPath($archive.path)
    $destinationPath = [IO.Path]::GetFullPath($archive.originalPath)
    if (-not $destinationPath.StartsWith($modsRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'Destination outside configured mods' }
    if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) { throw "Source missing: $sourcePath" }
    if (Test-Path -LiteralPath $destinationPath) { throw "Destination exists: $destinationPath" }
    $items = @($recycleItems | Where-Object { $_.Path -eq $sourcePath })
    if ($items.Count -ne 1) { throw "Recycle entry missing or ambiguous: $sourcePath" }
    $shellOriginal = [IO.Path]::GetFullPath((Join-Path ([string]$items[0].ExtendedProperty('System.Recycle.DeletedFrom')) $items[0].Name))
    if (-not $shellOriginal.Equals($destinationPath, [StringComparison]::OrdinalIgnoreCase)) { throw 'Recycle original path mismatch' }
    $restorePlan += [pscustomobject]@{
        guid = $match.guid
        source = $sourcePath
        destination = $destinationPath
        sha256 = (Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash
    }
    $shellItems[$sourcePath] = $items[0]
}
$planPath = Join-Path $workRoot 'windows-recycle-restore-plan.json'
$restorePlan | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $planPath -Encoding UTF8
$python = 'C:\Users\Windows\AppData\Local\KoikatsuModManager\build-browser-313\Scripts\python.exe'
& $python -c 'import json,sys,zipfile,xml.etree.ElementTree as E; rows=json.load(open(sys.argv[1],encoding="utf-8-sig")); [(lambda z,r: (None if E.fromstring(z.read("manifest.xml")).findtext("guid","").casefold()==r["guid"].casefold() else sys.exit("GUID mismatch"), None if z.testzip() is None else sys.exit("CRC error")))(zipfile.ZipFile(r["source"]),r) for r in rows]; print("GUID and CRC verified:",len(rows))' $planPath
if ($LASTEXITCODE -ne 0) { throw 'Archive verification failed' }
$restored = @()
foreach ($entry in $restorePlan) {
    [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($entry.destination)) | Out-Null
    $shellItems[$entry.source].InvokeVerb('undelete')
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    while (-not (Test-Path -LiteralPath $entry.destination -PathType Leaf) -and [DateTime]::UtcNow -lt $deadline) {
        Start-Sleep -Milliseconds 250
    }
    if (-not (Test-Path -LiteralPath $entry.destination -PathType Leaf)) { throw "Restore did not complete: $($entry.destination)" }
    $destinationHash = (Get-FileHash -LiteralPath $entry.destination -Algorithm SHA256).Hash
    if ($destinationHash -ne $entry.sha256) { throw "Restored file hash mismatch: $($entry.destination)" }
    if (Test-Path -LiteralPath $entry.source) { throw "Recycle source still exists: $($entry.source)" }
    $restored += [pscustomobject]@{
        guid = $entry.guid
        destination = $entry.destination
        sha256 = $destinationHash
        restoredAt = [DateTime]::Now.ToString('s')
    }
    $restored | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $workRoot 'windows-recycle-restored.json') -Encoding UTF8
    Write-Output "Restored: $($entry.destination)"
}
Write-Output "Successfully restored and verified $($restored.Count) packages"
