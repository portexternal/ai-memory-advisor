# Records every file read cs2.exe performs from launch to quit, using Process Monitor.
# Self-elevates (UAC prompt). Flow: procmon starts -> you launch CS2 -> reach main menu ->
# wait 10 s -> quit CS2 -> script stops procmon and exports data\procmon\cs2.csv.
# Then run: python bench\parse_procmon.py
param([string]$Exe = "cs2.exe", [switch]$Flush, [string]$Name = "cs2")
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$procmon = Join-Path $root "tools\Procmon64.exe"
$outDir = Join-Path $root "data\procmon"
$pml = Join-Path $outDir "$Name.pml"
$csv = Join-Path $outDir "$Name.csv"

$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$isAdmin = (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Start-Process powershell -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$($MyInvocation.MyCommand.Path)`" -Exe $Exe -Name $Name$(if ($Flush) { ' -Flush' })"
    exit
}

New-Item -ItemType Directory -Force $outDir | Out-Null
Remove-Item $pml, $csv -ErrorAction SilentlyContinue
if (Get-Process -Name $Exe.Replace(".exe", "") -ErrorAction SilentlyContinue) { throw "$Exe already running, quit it first" }

if ($Flush) {
    $rammap = Join-Path $root "tools\RAMMap64.exe"
    foreach ($flag in "-Ew", "-Em", "-Es", "-Et", "-E0") { Start-Process $rammap -ArgumentList "/accepteula $flag" -Wait -WindowStyle Hidden }
    Start-Sleep 3
    Write-Host ("cache flushed, standby now {0:N0} MB" -f ((Get-Counter '\Memory\Standby Cache Normal Priority Bytes').CounterSamples[0].CookedValue / 1MB))
}
Write-Host "starting Process Monitor (backing file $pml)"
Start-Process $procmon -ArgumentList "/AcceptEula /Quiet /Minimized /NoFilter /BackingFile `"$pml`""
Start-Sleep 4
Write-Host ""
Write-Host ">>> LAUNCH $Exe NOW. Reach the main menu, wait 10 s, then QUIT the game. <<<" -ForegroundColor Yellow
$name = $Exe.Replace(".exe", "")
while (-not (Get-Process -Name $name -ErrorAction SilentlyContinue)) { Start-Sleep -Milliseconds 200 }
Write-Host "$Exe started, recording..."
while (Get-Process -Name $name -ErrorAction SilentlyContinue) { Start-Sleep 1 }
Write-Host "$Exe exited, stopping Process Monitor"
Start-Process $procmon -ArgumentList "/Terminate" -Wait
Start-Sleep 2
Write-Host "exporting CSV (this takes a minute)..."
Start-Process $procmon -ArgumentList "/AcceptEula /Quiet /OpenLog `"$pml`" /SaveAs `"$csv`"" -Wait
Write-Host ("done: {0} ({1:N0} MB)" -f $csv, ((Get-Item $csv).Length / 1MB))
Write-Host "next: python bench\parse_procmon.py"
Read-Host "press Enter to close"
