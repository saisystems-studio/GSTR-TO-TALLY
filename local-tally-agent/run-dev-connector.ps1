# Developer only: run the connector FROM SOURCE against the LOCAL backend and web app (never production).
param(
    [string]$Backend = 'http://127.0.0.1:8000',
    [string]$Web = 'http://localhost:5173/login',
    [string]$PythonExe = (Join-Path $PSScriptRoot '..\backend\.venv\Scripts\python.exe')
)
$env:GSTR_CONNECTOR_DEV_ORIGIN = $Backend
$env:GSTR_CONNECTOR_DEV_WEB_URL = $Web
& $PythonExe (Join-Path $PSScriptRoot 'tally_local_agent.py')
