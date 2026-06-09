<#
    Starts the OffgridCloud server on Windows.
    Loads .env from the repo root into the environment, then runs uvicorn from
    the bundled virtual environment.
#>
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

# Load .env (KEY=VALUE lines) into the process environment.
$envFile = Join-Path $Root ".env"
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        if ($_ -match '^\s*([^#=]+)=(.*)$') {
            [Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim())
        }
    }
}

$port = if ($env:OGC_PORT) { $env:OGC_PORT } else { "8000" }
$py = Join-Path $Root "backend\.venv\Scripts\python.exe"

Set-Location (Join-Path $Root "backend")
& $py -m uvicorn app.main:app --host 0.0.0.0 --port $port
