# Push current branch to origin (used by git hooks and auto-sync).
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

$branch = (git rev-parse --abbrev-ref HEAD).Trim()
if (-not $branch) {
    Write-Error "Could not detect git branch."
}

Write-Host "[git-sync] Pushing $branch -> origin..."
git push origin "HEAD:$branch"
if ($LASTEXITCODE -ne 0) {
    Write-Error "git push failed (check GitHub login / token)."
}
Write-Host "[git-sync] Done."
