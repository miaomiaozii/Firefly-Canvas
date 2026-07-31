param(
    [string]$PythonPath = ''
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$uri = 'http://127.0.0.1:3000/'

function Test-FireflyServer {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 -Uri $uri
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 500
    } catch {
        return $false
    }
}

if (-not $PythonPath) {
    $bundled = Join-Path $root 'python\python.exe'
    if (Test-Path -LiteralPath $bundled) {
        $PythonPath = $bundled
    } else {
        $PythonPath = 'python.exe'
    }
}

if ($PythonPath -eq 'python') {
    $PythonPath = 'python.exe'
}

if (-not (Test-FireflyServer)) {
    $dataDir = Join-Path $root 'data'
    New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
    $stdout = Join-Path $dataDir 'server.stdout.log'
    $stderr = Join-Path $dataDir 'server.stderr.log'
    # The Python helper executes runpy.run_path('main.py', run_name='__main__').
    Start-Process -FilePath $PythonPath `
        -ArgumentList @('tools\run-server.py') `
        -WorkingDirectory $root `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr | Out-Null
}

# Browser opening is also detached. The batch file may close immediately and
# the server process above continues independently until it is stopped by the
# user or the OS.
$waitScript = Join-Path $root 'tools\wait-for-local-server.ps1'
Start-Process -FilePath 'powershell.exe' `
    -ArgumentList @('-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $waitScript) `
    -WorkingDirectory $root `
    -WindowStyle Hidden | Out-Null
