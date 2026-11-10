# End-of-week: yesterday's daily report, the weekly report, rebuilt dashboard, then open the weekly report.
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
& python (Join-Path $root "reports\daily_report.py")
& python (Join-Path $root "reports\weekly_report.py")
& python (Join-Path $root "dashboard\build_dashboard.py")
$latest = Get-ChildItem (Join-Path $root "reports\weekly\*.md") | Sort-Object LastWriteTime | Select-Object -Last 1
Start-Process $latest.FullName
