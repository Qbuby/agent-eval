# keep-agent-tunnel.ps1
# Windows-side SSH tunnel keeper for the agent at 10.0.0.56:8094.
#
# Why not WSL/autossh: docker compose backend reaches the agent via the
# Windows host LAN IP (172.16.100.188) which only sees Windows-network
# listeners — not WSL-internal ones. So the tunnel must run on Windows.
#
# Windows OpenSSH dies on the slightest network blip. This wrapper restarts
# ssh.exe whenever it exits, with a small backoff to avoid hammering the
# remote.
#
# Run from a PowerShell window (won't survive log-out):
#   pwsh -File scripts\keep-agent-tunnel.ps1
# Or hide it as a Scheduled Task (Trigger=AtLogon, Action=pwsh -File ...).

$ErrorActionPreference = 'Stop'

$Key      = "$env:USERPROFILE\.ssh\autossh_frp.pem"
$JumpHost = 'tunnel@1.15.50.14'
$JumpPort = 2201
$Bind     = '0.0.0.0:18094'
$Remote   = '10.0.0.56:8094'

if (-not (Test-Path $Key)) {
  Write-Error "ssh key not found at $Key"
  exit 2
}

Write-Host "[keep-tunnel] starting ssh tunnel $Bind -> $Remote via $JumpHost`:$JumpPort"
$attempt = 0
while ($true) {
  $attempt++
  Write-Host "[keep-tunnel] attempt #$attempt at $(Get-Date -Format 'HH:mm:ss')"

  & ssh -i $Key -p $JumpPort `
    -L "${Bind}:${Remote}" `
    -o ServerAliveInterval=30 `
    -o ServerAliveCountMax=3 `
    -o ExitOnForwardFailure=yes `
    -o StrictHostKeyChecking=no `
    -o UserKnownHostsFile=/dev/null `
    -N $JumpHost

  $code = $LASTEXITCODE
  Write-Host "[keep-tunnel] ssh exited with code $code, restarting in 5s..."
  Start-Sleep -Seconds 5
}
