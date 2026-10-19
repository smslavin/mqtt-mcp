#Requires -RunAsAdministrator
$name = "AVEVA Demo MqttMCP"   # one argument: unquoted, nssm got "AVEVA" and removed nothing
$svc = Get-Service -Name $name -ErrorAction SilentlyContinue
if (-not $svc) { Write-Host "$name not installed."; exit 0 }
if ($svc.Status -eq "Running") { nssm stop $name confirm }
nssm remove $name confirm
if ($LASTEXITCODE -ne 0 -or (Get-Service -Name $name -ErrorAction SilentlyContinue)) {
    Write-Error "$name was not removed (nssm exit code $LASTEXITCODE). A service that depends on it, such as the aggregator, may need stopping first."
    exit 1
}
Write-Host "$name removed."
