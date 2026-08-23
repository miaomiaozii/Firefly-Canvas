param(
    [string]$PythonPath = ''
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$uri = 'http://127.0.0.1:3000/'

function Resolve-Python {
    if ($PythonPath -and (Test-Path -LiteralPath $PythonPath)) { return $PythonPath }
    $bundled = Join-Path $root 'python\python.exe'
    if (Test-Path -LiteralPath $bundled) { return $bundled }
    return 'python.exe'
}

function Stop-FireflyServer {
    $killed = $false
    # 1) Kill whatever is listening on port 3000 (most accurate).
    $conns = Get-NetTCPConnection -LocalPort 3000 -State Listen -ErrorAction SilentlyContinue
    if ($conns) {
        foreach ($conn in $conns) {
            try {
                Stop-Process -Id $conn.OwningProcess -Force -ErrorAction Stop
                Write-Host "[RESTART] Stopped server PID $($conn.OwningProcess) (port 3000)"
                $killed = $true
            } catch {
                # Process may already have exited.
            }
        }
    }
    # 2) Kill orphaned python processes still running run-server.py / main.py.
    $procs = Get-CimInstance Win32_Process -Filter "Name='python.exe' or Name='pythonw.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match 'run-server\.py|main\.py' }
    if ($procs) {
        foreach ($proc in $procs) {
            try {
                Stop-Process -Id $proc.ProcessId -Force -ErrorAction Stop
                Write-Host "[RESTART] Stopped orphan server PID $($proc.ProcessId)"
                $killed = $true
            } catch {
                # Ignore; already handled above or gone.
            }
        }
    }
    if (-not $killed) {
        Write-Host "[RESTART] No running Firefly server found."
    }
    # Give the OS a moment to release the port.
    Start-Sleep -Milliseconds 800
}

function Bump-AssetVersions {
    $htmlPath = Join-Path $root 'static\smart-canvas.html'
    if (-not (Test-Path -LiteralPath $htmlPath)) {
        Write-Host "[WARN] static\smart-canvas.html not found; skipping cache bust."
        return
    }
    # Read/write via .NET with explicit UTF-8 (no BOM) so the file's encoding and
    # CRLF line endings are preserved; Set-Content's default encoding corrupts it.
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    $content = [System.IO.File]::ReadAllText($htmlPath, $utf8NoBom)
    # Unique on every restart so the browser always fetches the fresh main CSS/JS.
    # Only the two files that actually change during development are bumped, to
    # keep the HTML (and git) clean; vendor assets are untouched.
    $newVersion = (Get-Date -Format 'yyyyMMdd.HHmmss') + '.' + ((Get-Random -Maximum 9000) + 1000)
    # Use ${1} (not $1) so .NET does not swallow the version digits into the group ref.
    $updated = [regex]::Replace($content, '(smart-canvas\.css\?v=)[^"]+', "`${1}$newVersion")
    $updated = [regex]::Replace($updated, '(smart-canvas\.js\?v=)[^"]+', "`${1}$newVersion")
    if ($updated -ne $content) {
        [System.IO.File]::WriteAllText($htmlPath, $updated, $utf8NoBom)
        Write-Host "[RESTART] Bumped smart-canvas css/js versions to $newVersion (browser cache invalidated)."
    } else {
        Write-Host "[WARN] smart-canvas.css/js ?v= parameters not found in smart-canvas.html."
    }
}

function Start-FireflyServer {
    $py = Resolve-Python
    $dataDir = Join-Path $root 'data'
    New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
    $stdout = Join-Path $dataDir 'server.stdout.log'
    $stderr = Join-Path $dataDir 'server.stderr.log'
    # Same entrypoint as run.bat -> launch-firefly.ps1.
    Start-Process -FilePath $py `
        -ArgumentList @('tools\run-server.py') `
        -WorkingDirectory $root `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr | Out-Null
    Write-Host "[RESTART] Server starting via tools\run-server.py ..."
}

Write-Host "=== Firefly Canvas restart ==="
Stop-FireflyServer
Bump-AssetVersions
Start-FireflyServer

# Reuse the existing wait + open-browser helper.
$waitScript = Join-Path $root 'tools\wait-for-local-server.ps1'
Start-Process -FilePath 'powershell.exe' `
    -ArgumentList @('-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $waitScript) `
    -WorkingDirectory $root `
    -WindowStyle Hidden | Out-Null

Write-Host "Done. The browser will open at $uri once the server is ready."
