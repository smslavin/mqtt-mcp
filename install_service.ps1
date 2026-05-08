#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Install the MqttMCP server as a Windows service via NSSM.

.DESCRIPTION
    Installs one service: MqttMCP (port 8001).
    NSSM must be on PATH (https://nssm.cc/download). Run as Administrator.

.EXAMPLE
    # Edit the Configuration block below, then:
    .\install_service.ps1
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── Configuration ──────────────────────────────────────────────────────────────

$Root             = $PSScriptRoot
$MqttBrokerUrl    = "localhost"
$MqttBrokerPort   = "1883"
$MqttTopicRoot    = "#"

# ── End Configuration ──────────────────────────────────────────────────────────

if (-not (Get-Command nssm -ErrorAction SilentlyContinue)) {
    Write-Error "nssm not found on PATH. Download from https://nssm.cc/download and add to PATH."
}

$LogDir = Join-Path $Root "logs"
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

$svcName = "MqttMCP"
$exe     = Join-Path $Root ".venv-mqtt\Scripts\python.exe"

Write-Host "Installing $svcName ..." -ForegroundColor Cyan

$existing = Get-Service -Name $svcName -ErrorAction SilentlyContinue
if ($existing) {
    if ($existing.Status -eq "Running") { nssm stop $svcName confirm }
    nssm remove $svcName confirm
}

nssm install $svcName $exe "server.py"
nssm set $svcName AppDirectory $Root
nssm set $svcName Description "MQTT MCP Server — brownfield MQTT data access (port 8001)"

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

nssm start $svcName
Start-Sleep -Milliseconds 500
$status = (Get-Service -Name $svcName).Status
Write-Host "$svcName — $status" -ForegroundColor Green
Write-Host "Logs: $LogDir"
