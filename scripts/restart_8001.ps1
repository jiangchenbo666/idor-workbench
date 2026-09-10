param(
  [string]$PythonExe = "python",
  [string]$HostName = "127.0.0.1",
  [int]$Port = 8001,
  [int]$TaskWorkers = 4,
  [int]$AiTaskWorkers = 1
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")

$connections = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue |
  Where-Object { $_.State -eq "Listen" -and $_.OwningProcess -gt 0 }

foreach ($conn in $connections) {
  Write-Host "Stopping process $($conn.OwningProcess) on port $Port"
  Stop-Process -Id $conn.OwningProcess -Force
}

Start-Sleep -Seconds 1

$env:IDOR_TASK_WORKERS = [string]$TaskWorkers
$env:IDOR_AI_TASK_WORKERS = [string]$AiTaskWorkers

Write-Host "Starting IDOR Workbench on http://$HostName`:$Port"
Start-Process `
  -FilePath $PythonExe `
  -ArgumentList "-m", "uvicorn", "idor_workbench.views.api:app", "--host", $HostName, "--port", "$Port" `
  -WorkingDirectory $Root `
  -WindowStyle Hidden

Start-Sleep -Seconds 3

$status = Invoke-WebRequest -UseBasicParsing "http://$HostName`:$Port/api/bootstrap" |
  Select-Object -ExpandProperty StatusCode

Write-Host "Health check: $status"
Write-Host "Open: http://$HostName`:$Port"
