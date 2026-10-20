param([Parameter(Mandatory=$true)][string]$PlanPath)
$ErrorActionPreference = 'Stop'
$installed = $false
$handover = $false
$restarted = $null
$plan = Get-Content -LiteralPath $PlanPath -Raw -Encoding UTF8 | ConvertFrom-Json
$jobDir = [IO.Path]::GetFullPath((Split-Path -Parent $PlanPath))
$target = [IO.Path]::GetFullPath($plan.target)
$staged = [IO.Path]::GetFullPath($plan.staged)
$targetDir = Split-Path -Parent $target
$backup = Join-Path $jobDir 'previous.exe'
$abort = Join-Path $jobDir 'abort'

function Write-UpdateLog([string]$message) {
    $logDir = Split-Path -Parent $plan.log_path
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    ('{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $message) |
        Add-Content -LiteralPath $plan.log_path -Encoding UTF8
}

function Read-UpdateHash([string]$path) {
    $stream = [IO.File]::OpenRead($path)
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($algorithm.ComputeHash($stream))).Replace('-', '').ToLower() }
    finally { $algorithm.Dispose(); $stream.Dispose() }
}

try {
    if ((Split-Path -Parent $jobDir) -ne $targetDir -or
        -not (Split-Path -Leaf $jobDir).StartsWith('.nju-update-') -or
        (Split-Path -Parent $staged) -ne $jobDir -or
        [IO.Path]::GetExtension($target) -ne '.exe') {
        throw 'Invalid update paths.'
    }
    if ((Read-UpdateHash $staged) -ne $plan.sha256) {
        throw 'Update checksum mismatch.'
    }
    [IO.File]::WriteAllText((Join-Path $jobDir 'ready'), 'ready')
    $parent = Get-Process -Id $plan.parent_pid -ErrorAction SilentlyContinue
    $deadline = (Get-Date).AddMinutes(3)
    while ($parent -and -not $parent.HasExited) {
        if (Test-Path -LiteralPath $abort) { exit 0 }
        if ((Get-Date) -gt $deadline) { throw 'Timed out waiting for the application to exit.' }
        $null = $parent.WaitForExit(500)
    }
    if (Test-Path -LiteralPath $abort) { exit 0 }
    $handover = $true
    # The one-file bootloader can hold the image briefly after the inner
    # application exits. Retry atomic replacement until that handle is released.
    for ($attempt = 0; $attempt -lt 120; $attempt++) {
        try {
            [IO.File]::Replace($staged, $target, $backup, $true)
            $installed = $true
            break
        } catch {
            if ($attempt -eq 119) { throw }
            Start-Sleep -Milliseconds 500
        }
    }
    if ((Read-UpdateHash $target) -ne $plan.sha256) {
        throw 'Installed checksum mismatch.'
    }
    # Force a fresh one-file extraction instead of inheriting the old
    # PyInstaller process's extraction directory, which has just been removed.
    $env:PYINSTALLER_RESET_ENVIRONMENT = '1'
    $acknowledgement = Join-Path $jobDir 'restarted'
    $restarted = Start-Process -FilePath $target -WorkingDirectory $targetDir -PassThru `
        -ArgumentList @('--update-ready', ('"{0}"' -f $acknowledgement))
    $startupDeadline = (Get-Date).AddSeconds(60)
    while (-not (Test-Path -LiteralPath $acknowledgement)) {
        if ($restarted.HasExited) { throw 'The updated application could not start.' }
        if ((Get-Date) -gt $startupDeadline) { throw 'Timed out waiting for the updated application to start.' }
        Start-Sleep -Milliseconds 200
    }
    Write-UpdateLog ('Updated and restarted: ' + $plan.revision)
    # Both paths above were resolved and checked before this recursive cleanup.
    Remove-Item -LiteralPath $jobDir -Recurse -Force -ErrorAction SilentlyContinue
    exit 0
} catch {
    Write-UpdateLog ('Update failed: ' + $_.Exception.Message)
    if ($restarted -and -not $restarted.HasExited) {
        & (Join-Path $env:SystemRoot 'System32/taskkill.exe') /PID $restarted.Id /T /F | Out-Null
        $null = $restarted.WaitForExit(5000)
    }
    if ($installed -and (Test-Path -LiteralPath $backup)) {
        try {
            [IO.File]::Replace($backup, $target, (Join-Path $jobDir 'failed.exe'), $true)
            Write-UpdateLog 'Restored the previous version.'
        } catch {
            $handover = $false
            Write-UpdateLog ('Restore failed: ' + $_.Exception.Message + '; previous EXE: ' + $backup)
        }
    }
    if ($handover) {
        try {
            $env:PYINSTALLER_RESET_ENVIRONMENT = '1'
            Start-Process -FilePath $target -WorkingDirectory $targetDir | Out-Null
            Write-UpdateLog 'Restarted the previous version.'
        } catch { Write-UpdateLog ('Restart failed: ' + $_.Exception.Message) }
    }
    exit 1
}
