param(
    [int]$Port = 8765,
    [switch]$SkipBuild,
    [switch]$StopExisting,
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"
$OutputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$CoreRoot = Join-Path $RepoRoot "pixel-office-core"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Assert-Path {
    param(
        [string]$Path,
        [string]$Message
    )
    if (-not (Test-Path $Path)) {
        throw $Message
    }
}

Set-Location $RepoRoot

Assert-Path $CoreRoot "pixel-office-core directory not found: $CoreRoot"

if ([string]::IsNullOrWhiteSpace($PythonPath)) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCommand) {
        throw "Python was not found. Activate your Python environment first or pass -PythonPath."
    }
    $PythonPath = $pythonCommand.Source
}
elseif (-not (Test-Path $PythonPath)) {
    $pythonCommand = Get-Command $PythonPath -ErrorAction SilentlyContinue
    if (-not $pythonCommand) {
        throw "Python not found: $PythonPath. Activate your Python environment first or pass -PythonPath."
    }
    $PythonPath = $pythonCommand.Source
}

Write-Step "Using Python"
Write-Host $PythonPath
& $PythonPath --version

if (-not $SkipBuild) {
    Write-Step "Building pixel-office-core"
    Push-Location $CoreRoot
    try {
        if (-not (Test-Path "node_modules")) {
            Write-Host "node_modules not found; running npm install first."
            npm install
        }
        npm run build
    }
    finally {
        Pop-Location
    }
}
else {
    Write-Step "Skipping pixel-office-core build"
}

$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($listener) {
    if ($StopExisting) {
        Write-Host ""
        Write-Host "Stopping existing process $($listener.OwningProcess) on port $Port." -ForegroundColor Yellow
        Stop-Process -Id $listener.OwningProcess
        Start-Sleep -Seconds 1
        $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($listener) {
            throw "Port $Port is still in use by process $($listener.OwningProcess)."
        }
    }
    else {
    Write-Host ""
    Write-Host "Port $Port is already used by process $($listener.OwningProcess)." -ForegroundColor Yellow
    Write-Host "If this is an old dashboard, close it first or use another port, for example:"
    Write-Host "  .\scripts\dev-core-dashboard.ps1 -Port 8766"
    Write-Host "Or stop the existing listener automatically:"
    Write-Host "  .\scripts\dev-core-dashboard.ps1 -StopExisting"
    exit 1
    }
}

$env:WECLAW_DASHBOARD_PORT = [string]$Port

Write-Step "Starting WeClaw Dashboard"
Write-Host "Classic: http://127.0.0.1:$Port/"
Write-Host "V2:      http://127.0.0.1:$Port/v2/"
Write-Host "Core:    http://127.0.0.1:$Port/core/"
Write-Host ""
Write-Host "Press Ctrl+C to stop the server."
Write-Host ""

& $PythonPath -m weclaw.monitor.server
