param([string]$Python = "python", [string]$OutputRoot = "")
$ErrorActionPreference = 'Stop'
$appRoot = (Get-Item -LiteralPath $PSScriptRoot).Parent.FullName
$release = if ($OutputRoot) { [IO.Path]::GetFullPath($OutputRoot) } else { Join-Path $appRoot 'release' }
$package = Join-Path $release 'KoikatsuModManager'
foreach ($userFile in @('config.json', 'catalog', 'runtime')) {
    if (Test-Path -LiteralPath (Join-Path $package $userFile)) {
        throw 'This release folder contains user data. Move it before rebuilding.'
    }
}
Push-Location -LiteralPath $appRoot
try {
    & $Python -B -c "import sys, struct; assert sys.version_info >= (3, 11) and struct.calcsize('P') == 8, 'Use 64-bit Python 3.11 or newer'"
    if ($LASTEXITCODE -ne 0) { throw 'Unsupported build Python' }
    & $Python -m PyInstaller --noconfirm --clean --distpath $release --workpath (Join-Path $appRoot 'runtime\build') KoikatsuModManager.spec
    if ($LASTEXITCODE -ne 0) { throw 'EXE build failed' }
    # Low-integrity build folders otherwise restrict the EXE and its browser children.
    & icacls.exe (Join-Path $package 'KoikatsuModManager.exe') /setintegritylevel M
    if ($LASTEXITCODE -ne 0) { throw 'Cannot set normal integrity on the release executable' }
    foreach ($name in @('README.md', 'THIRD_PARTY_NOTICES.md')) {
        Copy-Item -LiteralPath (Join-Path $appRoot $name) -Destination $package
    }
    $zipPath = Join-Path $release 'KoikatsuModManager-windows-x64.zip'
    # Only ship these explicit files; a previous launch may have created user data.
    $releaseFiles = @('KoikatsuModManager.exe', '_internal', 'README.md', 'THIRD_PARTY_NOTICES.md') |
        ForEach-Object { Join-Path $package $_ }
    Compress-Archive -LiteralPath $releaseFiles -DestinationPath $zipPath -Force
    Write-Output "Release: $zipPath"
} finally {
    Pop-Location
}
