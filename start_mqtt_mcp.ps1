$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$env:FASTMCP_PORT = "8001"
& "$scriptDir\.venv-mqtt\Scripts\python.exe" "$scriptDir\server.py"
