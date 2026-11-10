# Registers two Task Scheduler tasks for the current user (no admin needed):
#   ThesisBehaviorLogger  - at logon, run the logger silently with pythonw.exe
#   ThesisDashboard       - at logon (+delay), open dashboard/index.html in the default browser
# Re-run to update. Remove with uninstall_startup.ps1.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$config = Get-Content (Join-Path $root "config.json") -Raw | ConvertFrom-Json
$pythonw = Join-Path (Split-Path -Parent (Get-Command python).Source) "pythonw.exe"
if (-not (Test-Path $pythonw)) { throw "pythonw.exe not found next to python.exe" }

$loggerScript = Join-Path $root "logger\behavior_logger.py"
$dashboard = Join-Path $root "dashboard\index.html"
$user = "$env:USERDOMAIN\$env:USERNAME"

$loggerAction = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$loggerScript`"" -WorkingDirectory $root
$loggerTrigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "ThesisBehaviorLogger" -Action $loggerAction -Trigger $loggerTrigger -Settings $settings -Description "Thesis: behavior logger (user-mode, JSONL)" -Force | Out-Null
Write-Host "registered ThesisBehaviorLogger -> $pythonw $loggerScript"

if ($config.open_dashboard_at_logon) {
    $delay = [TimeSpan]::FromSeconds([int]$config.dashboard_logon_delay_seconds)
    $dashTrigger = New-ScheduledTaskTrigger -AtLogOn -User $user
    $dashTrigger.Delay = [System.Xml.XmlConvert]::ToString($delay)
    $dashAction = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c start `"`" `"$dashboard`""
    $dashSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
    Register-ScheduledTask -TaskName "ThesisDashboard" -Action $dashAction -Trigger $dashTrigger -Settings $dashSettings -Description "Thesis: open behavior dashboard at logon" -Force | Out-Null
    Write-Host "registered ThesisDashboard -> opens $dashboard $($delay.TotalSeconds)s after logon"
} else {
    Unregister-ScheduledTask -TaskName "ThesisDashboard" -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "open_dashboard_at_logon=false, ThesisDashboard not registered"
}

$predictorScript = Join-Path $root "predictor\daemon.py"
$predAction = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$predictorScript`"" -WorkingDirectory $root
$predTrigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$predTrigger.Delay = "PT1M"
Register-ScheduledTask -TaskName "ThesisPredictor" -Action $predAction -Trigger $predTrigger -Settings $settings -Description "Thesis: next-app predictor, fires prefetch" -Force | Out-Null
Write-Host "registered ThesisPredictor -> $pythonw $predictorScript (1 min after logon)"

# Start the logger and predictor right now so today's log begins immediately.
Start-ScheduledTask -TaskName "ThesisBehaviorLogger"
Start-ScheduledTask -TaskName "ThesisPredictor"
Write-Host "logger started. Logs: $root\data\logs  Dashboard: $dashboard"
