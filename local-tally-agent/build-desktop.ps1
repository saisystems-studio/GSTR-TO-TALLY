param(
    [string]$PythonExe = "python",
    [Parameter(Mandatory = $true)][string]$WebUrl,     # e.g. https://<host>/gstr2tally/  (the deployed GSTR2Tally site)
    [string]$Version = "1.1.0",
    [string]$InstallerBaseName = "GSTR2TallySetup-TEST",
    [string]$InnoSetupCompiler = ""
)
$ErrorActionPreference = 'Stop'
$web = [Uri]$WebUrl
if ($web.Scheme -ne 'https' -or $web.UserInfo -or $web.Query -or $web.Fragment) { throw 'WebUrl must be an https URL without credentials, query or fragment.' }
$origin = $web.GetLeftPart([UriPartial]::Authority)
if (-not $InnoSetupCompiler) {
    $InnoSetupCompiler = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $InnoSetupCompiler) { throw 'Inno Setup 6 (ISCC.exe) not found.' }
# Non-secret configuration only: the web origin the Connector/desktop may talk to. NEVER put Nexus keys here.
([ordered]@{ origin = $origin } | ConvertTo-Json) | Set-Content (Join-Path $PSScriptRoot 'release.json') -Encoding UTF8
([ordered]@{ web_url = $WebUrl } | ConvertTo-Json) | Set-Content (Join-Path $PSScriptRoot 'desktop.json') -Encoding UTF8
$ErrorActionPreference = 'Continue'   # pip writes upgrade notices to stderr
& $PythonExe -m pip install -r (Join-Path $PSScriptRoot 'requirements-build.txt') pywebview --disable-pip-version-check | Out-Null
$ErrorActionPreference = 'Stop'
$paths = @('--paths', (Join-Path $PSScriptRoot '..\backend'))
& $PythonExe -m PyInstaller --noconfirm --clean --onedir --windowed @paths --name GSTR2TallyConnector --distpath (Join-Path $PSScriptRoot 'dist') --workpath (Join-Path $PSScriptRoot 'build-connector') --specpath (Join-Path $PSScriptRoot 'build-connector') (Join-Path $PSScriptRoot 'tally_local_agent.py')
if ($LASTEXITCODE) { throw 'Connector build failed.' }
& $PythonExe -m PyInstaller --noconfirm --clean --onedir --windowed --paths $PSScriptRoot --name GSTR2Tally --distpath (Join-Path $PSScriptRoot 'dist') --workpath (Join-Path $PSScriptRoot 'build-desktop') --specpath (Join-Path $PSScriptRoot 'build-desktop') (Join-Path $PSScriptRoot 'gstr2tally_desktop.py')
if ($LASTEXITCODE) { throw 'Desktop build failed.' }
$out = Join-Path $PSScriptRoot 'release'
New-Item -ItemType Directory -Force $out | Out-Null
& $InnoSetupCompiler "/DMyAppVersion=$Version" "/DOutputName=$InstallerBaseName" "/O$out" (Join-Path $PSScriptRoot 'GSTR2TallySetup.iss')
if ($LASTEXITCODE) { throw 'Inno Setup failed.' }
$exe = Join-Path $out "$InstallerBaseName.exe"
Write-Host "Installer: $exe ($((Get-Item $exe).Length) bytes)"
