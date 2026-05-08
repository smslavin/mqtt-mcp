#Requires -RunAsAdministrator
$svc = Get-Service -Name "MqttMCP" -ErrorAction SilentlyContinue
if (-not $svc) { Write-Host "MqttMCP not installed."; exit 0 }
if ($svc.Status -eq "Running") { nssm stop MqttMCP confirm }
nssm remove MqttMCP confirm
Write-Host "MqttMCP removed."
