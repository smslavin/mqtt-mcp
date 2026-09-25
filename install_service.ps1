#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Install the MqttMCP server as a Windows service via NSSM.

.DESCRIPTION
    Installs one service: AVEVA Demo MqttMCP (port 8001).
    NSSM must be on PATH (https://nssm.cc/download). Run as Administrator.

.EXAMPLE
    # Edit the Configuration block below, then:
    .\install_service.ps1
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# -- Configuration --------------------------------------------------------------

$Root             = $PSScriptRoot
$MqttBrokerUrl    = "localhost"
$MqttBrokerPort   = "1883"
$MqttTopicRoot    = "#"
# Local broker service to start before MqttMCP. Ignored if the broker is remote
# or the service is not installed.
$BrokerService    = "mosquitto"

# -- End Configuration ----------------------------------------------------------

if (-not (Get-Command nssm -ErrorAction SilentlyContinue)) {
    Write-Error "nssm not found on PATH. Download from https://nssm.cc/download and add to PATH."
}

$LogDir = Join-Path $Root "logs"
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

$svcName = "AVEVA Demo MqttMCP"
$exe     = Join-Path $Root ".venv-mqtt\Scripts\python.exe"

Write-Host "Installing $svcName ..." -ForegroundColor Cyan

$existing = Get-Service -Name $svcName -ErrorAction SilentlyContinue
if ($existing) {
    if ($existing.Status -eq "Running") { nssm stop $svcName confirm }
    nssm remove $svcName confirm
}

nssm install $svcName $exe "server.py"
nssm set $svcName AppDirectory $Root
nssm set $svcName Description "MQTT MCP Server - brownfield MQTT data access (port 8001)"

$envBlock = "FASTMCP_PORT=8001`nMQTT_BROKER_URL=$MqttBrokerUrl`nMQTT_BROKER_PORT=$MqttBrokerPort`nMQTT_TOPIC_ROOT=$MqttTopicRoot"
nssm set $svcName AppEnvironmentExtra $envBlock

nssm set $svcName AppStdout (Join-Path $LogDir "mqtt-mcp-stdout.log")
nssm set $svcName AppStderr (Join-Path $LogDir "mqtt-mcp-stderr.log")
nssm set $svcName AppStdoutCreationDisposition 4
nssm set $svcName AppStderrCreationDisposition 4
nssm set $svcName AppRotateFiles 1
nssm set $svcName AppRotateBytes 10485760

nssm set $svcName AppExit Default Restart
nssm set $svcName AppRestartDelay 60000
nssm set $svcName Start SERVICE_AUTO_START

# Start the broker first so server.py connects on its first attempt instead of
# waiting out a retry backoff.
$isLocalBroker = $MqttBrokerUrl -in @("localhost", "127.0.0.1", "::1")
if ($isLocalBroker -and (Get-Service -Name $BrokerService -ErrorAction SilentlyContinue)) {
    nssm set $svcName DependOnService $BrokerService
    Write-Host "Dependency set: $svcName -> $BrokerService"
} elseif ($isLocalBroker) {
    Write-Warning "Broker service '$BrokerService' not found - no dependency set. Install Mosquitto (see README) and re-run."
}

nssm start $svcName
Start-Sleep -Milliseconds 500
$status = (Get-Service -Name $svcName).Status
Write-Host "$svcName - $status" -ForegroundColor Green
Write-Host "Logs: $LogDir"
