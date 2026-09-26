param(
    [int]$Port = 4381,
    [string]$Source = '',
    [switch]$NoBrowser
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
try {
    $runningApp = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 2
    if ($runningApp.service -eq 'research-swarm') {
        if (-not $NoBrowser) { Start-Process "http://127.0.0.1:$Port" }
        return
    }
} catch { }
& python -c "import pypdf"
if ($LASTEXITCODE -ne 0) {
    & python -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'PDF 读取依赖安装失败' }
}
if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'dist\index.html'))) {
    & npm.cmd ci
    if ($LASTEXITCODE -ne 0) { throw '依赖安装失败' }
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw '界面构建失败' }
}
$env:PYTHONIOENCODING = 'utf-8'
if (-not $NoBrowser) { Start-Process "http://127.0.0.1:$Port" }
if ($Source) { & python -X utf8 -m research_swarm --source $Source --port $Port }
else { & python -X utf8 -m research_swarm --port $Port }
