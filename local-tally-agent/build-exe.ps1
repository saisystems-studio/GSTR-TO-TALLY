param([string]$PythonExe = "python", [string]$Origin = "https://gstr2tally.example.com")

$ErrorActionPreference = 'Stop'
& $PythonExe -m pip install -r "$PSScriptRoot\requirements-build.txt"
Set-Content -LiteralPath "$PSScriptRoot\release.json" -Value (ConvertTo-Json @{ origin = $Origin }) -Encoding UTF8
& $PythonExe -m PyInstaller --noconfirm --clean --onedir --windowed --paths "$PSScriptRoot\..\backend" --name GSTR2TallyConnector --distpath "$PSScriptRoot\dist" "$PSScriptRoot\tally_local_agent.py"
Write-Host "Built $PSScriptRoot\dist\GSTR2TallyConnector\GSTR2TallyConnector.exe"
