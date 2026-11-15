$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$listener = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
if (-not $listener) { Write-Output "Demo is not running."; exit }
$processInfo = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
if ($processInfo.CommandLine -notlike '*tools/demo_server.py*') {
    throw "Port 8765 belongs to another process; left running."
}
Stop-Process -Id $processInfo.ProcessId
Write-Output "PrivateTraffic demo stopped."
