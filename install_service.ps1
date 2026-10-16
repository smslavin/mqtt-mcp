#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Install the MqttMCP server as a Windows service via NSSM.

.DESCRIPTION
    Installs one service: AVEVA Demo MqttMCP (port 8001 by default).
    NSSM must be on PATH (https://nssm.cc/download). Run as Administrator.

    graccess-mcp's install_services.ps1 installs this service too, with its
    -MqttMcpPort. Re-running this script keeps the port the service already
    has unless -Port is given, so it can't move the server off the port the
    aggregator and the site expect (on MDDEMO, 8003; 8001 is IIS there).

.PARAMETER Port
    Port to listen on. Default: the existing service's port, else 8001.

.EXAMPLE
    # Edit the Configuration block below, then:
    .\install_service.ps1
    .\install_service.ps1 -Port 8003
#>
param(
    [ValidateRange(0, 65535)][int]$Port = 0
)

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

$existing = Get-Service -Name $svcName -ErrorAction SilentlyContinue

# Keep an existing service's port unless -Port says otherwise (mcp-servers#7).
if ($Port -eq 0) {
    $Port = 8001
    $params = "HKLM:\SYSTEM\CurrentControlSet\Services\$svcName\Parameters"
    $current = (Get-ItemProperty -Path $params -Name AppEnvironmentExtra -ErrorAction SilentlyContinue).AppEnvironmentExtra
    $line = @($current) | Where-Object { $_ -match '^FASTMCP_PORT=(\d+)$' } | Select-Object -First 1
    if ($line -and $line -match '^FASTMCP_PORT=(\d+)$') {
        $Port = [int]$Matches[1]
        Write-Host "Keeping the existing service's port, $Port."
    }
}

# Whether a process is the service's: NSSM runs the venv's python.exe, a
# launcher that starts the base interpreter, so walk up a few parents.
function Test-ServiceProcess([string]$ServiceName, [int]$ProcessId) {
    $svcPid = (Get-CimInstance Win32_Service -Filter "Name='$ServiceName'" -ErrorAction SilentlyContinue).ProcessId
    if (-not $svcPid) { return $false }
    $id = $ProcessId
    for ($i = 0; $i -lt 4 -and $id; $i++) {
        if ($id -eq $svcPid) { return $true }
        $id = (Get-CimInstance Win32_Process -Filter "ProcessId=$id" -ErrorAction SilentlyContinue).ParentProcessId
    }
    return $false
}

# Refuse a port another program holds; the server's own listener is fine.
$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($listener) {
    $owner = Get-Process -Id $listener.OwningProcess -ErrorAction SilentlyContinue
    $ours = Test-ServiceProcess $svcName $listener.OwningProcess
    if (-not $ours) {
        $name = if ($owner) { $owner.ProcessName } else { "PID $($listener.OwningProcess)" }
        Write-Error "Port $Port is in use by $name. Pick another with -Port."
    }
}

Write-Host "Installing $svcName on port $Port ..." -ForegroundColor Cyan
if ($existing) {
    if ($existing.Status -eq "Running") { nssm stop $svcName confirm }
    nssm remove $svcName confirm
}

nssm install $svcName $exe "server.py"
nssm set $svcName AppDirectory $Root
nssm set $svcName Description "MQTT MCP Server - brownfield MQTT data access (port $Port)"

$envBlock = "FASTMCP_PORT=$Port`nMQTT_BROKER_URL=$MqttBrokerUrl`nMQTT_BROKER_PORT=$MqttBrokerPort`nMQTT_TOPIC_ROOT=$MqttTopicRoot"
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
