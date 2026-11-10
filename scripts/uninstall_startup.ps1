# Removes the logon tasks and stops a running logger.
$ErrorActionPreference = "SilentlyContinue"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Stop-ScheduledTask -TaskName "ThesisBehaviorLogger"
Stop-ScheduledTask -TaskName "ThesisPredictor"
Unregister-ScheduledTask -TaskName "ThesisBehaviorLogger" -Confirm:$false
Unregister-ScheduledTask -TaskName "ThesisPredictor" -Confirm:$false
Unregister-ScheduledTask -TaskName "ThesisDashboard" -Confirm:$false
$lock = Join-Path $root "data\logger.lock"
if (Test-Path $lock) {
    $pid_ = Get-Content $lock
    Stop-Process -Id $pid_ -Force
    Remove-Item $lock
}
Write-Host "tasks removed, logger stopped"
