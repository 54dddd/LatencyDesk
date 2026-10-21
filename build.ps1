$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
        py -3.12 -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw '创建环境失败，请安装 Python 3.12 x64（包含 Tkinter）。' }
    }
    & '.venv/Scripts/python.exe' -m pip install -r requirements-build.txt
    if ($LASTEXITCODE -ne 0) { throw '安装构建依赖失败。' }
    & '.venv/Scripts/python.exe' -m unittest -v test_diagnostics
    if ($LASTEXITCODE -ne 0) { throw '测试失败，未生成 EXE。' }
    $icon = (Resolve-Path 'latency.ico').Path
    $version = (Resolve-Path 'version_info.txt').Path
    & '.venv/Scripts/python.exe' -m PyInstaller --noconfirm --clean --onefile --windowed --name '网络延时助手' --icon $icon --add-data ($icon + ';.') --version-file $version --distpath dist --workpath build --specpath build latency_desk.py
    if ($LASTEXITCODE -ne 0) { throw '打包失败。' }
    Write-Host '完成：dist/网络延时助手.exe'
} finally {
    Pop-Location
}
