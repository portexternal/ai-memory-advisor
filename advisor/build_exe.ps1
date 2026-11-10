# Builds dist\AIMemoryAdvisor.exe (one file, no console window).
# Needs: pip install pyinstaller psutil. Run from anywhere:
#   powershell -ExecutionPolicy Bypass -File advisor\build_exe.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
python -m PyInstaller --noconfirm --clean --onefile --noconsole `
    --name AIMemoryAdvisor `
    --paths $root `
    --distpath "$root\dist" --workpath "$root\build" --specpath "$root\build" `
    advisor\app.py
Get-Item "$root\dist\AIMemoryAdvisor.exe" | Select-Object Name, Length, LastWriteTime
