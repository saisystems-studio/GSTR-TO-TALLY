param(
    [Parameter(Mandatory = $true)][string]$AgentExecutable
)

$taskName = 'GSTR2TallyLocalAgent'
$action = New-ScheduledTaskAction -Execute $AgentExecutable
$trigger = New-ScheduledTaskTrigger -AtLogOn
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Description 'Secure outbound connector for local TallyPrime' -Force
Write-Host "Installed $taskName. Start it now from Task Scheduler or sign out and back in."
