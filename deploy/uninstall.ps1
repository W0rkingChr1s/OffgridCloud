<#
.SYNOPSIS
    Removes an OffgridCloud installation created by deploy\install.ps1.

.DESCRIPTION
    Counterpart to install.ps1. Stops and unregisters the "OffgridCloud"
    Scheduled Task, stops a server still running from this folder, and deletes
    what the installer built: the Python virtual environment, the bundled
    frontend (backend\app\static), the frontend build output and node_modules,
    plus the OGC_PORT user environment variable.

    Data and secrets are KEPT unless -Purge is given: the .env holds
    OGC_SECRET_KEY, without which the provider credentials in the database
    cannot be decrypted.

    Run from anywhere (paths are resolved relative to this script):
        powershell -ExecutionPolicy Bypass -File deploy\uninstall.ps1
        powershell -ExecutionPolicy Bypass -File deploy\uninstall.ps1 -Purge
        powershell -ExecutionPolicy Bypass -File deploy\uninstall.ps1 -DryRun

    Unregistering the Scheduled Task needs an elevated (admin) PowerShell —
    the same shell the installer needed for -InstallService.

.PARAMETER Purge
    Also delete .env and the data folder (database + media buffer). Destructive.

.PARAMETER NoBackup
    Skip the safety backup (database + .env) that -Purge takes first.

.PARAMETER DryRun
    Show what would be removed, change nothing.

.PARAMETER Yes
    Don't ask for confirmation.
#>
[CmdletBinding()]
param(
    [switch]$Purge,
    [switch]$NoBackup,
    [switch]$DryRun,
    [switch]$Yes
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

function Remove-Target($path, $label) {
    if (-not (Test-Path $path)) { return }
    if ($DryRun) { Write-Host "   [dry-run] entfernen: $label ($path)"; return }
    Remove-Item -Recurse -Force $path
    Write-Host "   Entfernt: $label"
}

Write-Host "== OffgridCloud Windows-Deinstallation ==" -ForegroundColor Cyan
Write-Host ">> Installation: $Root"

# --- What is there? -------------------------------------------------------
# try/catch, not just -ErrorAction: on systems without the ScheduledTasks module
# the missing cmdlet itself would be a terminating error ($ErrorActionPreference).
$task = $null
try { $task = Get-ScheduledTask -TaskName "OffgridCloud" -ErrorAction SilentlyContinue } catch { }
$venv     = Join-Path $Root "backend\.venv"
$static   = Join-Path $Root "backend\app\static"
$dist     = Join-Path $Root "frontend\dist"
$modules  = Join-Path $Root "frontend\node_modules"
$envFile  = Join-Path $Root ".env"
$dataDir  = Join-Path $Root "data"
$db       = Join-Path $dataDir "offgridcloud.db"

Write-Host ""
Write-Host "Gefunden:"
Write-Host ("  Autostart-Task .......... " + $(if ($task)               { "ja" } else { "nein" }))
Write-Host ("  Virtuelle Umgebung ...... " + $(if (Test-Path $venv)     { "ja" } else { "nein" }))
Write-Host ("  Gebautes Frontend ....... " + $(if (Test-Path $static)   { "ja" } else { "nein" }))
Write-Host ("  Datenbank ............... " + $(if (Test-Path $db)       { "ja" } else { "nein" }))
Write-Host ("  .env (Schlüssel) ........ " + $(if (Test-Path $envFile)  { "ja" } else { "nein" }))
Write-Host ""
Write-Host ("Daten + .env löschen: " + $(if ($Purge) { "JA" } else { "nein (mit -Purge)" }))

if ($DryRun) { Write-Host "Trockenlauf — es wird nichts verändert." -ForegroundColor Yellow }

if (-not $Yes -and -not $DryRun) {
    $answer = Read-Host "Deinstallation jetzt starten? (j/N)"
    if ($answer -notmatch '^[jJyY]') { Write-Host "Abgebrochen — nichts wurde verändert."; return }
}

# --- Safety backup before a purge -----------------------------------------
if ($Purge -and -not $NoBackup -and (Test-Path $db)) {
    Write-Host ">> Sicherung von Datenbank + .env..."
    $stamp   = Get-Date -Format "yyyyMMdd-HHmmss"
    $archive = Join-Path ([Environment]::GetFolderPath("MyDocuments")) "offgridcloud-backup-$stamp.zip"
    if ($DryRun) {
        Write-Host "   [dry-run] Sicherung nach $archive"
    } else {
        $tmp = Join-Path ([System.IO.Path]::GetTempPath()) "ogc-backup-$stamp"
        New-Item -ItemType Directory -Path $tmp -Force | Out-Null
        Copy-Item $db $tmp
        if (Test-Path $envFile) { Copy-Item $envFile (Join-Path $tmp "env.txt") }
        Compress-Archive -Path (Join-Path $tmp "*") -DestinationPath $archive -Force
        Remove-Item -Recurse -Force $tmp
        Write-Host "   Sicherung: $archive" -ForegroundColor Yellow
    }
}

# --- Scheduled Task --------------------------------------------------------
if ($task) {
    Write-Host ">> Autostart-Task 'OffgridCloud' entfernen..."
    if ($DryRun) {
        Write-Host "   [dry-run] Unregister-ScheduledTask -TaskName OffgridCloud"
    } else {
        try {
            Stop-ScheduledTask -TaskName "OffgridCloud" -ErrorAction SilentlyContinue
            Unregister-ScheduledTask -TaskName "OffgridCloud" -Confirm:$false
            Write-Host "   Task entfernt."
        } catch {
            Write-Warning "   Task konnte nicht entfernt werden (Admin-PowerShell nötig): $($_.Exception.Message)"
        }
    }
}

# --- A server still running out of this folder -----------------------------
$running = @()
try {
    $running = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($venv) })
} catch { }
foreach ($proc in $running) {
    Write-Host ">> Laufenden Server beenden (PID $($proc.ProcessId))..."
    if ($DryRun) {
        Write-Host "   [dry-run] Stop-Process -Id $($proc.ProcessId)"
    } else {
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

# --- Installed artefacts ---------------------------------------------------
Write-Host ">> Vom Installer erzeugte Dateien entfernen..."
Remove-Target $venv    "Python-venv (backend\.venv)"
Remove-Target $static  "gebautes Frontend (backend\app\static)"
Remove-Target $dist    "Frontend-Build (frontend\dist)"
Remove-Target $modules "npm-Pakete (frontend\node_modules)"

if ([Environment]::GetEnvironmentVariable("OGC_PORT", "User")) {
    if ($DryRun) {
        Write-Host "   [dry-run] Benutzer-Variable OGC_PORT löschen"
    } else {
        [Environment]::SetEnvironmentVariable("OGC_PORT", $null, "User")
        Write-Host "   Entfernt: Benutzer-Variable OGC_PORT"
    }
}

# --- Data + secrets (opt-in) -----------------------------------------------
if ($Purge) {
    Write-Host ">> Daten und Schlüssel entfernen..."
    Remove-Target $envFile ".env (OGC_SECRET_KEY)"
    Remove-Target $dataDir "Daten (Datenbank + Medien-Puffer)"
}

Write-Host ""
if ($DryRun) {
    Write-Host "Trockenlauf beendet — es wurde nichts verändert." -ForegroundColor Yellow
    return
}
Write-Host "Fertig." -ForegroundColor Green
if (-not $Purge) {
    Write-Host "  Behalten: .env und data\ — endgültig löschen mit -Purge"
}
Write-Host "  Behalten: Python, Node.js und rclone (allgemeine Werkzeuge)."
Write-Host "  Das Repository selbst kannst du jetzt einfach löschen."
