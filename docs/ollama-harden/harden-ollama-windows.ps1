# Harden Ollama on Windows: IP whitelist for TCP 11434
# Run as Administrator on the Ollama PC.
# Allowed: localhost, stream PC, home LAN optional, public server.

$ErrorActionPreference = "Stop"
$Port = 11434
$Allow = @(
  "127.0.0.1",
  "192.168.50.65",      # stream / VTS PC
  "203.86.236.178"      # public server SSH/deploy IP
)

Write-Host "Setting OLLAMA_HOST=0.0.0.0:$Port (listen on LAN)..."
[System.Environment]::SetEnvironmentVariable("OLLAMA_HOST", "0.0.0.0:$Port", "Machine")
$env:OLLAMA_HOST = "0.0.0.0:$Port"

# Remove previous AILive2D Ollama rules
Get-NetFirewallRule -ErrorAction SilentlyContinue |
  Where-Object { $_.DisplayName -like "AILive2D Ollama*" } |
  Remove-NetFirewallRule

foreach ($ip in $Allow) {
  $name = "AILive2D Ollama allow $ip"
  New-NetFirewallRule -DisplayName $name -Direction Inbound -Action Allow `
    -Protocol TCP -LocalPort $Port -RemoteAddress $ip -Profile Any | Out-Null
  Write-Host "ALLOW $ip -> $Port"
}

New-NetFirewallRule -DisplayName "AILive2D Ollama block others" -Direction Inbound -Action Block `
  -Protocol TCP -LocalPort $Port -Profile Any | Out-Null
Write-Host "BLOCK all other remotes on $Port"

# Restart Ollama app/service if present
Get-Process ollama -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
$svc = Get-Service Ollama -ErrorAction SilentlyContinue
if ($svc) { Restart-Service Ollama -Force; Write-Host "Restarted service Ollama" }
else {
  $exe = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
  if (Test-Path $exe) {
    Start-Process $exe -ArgumentList "serve" -WindowStyle Hidden
    Write-Host "Started ollama serve"
  } else {
    Write-Host "Please relaunch Ollama from Start Menu so new OLLAMA_HOST applies."
  }
}

Write-Host ""
Write-Host "Verify from stream PC: curl http://192.168.50.226:11434/api/tags"
Write-Host "From a non-whitelisted IP should time out / be refused."
