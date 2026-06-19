# Build the single-file Windows .exe.  Run from the repo root:  .\packaging\build_exe.ps1
# Output: dist\dna-entropy.exe  (lightweight client — torch/evo2 run in the cloud, not here)
$ErrorActionPreference = "Stop"

.\.venv\Scripts\python.exe -m pip install --quiet pyinstaller

.\.venv\Scripts\python.exe -m PyInstaller --onefile --name dna-entropy --console `
    --exclude-module torch --exclude-module evo2 --exclude-module flash_attn --exclude-module pyrodigal `
    --add-data "src/dna_entropy;_pkgsrc" `
    --noconfirm packaging\launcher.py

Write-Host ""
Write-Host "Built: dist\dna-entropy.exe" -ForegroundColor Green
Write-Host "Distribute via a GitHub Release (do not commit the .exe)." -ForegroundColor Cyan
