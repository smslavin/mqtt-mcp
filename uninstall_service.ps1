#Requires -RunAsAdministrator
$svc = Get-Service -Name "AVEVA Demo MqttMCP" -ErrorAction SilentlyContinue
if (-not $svc) { Write-Host "AVEVA Demo MqttMCP not installed."; exit 0 }
if ($svc.Status -eq "Running") { nssm stop AVEVA Demo MqttMCP confirm }
nssm remove AVEVA Demo MqttMCP confirm
Write-Host "AVEVA Demo MqttMCP removed."
