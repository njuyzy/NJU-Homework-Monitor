$ErrorActionPreference = 'Stop'
$projectRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    $python = (Get-Command python -ErrorAction Stop).Source
}

Push-Location $projectRoot
$originalPath = $env:PATH
try {
    $revision = (& git rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or $revision -notmatch '^[0-9a-f]{40}$') { throw 'Cannot determine build revision.' }
    New-Item -ItemType Directory -Force -Path 'build' | Out-Null
    @{ revision = $revision } | ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath 'build/build-info.json'
    # Avoid PyInstaller picking up same-named ICU DLLs from unrelated tools on the host PATH.
    $env:PATH = (($env:PATH -split ';') | Where-Object {
        $_ -and $_ -notmatch '[\\/]poppler[\\/]Library[\\/]bin$'
    }) -join ';'
    $pysideRoot = (& $python -c "from pathlib import Path; import PySide6; print(Path(PySide6.__file__).parent)").Trim()
    $codecvtDll = Join-Path $pysideRoot 'msvcp140_codecvt_ids.dll'
    $vcompDll = Join-Path $pysideRoot 'vcomp140.dll'

    $buildArgs = @(
        '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile', '--windowed',
        '--name', 'NJU-Homework-Monitor', '--icon', 'static/nju-icon.ico',
        '--collect-all', 'playwright', '--collect-all', 'windows_toasts',
        '--add-data', 'static;static',
        '--add-data', 'build/build-info.json;.',
        '--hidden-import', 'winrt.windows.ui.notifications'
    )
    foreach ($dll in @($codecvtDll, $vcompDll)) {
        if (Test-Path -LiteralPath $dll) { $buildArgs += @('--add-binary', "${dll};PySide6") }
    }
    $buildArgs += 'desktop_app.py'
    & $python @buildArgs
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }
} finally {
    $env:PATH = $originalPath
    Pop-Location
}

Write-Output (Join-Path $projectRoot 'dist\NJU-Homework-Monitor.exe')
