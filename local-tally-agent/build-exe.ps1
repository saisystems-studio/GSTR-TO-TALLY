param(
    [string]$PythonExe = "python",
    [Parameter(Mandatory = $true)][string]$Origin,
    [string]$Version = "1.0.0",
    [string]$InnoSetupCompiler = ""
)

$ErrorActionPreference = 'Stop'
$installerName = 'GSTR2TallyConnectorSetup.exe'
$releaseDir = Join-Path $PSScriptRoot 'release'
try { $releaseOrigin = [Uri]$Origin } catch { throw 'Origin must be an absolute HTTPS URL.' }
if (-not $releaseOrigin.IsAbsoluteUri -or $releaseOrigin.Scheme -ne 'https' -or
    $releaseOrigin.AbsolutePath -ne '/' -or $releaseOrigin.Query -or
    $releaseOrigin.Fragment -or $releaseOrigin.UserInfo) {
    throw 'Origin must be an absolute HTTPS origin without a path, query, fragment, or credentials.'
}
$normalizedOrigin = $releaseOrigin.GetLeftPart([UriPartial]::Authority)

if (-not $InnoSetupCompiler) {
    $candidates = @(
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
        (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe')
    )
    $InnoSetupCompiler = $candidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
}
if (-not $InnoSetupCompiler -or -not (Test-Path -LiteralPath $InnoSetupCompiler)) {
    throw "Inno Setup 6 compiler (ISCC.exe) was not found. Install Inno Setup 6 on the developer/CI build runner, or pass -InnoSetupCompiler with its full path. Expected locations: C:\Program Files (x86)\Inno Setup 6\ISCC.exe or C:\Program Files\Inno Setup 6\ISCC.exe. No installer was created."
}

# release.json is bundled by the installer, never supplied by a customer.
$releaseConfig = [ordered]@{ origin = $normalizedOrigin } | ConvertTo-Json
Set-Content -LiteralPath (Join-Path $PSScriptRoot 'release.json') -Value $releaseConfig -Encoding UTF8

& $PythonExe -m pip install -r (Join-Path $PSScriptRoot 'requirements-build.txt')
if ($LASTEXITCODE -ne 0) { throw 'Unable to install connector build requirements.' }
& $PythonExe -m PyInstaller --noconfirm --clean --onedir --windowed `
    --paths (Join-Path $PSScriptRoot '..\backend') `
    --name GSTR2TallyConnector --distpath (Join-Path $PSScriptRoot 'dist') `
    --workpath (Join-Path $PSScriptRoot 'build') `
    --specpath $PSScriptRoot (Join-Path $PSScriptRoot 'tally_local_agent.py')
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed to build the connector.' }

$connectorExe = Join-Path $PSScriptRoot 'dist\GSTR2TallyConnector\GSTR2TallyConnector.exe'
if (-not (Test-Path -LiteralPath $connectorExe)) { throw "PyInstaller did not create $connectorExe" }
New-Item -ItemType Directory -Path $releaseDir -Force | Out-Null
$installer = Join-Path $releaseDir $installerName
if (Test-Path -LiteralPath $installer) { Remove-Item -LiteralPath $installer -Force }
& $InnoSetupCompiler "/DMyAppVersion=$Version" "/O$releaseDir" "/F$([IO.Path]::GetFileNameWithoutExtension($installerName))" (Join-Path $PSScriptRoot 'GSTR2TallyConnector.iss')
if ($LASTEXITCODE -ne 0) { throw 'Inno Setup compilation failed.' }
if (-not (Test-Path -LiteralPath $installer)) { throw "Inno Setup did not create $installer" }
if ((Get-Item -LiteralPath $installer).Length -lt 1MB) { throw "Generated installer is unexpectedly small: $installer" }
$bundledOrigin = (Get-Content -LiteralPath (Join-Path $PSScriptRoot 'release.json') -Raw | ConvertFrom-Json).origin
if ($bundledOrigin -ne $normalizedOrigin -or $bundledOrigin -match 'example\.com|200\.141\.7\.59') { throw 'Production release.json validation failed.' }
Write-Host "Genuine installer created: $installer ($((Get-Item -LiteralPath $installer).Length) bytes)"
