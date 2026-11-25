# DEVELOPER ONLY. Builds a connector installer wired to the LOCAL web app/backend (Vite on 5173 proxies /api to Django on 8000).
# Output: release\GSTR2TallyConnectorSetup-DEV.exe  (never overwrites the production installers).
# The tracked release.json (production origin) is backed up and restored, so production configuration is untouched.
param(
    [string]$Origin = 'http://127.0.0.1:5173',
    [string]$InnoSetupCompiler = ''
)
$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$venv = Join-Path $here '.devbuild-venv'
$python = Join-Path $venv 'Scripts\python.exe'
if (-not (Test-Path $python)) { & python -m venv $venv; if ($LASTEXITCODE -ne 0) { throw 'Unable to create build venv.' } }
if (-not $InnoSetupCompiler) {
    $InnoSetupCompiler = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe") |
        Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
$releaseJson = Join-Path $here 'release.json'
$backup = Join-Path $env:TEMP 'gstr-release.json.bak'
Copy-Item -LiteralPath $releaseJson -Destination $backup -Force
try {
    & (Join-Path $here 'build-exe.ps1') -PythonExe $python -Origin $Origin -DevLoopback -Version '1.0.0-dev' -InstallerBaseName 'GSTR2TallyConnectorSetup-DEV' -InnoSetupCompiler $InnoSetupCompiler
} finally {
    Copy-Item -LiteralPath $backup -Destination $releaseJson -Force   # restore the production origin
}
Write-Host 'release.json restored to its original (production) content.'
