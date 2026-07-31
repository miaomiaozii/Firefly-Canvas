$ErrorActionPreference = 'SilentlyContinue'
$uri = 'http://127.0.0.1:3000/'
$deadline = (Get-Date).AddSeconds(60)

while ((Get-Date) -lt $deadline) {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 -Uri $uri
        if ($response.StatusCode -eq 200) {
            Start-Process $uri
            exit 0
        }
    } catch {
        # The server may still be importing dependencies or creating its data directories.
    }
    Start-Sleep -Milliseconds 500
}

exit 1
