<#
.SYNOPSIS
    Installs OffgridCloud on Windows.

.DESCRIPTION
    Sets up the Python virtual environment, builds the React frontend, ensures
    rclone is available, writes a .env (with a generated secret), and optionally
    registers a Scheduled Task so the server starts at boot.

    Run from the repository root (or anywhere):
        powershell -ExecutionPolicy Bypass -File deploy\install.ps1
        powershell -ExecutionPolicy Bypass -File deploy\install.ps1 -InstallService

.PARAMETER InstallService
    Register a Scheduled Task "OffgridCloud" that runs the server at startup
    (requires an elevated/admin PowerShell).

.PARAMETER Port
    Port to serve on (default 8000). Stored for the run script.
#>
[CmdletBinding()]
param(
    [switch]$InstallService,
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

function Have($name) { return [bool](Get-Command $name -ErrorAction SilentlyContinue) }

function Ensure-Tool($command, $wingetId, $label) {
    if (Have $command) { return }
    Write-Host ">> $label not found." -ForegroundColor Yellow
    if (Have "winget") {
        Write-Host ">> Installing $label via winget..."
        winget install --id $wingetId -e --accept-source-agreements --accept-package-agreements
    } else {
        throw "$label is required but not installed, and winget is unavailable. Install $label and re-run."
    }
}

Write-Host "== OffgridCloud Windows installer ==" -ForegroundColor Cyan
Write-Host ">> Install root: $Root"

# --- Prerequisites --------------------------------------------------------
Ensure-Tool "python" "Python.Python.3.12" "Python 3"
Ensure-Tool "node"   "OpenJS.NodeJS.LTS"  "Node.js"
if (-not (Have "rclone")) {
    Ensure-Tool "rclone" "Rclone.Rclone" "rclone"
}

# Refresh PATH for this session in case winget just installed tools.
$env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
            [System.Environment]::GetEnvironmentVariable("Path", "User")

# --- Frontend build -------------------------------------------------------
Write-Host ">> Building frontend..."
Push-Location (Join-Path $Root "frontend")
npm install
npm run build
Pop-Location

$staticDir = Join-Path $Root "backend\app\static"
if (Test-Path $staticDir) { Remove-Item -Recurse -Force $staticDir }
Copy-Item -Recurse (Join-Path $Root "frontend\dist") $staticDir

# --- Python venv ----------------------------------------------------------
Write-Host ">> Creating Python virtual environment..."
$venv = Join-Path $Root "backend\.venv"
if (-not (Test-Path $venv)) { python -m venv $venv }
$py = Join-Path $venv "Scripts\python.exe"
& $py -m pip install --upgrade pip
& $py -m pip install -r (Join-Path $Root "backend\requirements.txt")

# --- .env (generate a secret on first install) ----------------------------
$envFile = Join-Path $Root ".env"
$generatedPassword = $null
if (-not (Test-Path $envFile)) {
    Write-Host ">> Writing .env with a generated secret key + admin password..."
    $bytes = New-Object byte[] 48
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $secret = [Convert]::ToBase64String($bytes)
    $pwBytes = New-Object byte[] 12
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($pwBytes)
    $generatedPassword = [Convert]::ToBase64String($pwBytes) -replace '[+/=]', ''
    $buffer = (Join-Path $Root "data\buffer") -replace '\\', '/'
    @(
        "OGC_SECRET_KEY=$secret"
        "OGC_INITIAL_ADMIN_EMAIL=admin@offgrid.local"
        "OGC_INITIAL_ADMIN_PASSWORD=$generatedPassword"
        "OGC_ENVIRONMENT=production"
        "OGC_DATA_DIR=$(( (Join-Path $Root 'data') -replace '\\','/' ))"
        "OGC_BUFFER_DIR=$buffer"
        "OGC_RCLONE_BINARY=rclone"
    ) | Set-Content -Encoding UTF8 $envFile
} else {
    Write-Host ">> Keeping existing .env"
}

# Persist the chosen port for run.ps1.
[Environment]::SetEnvironmentVariable("OGC_PORT", "$Port", "User")

# --- Optional auto-start service ------------------------------------------
if ($InstallService) {
    Write-Host ">> Registering Scheduled Task 'OffgridCloud' (start at boot)..."
    $runScript = Join-Path $Root "deploy\run.ps1"
    $action = New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$runScript`""
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName "OffgridCloud" -Action $action -Trigger $trigger `
        -Settings $settings -RunLevel Highest -Force | Out-Null
    Start-ScheduledTask -TaskName "OffgridCloud"
    Write-Host "   Service registered and started."
}

Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host "  Edit secrets:   notepad `"$envFile`""
Write-Host "  Start manually: powershell -ExecutionPolicy Bypass -File `"$Root\deploy\run.ps1`""
Write-Host "  Then open:      http://localhost:$Port"
Write-Host ""
if ($generatedPassword) {
    Write-Host "  Admin login (shown only once — save it now):" -ForegroundColor Yellow
    Write-Host "    admin@offgrid.local / $generatedPassword" -ForegroundColor Yellow
    Write-Host "  Change the password after your first login."
} else {
    Write-Host "  Login uses the credentials already in $envFile"
}
