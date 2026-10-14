# NextGen AI Development Memory interactive installer (PowerShell).
$ErrorActionPreference = 'Stop'
Write-Host 'NextGen AI Development Memory - Windows setup'
$project = Read-Host 'Paste the full path to your EXISTING project folder'
if (-not (Test-Path -LiteralPath $project -PathType Container)) {
    Write-Error 'Folder not found. No changes made.'
    exit 1
}
$mode = Read-Host 'Is this the Nextory factory ERP project? [y/N]'
$arguments = @('-3', (Join-Path $PSScriptRoot 'install.py'), '--project', $project)
if ($mode -match '^(y|yes)$') { $arguments += @('--preset', 'nextory') }
& py @arguments
if ($LASTEXITCODE -ne 0) { Write-Error 'Installation failed; review output.'; exit $LASTEXITCODE }
Write-Host 'Installed. Open a new session in your AI tools, then verify /mcp.'
