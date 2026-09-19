param(
    [Parameter(Mandatory = $true)][string]$Origin,
    [string]$Version = "1.0.0",
    [string]$PythonExe = "python",
    [string]$InnoSetupCompiler = ""
)

# Backward-compatible CI entry point. The complete build now lives in
# build-exe.ps1 and always writes local-tally-agent\release\.
& (Join-Path $PSScriptRoot 'build-exe.ps1') @PSBoundParameters
exit $LASTEXITCODE
