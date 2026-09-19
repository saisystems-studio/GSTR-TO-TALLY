param(
    [Parameter(Mandatory = $true)][string]$Origin,
    [string]$Version = "1.0.0",
    [string]$PythonExe = "python",
    [string]$InnoSetupCompiler = ""
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$installerName = 'GSTR2TallyConnectorSetup.exe'

if (-not $InnoSetupCompiler) {
    $candidates = @(
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
        (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe')
    )
    $InnoSetupCompiler = $candidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
}
if (-not $InnoSetupCompiler -or -not (Test-Path -LiteralPath $InnoSetupCompiler)) {
    throw 'Inno Setup 6 is required on the developer or CI release runner.'
}

# This is a developer/CI-only operation. It embeds the production HTTPS
# origin, compiles the installer, and stages exactly the file Nginx serves.
& (Join-Path $PSScriptRoot 'build-exe.ps1') -PythonExe $PythonExe -Origin $Origin
& $InnoSetupCompiler "/DMyAppVersion=$Version" "/O$PSScriptRoot\out" (Join-Path $PSScriptRoot 'GSTR2TallyConnector.iss')
if ($LASTEXITCODE -ne 0) { throw 'Inno Setup compilation failed.' }

$installer = Join-Path $PSScriptRoot "out\$installerName"
if (-not (Test-Path -LiteralPath $installer)) { throw "Installer was not generated: $installer" }
$staging = Join-Path $root 'deploy\downloads'
New-Item -ItemType Directory -Force -Path $staging | Out-Null
Copy-Item -LiteralPath $installer -Destination (Join-Path $staging $installerName) -Force
Write-Host "Release installer staged at $(Join-Path $staging $installerName)"
