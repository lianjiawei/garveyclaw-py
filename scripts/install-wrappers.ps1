$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$DefaultInstallDir = Resolve-Path (Join-Path $ScriptDir "..")
$InstallDir = if ($env:WECLAW_INSTALL_DIR) { $env:WECLAW_INSTALL_DIR } else { $DefaultInstallDir.Path }
$BinDir = if ($env:WECLAW_BIN_DIR) { $env:WECLAW_BIN_DIR } else { Join-Path $env:USERPROFILE ".weclaw\bin" }

function Write-Step {
    param([string]$Message)
    Write-Host "[weclaw] $Message"
}

function Fail {
    param([string]$Message)
    Write-Error "[weclaw] ERROR: $Message"
    exit 1
}

function Write-CmdWrapper {
    param([string]$Name)
    New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
    $exe = Join-Path $InstallDir ".venv\Scripts\$Name.exe"
    if (-not (Test-Path $exe)) {
        Fail "Missing executable: $exe. Run: cd `"$InstallDir`"; python -m pip install -e ."
    }
    $target = Join-Path $BinDir "$Name.cmd"
    $script = "@echo off`r`nset WECLAW_INSTALL_DIR=$InstallDir`r`ncd /d `"$InstallDir`"`r`n`"$exe`" %*`r`n"
    Set-Content -Path $target -Value $script -Encoding ASCII
    Write-Step "Installed $target -> $exe"
}

Write-CmdWrapper "weclaw"
Write-CmdWrapper "weclaw-tui"
Write-CmdWrapper "weclaw-dashboard"
Write-CmdWrapper "weclaw-feishu"
Write-CmdWrapper "weclaw-weixin"

Write-Step "Command wrappers installed in $BinDir"
