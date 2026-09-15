param([string]$PythonExe = "python")

& $PythonExe -m pip install -r "$PSScriptRoot\requirements-build.txt"
& $PythonExe -m PyInstaller --noconfirm --clean --onefile --windowed --name GSTR2TallyAgent "$PSScriptRoot\tally_local_agent.py"
Write-Host "Built executable: $PSScriptRoot\dist\GSTR2TallyAgent.exe"
