$ErrorActionPreference = "Stop"

$InstallDir = if ($env:WECLAW_INSTALL_DIR) { $env:WECLAW_INSTALL_DIR } else { Join-Path $env:LOCALAPPDATA "WeClaw\weclaw" }
$BinDir = if ($env:WECLAW_BIN_DIR) { $env:WECLAW_BIN_DIR } else { Join-Path $env:USERPROFILE ".weclaw\bin" }
$KeepData = $env:WECLAW_KEEP_DATA -eq "1"

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

function Assert-SafeInstallDir {
    $resolved = [System.IO.Path]::GetFullPath($InstallDir)
    $home = [System.IO.Path]::GetFullPath($env:USERPROFILE)
    $localAppData = [System.IO.Path]::GetFullPath($env:LOCALAPPDATA)
    $root = [System.IO.Path]::GetPathRoot($resolved)
    $broadPaths = @($root, $home, $localAppData, (Join-Path $env:LOCALAPPDATA "WeClaw"), (Join-Path $env:USERPROFILE ".weclaw"))
    foreach ($path in $broadPaths) {
        if ($resolved.TrimEnd("\") -ieq ([System.IO.Path]::GetFullPath($path)).TrimEnd("\")) {
            Fail "Refusing to remove broad directory: $resolved. Set WECLAW_INSTALL_DIR to the exact WeClaw install path."
        }
    }
}

function Remove-PathIfExists {
    param([string]$Path)
    if (Test-Path $Path) {
        Remove-Item -LiteralPath $Path -Recurse -Force
        Write-Host "Removed $Path"
    }
}

function Remove-UserPathEntry {
    param([string]$PathToRemove)
    $currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ([string]::IsNullOrWhiteSpace($currentPath)) {
        return
    }
    $parts = $currentPath -split ";" | Where-Object { $_ -and ($_ -ne $PathToRemove) }
    $newPath = ($parts -join ";")
    if ($newPath -ne $currentPath) {
        [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
        $env:Path = (($env:Path -split ";") | Where-Object { $_ -and ($_ -ne $PathToRemove) }) -join ";"
        Write-Host "Removed $PathToRemove from the user PATH"
    }
}

function Main {
    Write-Step "Uninstalling WeClaw"
    if (-not $KeepData) {
        Assert-SafeInstallDir
    }

    Remove-PathIfExists (Join-Path $BinDir "weclaw.cmd")
    Remove-PathIfExists (Join-Path $BinDir "weclaw-tui.cmd")
    Remove-PathIfExists (Join-Path $BinDir "weclaw-dashboard.cmd")
    Remove-PathIfExists (Join-Path $BinDir "weclaw-feishu.cmd")

    if ($KeepData) {
        Write-Warn "Keeping install directory because WECLAW_KEEP_DATA=1: $InstallDir"
    } else {
        Remove-PathIfExists $InstallDir
        $parent = Split-Path -Parent $InstallDir
        if ((Test-Path $parent) -and -not (Get-ChildItem -LiteralPath $parent -Force -ErrorAction SilentlyContinue)) {
            Remove-Item -LiteralPath $parent -Force
            Write-Host "Removed $parent"
        }
    }

    Remove-UserPathEntry $BinDir

    Write-Host ""
    Write-Step "WeClaw uninstall complete"
    Write-Host "Open a new PowerShell window if stale commands are still visible."
}

Main
