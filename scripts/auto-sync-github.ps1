# Watches the repo and auto-commits + pushes to GitHub (debounced).
# Respects .gitignore — secrets in api/* should NOT be committed.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts/auto-sync-github.ps1
#
# Stop with Ctrl+C.

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

$DebounceSec = 45
$LastChange = Get-Date

function Sync-ToGitHub {
    $status = git status --porcelain
    if (-not $status) {
        Write-Host "[auto-sync] No changes."
        return
    }

    # Block if secret files would be staged (extra safety).
    $risky = git status --porcelain | Select-String -Pattern 'api/.*secret|client_secrets|token\.json'
    if ($risky) {
        Write-Warning "[auto-sync] Skipped — possible secret files in changes. Fix .gitignore first."
        return
    }

    $msg = "auto-sync: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    Write-Host "[auto-sync] Committing: $msg"
    git add -A
    git commit -m $msg
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "[auto-sync] Commit failed or nothing to commit."
        return
    }
    & "$PSScriptRoot\git-push-origin.ps1"
}

Write-Host "[auto-sync] Watching $Root (debounce ${DebounceSec}s). Ctrl+C to stop."
Write-Host "[auto-sync] Remote: $(git remote get-url origin)"

$watcher = New-Object System.IO.FileSystemWatcher
$watcher.Path = $Root
$watcher.IncludeSubdirectories = $true
$watcher.EnableRaisingEvents = $true
$watcher.Filter = "*.*"

$onChange = {
    $script:LastChange = Get-Date
}

Register-ObjectEvent $watcher Changed -Action $onChange | Out-Null
Register-ObjectEvent $watcher Created -Action $onChange | Out-Null
Register-ObjectEvent $watcher Deleted -Action $onChange | Out-Null
Register-ObjectEvent $watcher Renamed -Action $onChange | Out-Null

try {
    while ($true) {
        Start-Sleep -Seconds 5
        $idle = ((Get-Date) - $LastChange).TotalSeconds
        if ($idle -ge $DebounceSec) {
            $pending = git status --porcelain
            if ($pending) {
                Sync-ToGitHub
                $script:LastChange = Get-Date
            }
        }
    }
} finally {
    $watcher.EnableRaisingEvents = $false
    $watcher.Dispose()
}
