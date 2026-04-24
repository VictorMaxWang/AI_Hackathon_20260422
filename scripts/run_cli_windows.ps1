param(
    [string]$Prompt = "帮我查看当前磁盘使用情况"
)

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $ProjectRoot

& .\.venv\Scripts\python.exe -m app.cli $Prompt
