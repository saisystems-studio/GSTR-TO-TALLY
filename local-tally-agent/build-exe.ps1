param(
    [string]$PythonExe = "python",
    [Parameter(Mandatory = $true)][string]$Origin
)

$ErrorActionPreference = 'Stop'
try { $releaseOrigin = [Uri]$Origin } catch { throw 'Origin must be an absolute HTTPS URL.' }
if (-not $releaseOrigin.IsAbsoluteUri -or $releaseOrigin.Scheme -ne 'https' -or $releaseOrigin.AbsolutePath -ne '/' -or $releaseOrigin.Query -or $releaseOrigin.Fragment -or $releaseOrigin.UserInfo) {
    throw 'Origin must be an absolute HTTPS origin without a path, query, fragment, or credentials.'
}
& $PythonExe -m pip install -r "$PSScriptRoot\requirements-build.txt"
Set-Content -LiteralPath "$PSScriptRoot\release.json" -Value (ConvertTo-Json @{ origin = $releaseOrigin.GetLeftPart([UriPartial]::Authority) }) -Encoding UTF8
& $PythonExe -m PyInstaller --noconfirm --clean --onedir --windowed --paths "$PSScriptRoot\..\backend" --name GSTR2TallyConnector --distpath "$PSScriptRoot\dist" "$PSScriptRoot\tally_local_agent.py"
Write-Host "Built $PSScriptRoot\dist\GSTR2TallyConnector\GSTR2TallyConnector.exe"
