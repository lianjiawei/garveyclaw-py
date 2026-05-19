$ErrorActionPreference = "Stop"

$RepoUrl = if ($env:WECLAW_REPO_URL) { $env:WECLAW_REPO_URL } else { "https://github.com/lianjiawei/weclaw.git" }
$Branch = if ($env:WECLAW_BRANCH) { $env:WECLAW_BRANCH } else { "master" }
$InstallDir = if ($env:WECLAW_INSTALL_DIR) { $env:WECLAW_INSTALL_DIR } else { Join-Path $env:LOCALAPPDATA "WeClaw\weclaw" }
$BinDir = if ($env:WECLAW_BIN_DIR) { $env:WECLAW_BIN_DIR } else { Join-Path $env:USERPROFILE ".weclaw\bin" }

function Write-Step {
    param([string]$Message)
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Warn {
    param([string]$Message)
    Write-Host "Warning: $Message" -ForegroundColor Yellow
}

function Fail {
    param([string]$Message)
    Write-Host "Error: $Message" -ForegroundColor Red
    exit 1
}

function Resolve-Python {
    if ($env:PYTHON) {
        $candidate = Get-Command $env:PYTHON -ErrorAction SilentlyContinue
        if (-not $candidate) {
            Fail "PYTHON=$env:PYTHON was not found."
        }
        return [pscustomobject]@{ Exe = $candidate.Source; Args = @() }
    }

    $candidates = @(
        @{ Command = "py"; Args = @("-3.12") },
        @{ Command = "python"; Args = @() },
        @{ Command = "python3"; Args = @() }
    )

    foreach ($item in $candidates) {
        $command = Get-Command $item.Command -ErrorAction SilentlyContinue
        if (-not $command) {
            continue
        }
        $versionCheck = @"
import sys
raise SystemExit(0 if sys.version_info >= (3, 12) else 1)
"@
        $process = Start-Process -FilePath $command.Source -ArgumentList ($item.Args + @("-c", $versionCheck)) -Wait -PassThru -NoNewWindow
        if ($process.ExitCode -eq 0) {
            return [pscustomobject]@{ Exe = $command.Source; Args = $item.Args }
        }
    }

    Fail "Python 3.12+ is required. Install Python 3.12 first, or set `$env:PYTHON."
}

function Invoke-Python {
    param(
        [object]$PythonCommand,
        [string[]]$Arguments
    )
    & $PythonCommand.Exe @($PythonCommand.Args) @Arguments
}

function Ensure-Git {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Fail "git is required. Install Git for Windows first."
    }
}

function Install-Ffmpeg {
    if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
        Write-Step "ffmpeg is already installed"
        return
    }

    Write-Step "Installing ffmpeg for local voice transcription"
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install --id Gyan.FFmpeg --exact --silent --accept-package-agreements --accept-source-agreements
    } elseif (Get-Command choco -ErrorAction SilentlyContinue) {
        choco install ffmpeg -y
    } else {
        Write-Warn "ffmpeg was not found and neither winget nor choco is available. Voice transcription needs ffmpeg; install it manually if voice messages fail."
        return
    }

    if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
        Write-Warn "ffmpeg installation finished, but ffmpeg is still not on PATH. Open a new PowerShell window, or install ffmpeg manually if voice messages fail."
    }
}

function Install-Repo {
    $parent = Split-Path -Parent $InstallDir
    New-Item -ItemType Directory -Force -Path $parent | Out-Null

    if (Test-Path (Join-Path $InstallDir ".git")) {
        Write-Step "Updating WeClaw at $InstallDir"
        git -C $InstallDir fetch origin $Branch
        git -C $InstallDir checkout $Branch
        git -C $InstallDir pull --ff-only origin $Branch
    } elseif (Test-Path $InstallDir) {
        Fail "$InstallDir already exists but is not a git repository. Set WECLAW_INSTALL_DIR to another path."
    } else {
        Write-Step "Cloning WeClaw into $InstallDir"
        git clone --branch $Branch $RepoUrl $InstallDir
    }
}

function Install-PythonEnvironment {
    param([object]$PythonCommand)
    Write-Step "Preparing Python environment"
    Invoke-Python $PythonCommand @("-m", "venv", (Join-Path $InstallDir ".venv"))
    $venvPython = Join-Path $InstallDir ".venv\Scripts\python.exe"
    & $venvPython -m pip install --upgrade pip
    & $venvPython -m pip install -e $InstallDir
}

function Build-CoreDashboard {
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        Write-Warn "npm was not found. /core dashboard will be built later if npm is installed."
        return
    }
    $coreDir = Join-Path $InstallDir "pixel-office-core"
    if (-not (Test-Path (Join-Path $coreDir "package.json"))) {
        return
    }
    Write-Step "Building pixel-office-core dashboard"
    Push-Location $coreDir
    try {
        if (Test-Path "package-lock.json") {
            npm ci
        } else {
            npm install
        }
        npm run build
    } finally {
        Pop-Location
    }
}

function Write-CmdWrapper {
    param([string]$Name)
    Write-Warn "Write-CmdWrapper is deprecated; use scripts\install-wrappers.ps1."
}

function Install-Wrappers {
    Write-Step "Installing command wrappers into $BinDir"
    $env:WECLAW_INSTALL_DIR = $InstallDir
    $env:WECLAW_BIN_DIR = $BinDir
    & (Join-Path $InstallDir "scripts\install-wrappers.ps1")
}

function Ensure-UserPath {
    $currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if (($currentPath -split ";") -contains $BinDir) {
        return
    }
    $newPath = if ([string]::IsNullOrWhiteSpace($currentPath)) { $BinDir } else { "$currentPath;$BinDir" }
    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
    $env:Path = "$env:Path;$BinDir"
    Write-Warn "Added $BinDir to the user PATH. Open a new PowerShell window if commands are not found."
}

function Main {
    Ensure-Git
    $pythonCommand = Resolve-Python
    Write-Step "Using Python: $($pythonCommand.Exe) $($pythonCommand.Args -join ' ')"
    Install-Repo
    Install-Ffmpeg
    Install-PythonEnvironment $pythonCommand
    Build-CoreDashboard
    Install-Wrappers
    Ensure-UserPath

    Write-Host ""
    Write-Step "WeClaw installed successfully"
    Write-Host ""
    Write-Host "Next steps:"
    Write-Host "  weclaw setup"
    Write-Host "  weclaw doctor"
    Write-Host "  weclaw model list"
    Write-Host "  weclaw channel setup telegram   # optional, configure later"
    Write-Host "  weclaw run     # foreground mode on Windows"
    Write-Host "  weclaw start   # background mode on Linux/macOS/WSL2"
    Write-Host ""
    Write-Host "If you are inside the source directory, this also works:"
    Write-Host "  cd `"$InstallDir`"; python -m weclaw doctor"
    Write-Host ""
    Write-Host "Install path:"
    Write-Host "  $InstallDir"
}

Main
