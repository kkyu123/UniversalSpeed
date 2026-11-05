# 打包 UniversalSpeed（窗口版 exe）
# 依赖：Python 3.9+、pip install pyinstaller
# 产物：dist\UniversalSpeed\UniversalSpeed.exe + _internal\

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

python -m pip install --upgrade pyinstaller
python -m PyInstaller --noconfirm --onedir --windowed --name UniversalSpeed UniversalSpeedGUI.py

Write-Host ''
Write-Host '打包完成：dist\UniversalSpeed\' -ForegroundColor Green
Write-Host '把 dist\UniversalSpeed\ 整个目录拷出去即可运行。' -ForegroundColor Green
