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
    # #293：仅在端口未命中时才做全表扫——Get-CimInstance Win32_Process 在重载机上可挂 >200s，
    # 端口已命中则服务必死，无需再扫；扫描自身也降级为 SilentlyContinue+超时守卫。
    if (-not $killed) {
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
    }
    if (-not $killed) {
        Write-Host "[RESTART] No running Firefly server found."
    }
    # Give the OS a moment to release the port.
    Start-Sleep -Milliseconds 800
}

function Bump-AssetVersions {
    # #371：以前只刷 smart-canvas.html 一个页——另两个产品页（project-list.html / global-settings.html）
    # 的 CSS/JS 戳记停在旧值，改了前端用户拿不到，只有手动跑 refresh-cache.ps1 才刷。
    # 现在与 refresh-cache.ps1 同口径：一切 static\*.html 里的 ?v= 刷成同一个新值。
    $staticDir = Join-Path $root 'static'
    if (-not (Test-Path -LiteralPath $staticDir)) {
        Write-Host "[WARN] static\ not found; skipping cache bust."
        return
    }
    # Read/write via .NET with explicit UTF-8 (no BOM) so the file's encoding and
    # CRLF line endings are preserved; Set-Content's default encoding corrupts it.
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    # Unique on every restart so the browser always fetches the fresh CSS/JS.
    $newVersion = (Get-Date -Format 'yyyyMMdd.HHmmss') + '.' + ((Get-Random -Maximum 9000) + 1000)
    # Use ${1} (not $1) so .NET does not swallow the version digits into the group ref.
    # #292 后遗症：必须整页统一 bump——守卫 test_canvas_assets_share_one_cache_buster 要求
    # 同一页所有静态资产共用一个 ?v=；选择性 bump（只 css/js）会把守卫刷红。
    $bumped = 0
    foreach ($html in Get-ChildItem -Path $staticDir -Filter '*.html' -File -ErrorAction SilentlyContinue) {
        $content = [System.IO.File]::ReadAllText($html.FullName, $utf8NoBom)
        if ($content -notmatch '\?v=') { continue }
        $updated = [regex]::Replace($content, '(\?v=)[^"''\s>]+', "`${1}$newVersion")
        if ($updated -eq $content) { continue }
        # 原子写：先写 temp 再覆盖——进程被杀时不会留下 0 字节 html（本仓实测两次被杀即归零）。
        # bump 失败不该炸掉整次重启：捕获后降级为 WARN，服务照常起。
        try {
            $tmp = "$($html.FullName).tmp"
            [System.IO.File]::WriteAllText($tmp, $updated, $utf8NoBom)
            Move-Item -LiteralPath $tmp -Destination $html.FullName -Force
            $bumped++
        } catch {
            Write-Host ("[WARN] cache bust failed for " + $html.Name + ": " + $_.Exception.GetType().FullName + ": " + $_.Exception.Message)
            Remove-Item -LiteralPath "$($html.FullName).tmp" -Force -ErrorAction SilentlyContinue
        }
    }
    if ($bumped -gt 0) {
        Write-Host "[RESTART] Bumped asset versions on $bumped page(s) to $newVersion (browser cache invalidated)."
    } else {
        Write-Host "[WARN] no ?v= parameters found under static\*.html; browser may still serve cached assets."
    }
}

function Start-FireflyServer {
    $py = Resolve-Python
    # issue #364：不再重定向到 data\ 下的 server.*.log（固定名、下次启动直接盖掉）。每次启动一对带 UTC 时间戳的 boot 日志，只兜「日志配置生效之前就崩」的现场；
    # 服务自己的日志走 logs\server.log（UTF-8、追加、7 天 / 100MB 轮转，见 firefly_server_log.py）。
    $logDir = Join-Path $root 'logs'
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $stamp = [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss')
    $stdout = Join-Path $logDir ("boot-$stamp.stdout.log")
    $stderr = Join-Path $logDir ("boot-$stamp.stderr.log")
    # 不设 UTF-8，Windows 子进程按 GBK 码页写，中文日志全成乱码。
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    # Same entrypoint as run.bat -> launch-firefly.ps1.
    Start-Process -FilePath $py `
        -ArgumentList @('tools\run-server.py') `
        -WorkingDirectory $root `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr | Out-Null
    Write-Host "[RESTART] Server starting via tools\run-server.py ... log: $stdout"
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
