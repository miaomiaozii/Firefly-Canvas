param(
    [Parameter(Mandatory)][string]$GoExe,
    [Parameter(Mandatory)][string]$EvidenceDir,
    [Parameter(Mandatory)][string]$ScratchParent
)
$ErrorActionPreference = 'Stop'

function Get-SystemDllAclSummary {
    $rx = [int][Security.AccessControl.FileSystemRights]::ReadAndExecute
    foreach ($dll in @('kernel32.dll', 'KernelBase.dll', 'ntdll.dll')) {
        $acl = Get-Acl -LiteralPath (Join-Path ([Environment]::SystemDirectory) $dll)
        $rules = $acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])
        foreach ($sid in @('S-1-15-2-1', 'S-1-15-2-2')) {
            $allow = $deny = 0
            $matchesForFile = 0
            foreach ($rule in $rules) {
                if ($rule.IdentityReference.Value -ne $sid -or
                    ($rule.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly)) { continue }
                ++$matchesForFile
                if ($rule.AccessControlType -eq 'Allow') { $allow = $allow -bor [int]$rule.FileSystemRights }
                else { $deny = $deny -bor [int]$rule.FileSystemRights }
            }
            [ordered]@{
                dll=$dll; sid=$sid; allow_rx=(($allow -band $rx) -eq $rx)
                deny_rx_bits=(($deny -band $rx) -ne 0); matching_aces=$matchesForFile
            }
        }
    }
}

if (-not $IsWindows -or [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture -ne 'X64') {
    throw 'Run this synthetic probe from PowerShell 7 on Windows x64.'
}
$go = (Get-Item -LiteralPath $GoExe).FullName
$parent = Get-Item -LiteralPath $ScratchParent
if (-not $parent.PSIsContainer -or ($parent.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    throw 'ScratchParent must be a dedicated, existing local test directory.'
}
if (Test-Path -LiteralPath $EvidenceDir) { throw 'EvidenceDir already exists.' }
$evidence = (New-Item -ItemType Directory -Path $EvidenceDir).FullName
$run = (New-Item -ItemType Directory -Path (Join-Path $parent.FullName ('firefly-444-' + [Guid]::NewGuid().ToString('N')))).FullName
$src = Join-Path $PSScriptRoot 'canary.go'
$exe = Join-Path $run 'canary.exe'
$synthetic = Join-Path $run 'synthetic'
$temp = (New-Item -ItemType Directory -Path (Join-Path $run 'temp')).FullName
$goEnv = @{
    GOENV='off'; GOTOOLCHAIN='local'; GOPROXY='off'; GOSUMDB='off'; GOTELEMETRY='off';
    GOFLAGS=''; GOCACHE=(Join-Path $run 'cache'); GOPATH=(Join-Path $run 'gopath');
    TEMP=$temp; TMP=$temp; CGO_ENABLED='0'; GO111MODULE='off'; GOOS='windows'; GOARCH='amd64'
}
$previousEnv = @{}
$compile = @('build', '-trimpath', '-o', $exe, $src)
$buildExit = $null
$labExit = $null
$clock = [Diagnostics.Stopwatch]::StartNew()
Push-Location -LiteralPath $run
try {
    foreach ($pair in $goEnv.GetEnumerator()) {
        $previousEnv[$pair.Key] = [Environment]::GetEnvironmentVariable($pair.Key, 'Process')
        [Environment]::SetEnvironmentVariable($pair.Key, $pair.Value, 'Process')
    }
    [ordered]@{
        cwd=$run; go=(& $go version); build=@($go)+$compile; probe=@($exe,$synthetic)
        source_sha256=(Get-FileHash -LiteralPath $src -Algorithm SHA256).Hash
        host=(Get-CimInstance Win32_OperatingSystem | Select-Object Caption,Version,BuildNumber,OSArchitecture)
    } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $evidence 'command.json') -Encoding utf8
    & $go @compile *> (Join-Path $evidence 'build.log')
    $buildExit = $LASTEXITCODE
    if ($buildExit -eq 0) {
        & $exe $synthetic *> (Join-Path $evidence 'canary.log')
        $labExit = $LASTEXITCODE
        @(Get-SystemDllAclSummary) | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $evidence 'system-dll-acl.json') -Encoding utf8
    }
} finally {
    Pop-Location
    foreach ($pair in $previousEnv.GetEnumerator()) {
        [Environment]::SetEnvironmentVariable($pair.Key, $pair.Value, 'Process')
    }
    $clock.Stop()
    # The probe owns its synthetic junction; never recursively remove that tree from here.
    $syntheticRemoved = !(Test-Path -LiteralPath $synthetic)
    if ($syntheticRemoved) { Remove-Item -LiteralPath $run -Recurse -Force }
    [ordered]@{
        build_exit=$buildExit; lab_exit=$labExit; wall_seconds=$clock.Elapsed.TotalSeconds
        synthetic_root_removed=$syntheticRemoved; isolated_build_root_removed=!(Test-Path -LiteralPath $run)
    } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $evidence 'result.json') -Encoding utf8
}
if ($buildExit -ne 0) { exit $buildExit }
exit $labExit
