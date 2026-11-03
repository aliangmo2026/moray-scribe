# moray-scribe 一键安装（Windows PowerShell）。
#
#   powershell -ExecutionPolicy Bypass -File install.ps1
#
# 四步：找 Python >= 3.10 → 建独立 venv → 装 moray-scribe → 检查 Ollama。
# 环境变量/参数均可覆盖；不动系统 Python，不写系统目录。
#
# 参数（等价于 Linux 版的环境变量）：
#   -Version        版本约束（默认 ">=0.5"）
#   -PipIndex       pip 源（默认 https://pypi.org/simple；国内推荐
#                   https://pypi.tuna.tsinghua.edu.cn/simple）
#   -MorayHome      安装根目录（默认 ~\.moray-scribe）
#   -Python         显式指定解释器路径（如 D:\python3.12\python.exe）
#   -SkipOllamaCheck 跳过 Ollama 检查
param(
    [string]$Version = ">=0.5",
    [string]$PipIndex = "https://pypi.org/simple",
    [string]$MorayHome = "$env:USERPROFILE\.moray-scribe",
    [string]$Python = "",
    [switch]$SkipOllamaCheck
)

$ErrorActionPreference = "Stop"

function Write-MorayLog([string]$Message) {
    Write-Host "[moray-scribe] $Message" -ForegroundColor Cyan
}
function Write-MorayWarn([string]$Message) {
    Write-Host "[moray-scribe] $Message" -ForegroundColor Yellow
}

# --- 1. find python >= 3.10 -------------------------------------------------
$ResolvedPython = ""
if ($Python -and (Test-Path $Python)) {
    $ResolvedPython = $Python
}
if (-not $ResolvedPython) {
    foreach ($candidate in @("python3.12", "python3.11", "python3.10", "python")) {
        $command = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($null -eq $command) { continue }
        $ok = & $candidate -c "import sys; print(1 if sys.version_info >= (3, 10) else 0)" 2>$null
        if ("$ok".Trim() -eq "1") {
            $ResolvedPython = $candidate
            break
        }
    }
}
if (-not $ResolvedPython) {
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($null -ne $pyLauncher) {
        $ok = & py -c "import sys; print(1 if sys.version_info >= (3, 10) else 0)" 2>$null
        if ("$ok".Trim() -eq "1") { $ResolvedPython = "py" }
    }
}
if (-not $ResolvedPython) {
    Write-MorayWarn "没找到 Python >= 3.10。"
    Write-MorayWarn "  winget install Python.Python.3.12"
    Write-MorayWarn "  或用 -Python 参数显式指定解释器，或用 Docker："
    Write-MorayWarn "  docker run --rm -p 8000:8000 -v `"${PWD}:/work:ro`" moray-scribe"
    exit 1
}
$pyVersion = & $ResolvedPython -c "import sys; print('.'.join(map(str, sys.version_info[:3])))"
Write-MorayLog "使用 Python: $ResolvedPython ($pyVersion)"

# --- 2. venv ------------------------------------------------------------------
New-Item -ItemType Directory -Force -Path $MorayHome | Out-Null
$VenvDir = Join-Path $MorayHome "venv"
$venvPython = Join-Path $VenvDir "Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-MorayLog "创建虚拟环境: $VenvDir"
    & $ResolvedPython -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) {
        Write-MorayWarn "venv 创建失败。"
        exit 1
    }
}

$Pip = Join-Path $VenvDir "Scripts\pip.exe"
$MorayBin = Join-Path $VenvDir "Scripts\moray-scribe.exe"

# --- 3. install ---------------------------------------------------------------
Write-MorayLog "安装 moray-scribe $Version (index: $PipIndex)"
& $Pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { Write-MorayWarn "pip 升级失败，继续尝试安装"; }

$Wheel = Get-ChildItem -Path "dist" -Filter "moray_scribe-*.whl" -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($null -ne $Wheel) {
    Write-MorayLog "发现本地 wheel，优先使用: $($Wheel.Name)"
    & $Pip install $Wheel.FullName --index-url $PipIndex
} else {
    & $Pip install "moray-scribe$Version" --index-url $PipIndex
}
if ($LASTEXITCODE -ne 0) {
    Write-MorayWarn "安装失败：检查网络，或 -PipIndex https://pypi.tuna.tsinghua.edu.cn/simple"
    exit 1
}

& $MorayBin --version
if ($LASTEXITCODE -ne 0) {
    Write-MorayWarn "安装后无法运行 moray-scribe --version"
    exit 1
}

# --- 4. Ollama check (advisory) -------------------------------------------------
if (-not $SkipOllamaCheck) {
    $ollamaUrl = if ($env:OLLAMA_HOST) { $env:OLLAMA_HOST } else { "http://127.0.0.1:11434" }
    $found = $false
    try {
        Invoke-WebRequest -Uri "$ollamaUrl/api/tags" -TimeoutSec 3 -UseBasicParsing | Out-Null
        $found = $true
    } catch {
        $found = $false
    }
    if ($found) {
        Write-MorayLog "检测到 Ollama 在运行 ✓ （doc / 图谱问答可用）"
    } else {
        Write-MorayWarn "没检测到 Ollama（$ollamaUrl）。doc 模式和图谱问答需要它："
        Write-MorayWarn "  安装与模型拉取方法见 ollama.com/download，然后: ollama pull qwen2.5:7b"
        Write-MorayWarn "静态分析（scan/graph）与拓扑查看器完全不需要 Ollama。"
    }
}

Write-MorayLog "安装完成 ✓  试试："
Write-MorayLog "  $MorayBin graph .\your-project --out out\"
Write-MorayLog "  $MorayBin doc .\your-project --out out\      # 需要 Ollama"
Write-MorayLog "  $MorayBin check . --baseline <基线.json> --fail-on none"
Write-MorayLog "  拓扑查看器：python web_viewer/serve.py out\pruned-graph.json"
