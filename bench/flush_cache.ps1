# Makes the next launch cold without a reboot: empties the standby list, modified list
# and working sets with Sysinternals RAMMap. Self-elevates (UAC prompt).
# Verify once against a real reboot: cold-after-flush should match cold-after-reboot.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$rammap = Join-Path $root "tools\RAMMap64.exe"
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$isAdmin = (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Start-Process powershell -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$($MyInvocation.MyCommand.Path)`"" -Wait
    exit
}
$before = (Get-Counter '\Memory\Standby Cache Normal Priority Bytes').CounterSamples[0].CookedValue
foreach ($flag in "-Ew", "-Em", "-Es", "-Et", "-E0") {
    Start-Process $rammap -ArgumentList "/accepteula $flag" -Wait -WindowStyle Hidden
}
Start-Sleep 3
$after = (Get-Counter '\Memory\Standby Cache Normal Priority Bytes').CounterSamples[0].CookedValue
Write-Host ("standby cache: {0:N0} MB -> {1:N0} MB" -f ($before / 1MB), ($after / 1MB))
