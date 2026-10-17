# mqtt-mcp

A natural language interface for MQTT broker data, built on the Model Context Protocol (MCP). Enables AI assistants to discover, navigate, and read values from live MQTT topic hierarchies — without requiring knowledge of the topic structure in advance.

> **Note:** This server is under active development. Current capabilities are read-only.

---

## The Problem

MQTT brokers in industrial and IoT environments can host thousands of topics organized in deep, nested hierarchies. Understanding what data is available — and where it lives — typically requires purpose-built tooling, familiarity with the topic naming conventions of the specific deployment, or manually subscribing to wildcard topics and parsing the output.

For engineers commissioning systems, diagnosing issues, or building integrations, this creates friction. The data is there; getting to it is the problem.

---

## The Solution

mqtt-mcp connects to an MQTT broker, subscribes to the configured topic root, and builds a live map of the topic hierarchy as messages arrive. That map is exposed as AI-callable tools via the [Model Context Protocol](https://modelcontextprotocol.io/), enabling an AI assistant to navigate the topic tree and read values conversationally.

It also supports on-demand scanning: the `scan_topics` tool listens to any wildcard pattern for a configurable duration, on a connection of its own, and returns what it heard — designed for brownfield discovery workflows where the topic structure is unknown in advance.

**Example interactions:**

```
"What topics are publishing on this broker?"
"Scan Plant/WTP/# for 15 seconds and show me what you find."
"What subtopics are under Plant/WTP/Pump?"
"What is the current value of Plant/WTP/Pump/RawWater_01/Flow?"
"Show me the full topic tree."
```

---

## Key Capabilities

| Capability | Description |
|---|---|
| **Persistent discovery** | Subscribes to a configurable wildcard root at startup and caches the last payload of every topic, with its arrival time |
| **On-demand scanning** | `scan_topics` listens to any pattern for 1-30 s on its own connection — useful for discovering unknown topic structures |
| **Tree navigation** | Explore top-level topics or drill into specific branches of the hierarchy |
| **Value inspection** | Read the last known value of one or more topics, with its age and whether the broker is connected |

---

## Architecture

```
AI Assistant (Claude Desktop, Claude Code, etc.)
        │
        ▼
mqtt-mcp (MCP SDK / SSE)
        │
        ├── In-memory topic cache (last payload + arrival time per topic, bounded)
        │
        └── MQTT Broker (paho-mqtt, background loop)
                └── Subscribes to MQTT_TOPIC_ROOT on connect (default: #)
```

### Design Decisions

**Background MQTT loop.** The paho-mqtt client runs in a background thread via `loop_start()`, continuously receiving messages and updating the in-memory cache: one flat dict of topic → (payload, arrival time). The lock is held only to store a message or copy the dict; tools build their results from the copy, so they never hold up the network thread.

**Values carry their age.** Every result says whether the broker is connected and when the newest message arrived, and every value has `received_at` and `age_s`. While the broker is down the tools still answer from the cache, flagged as such, instead of passing old values off as live.

**Bounded cache.** The cache keeps at most `MQTT_MAX_TOPICS` topics (default 10000); messages on further new topics are dropped and counted in `messages_dropped`. Payloads over 2000 characters are cut, saying so. Topics named after batches or serial numbers would otherwise grow memory for as long as the service runs.

**Scans on their own connection.** `scan_topics` connects a short-lived client of its own, so a scan can't unsubscribe the persistent subscription or keep it from updating, whatever filter it uses, and concurrent scans don't interfere.

**Structured results, failures as errors.** Every tool returns structured content (with an output schema). A failure — nothing heard yet, an unknown topic, a scan that hears nothing, an unreachable broker — is a tool error, so clients don't cache or audit it as a result.

---

## Technology Stack

| Component | Technology |
|---|---|
| MCP Server | Python, [MCP SDK](https://github.com/modelcontextprotocol/python-sdk) (`MCPServer`, mcp>=2.0) |
| MQTT Client | [paho-mqtt](https://github.com/eclipse/paho.mqtt.python) |
| Broker Compatibility | Any MQTT 3.1.1 / 5.0 compliant broker (Mosquitto, HiveMQ, etc.) |
| Runtime | Python 3.13 |

---

## MCP Tools

| Tool | Description |
|---|---|
| `browse_topics` | List the subtopics directly below a topic (the top level by default), with values and child counts; up to 500 |
| `browse_topic_tree` | Return every known topic below a topic (everything by default), depth-first with values; capped at 500 topics |
| `read_topics` | Read the last payload of up to 200 topics, with arrival time and age; a topic without a value gets an error entry |
| `scan_topics` | Listen to any wildcard pattern for 1-30 s (default 10) on a connection of its own and return every topic heard, up to 2000 |

Before 0.2.0, `list_topics` and `list_subtopics` were `browse_topics`, `get_full_topic_tree` was `browse_topic_tree`, and `read_topic_value` was `read_topics`. The names follow one verb set with opcua-mcp: `browse_*`, `read_*`, `search_*`/`scan_*`.

---

## Configuration

| Environment Variable | Default | Description |
|---|---|---|
| `MQTT_BROKER_URL` | `localhost` | Hostname or IP of the MQTT broker |
| `MQTT_BROKER_PORT` | `1883` | Broker port |
| `MQTT_TOPIC_ROOT` | `#` | Wildcard subscription root for the persistent background subscription |
| `MQTT_MAX_TOPICS` | `10000` | Most topics the cache keeps; messages on further new topics are dropped and counted |
| `FASTMCP_PORT` | `8001` | Port for the MCP SSE server |

---

## Setup

### Prerequisites

- Python 3.13
- An accessible MQTT broker (e.g. [Mosquitto](https://mosquitto.org/)). **The server does not install one.** If nothing is listening on the configured host/port, startup logs `[WinError 10061] No connection could be made because the target machine actively refused it`.

### Installation

1. Install a broker (skip if using an existing broker on another host):
    - Download and run the Windows installer from [mosquitto.org/download](https://mosquitto.org/download/). It registers a `mosquitto` Windows service.
    - Start it and set it to auto-start (as Administrator):
      ```powershell
      Start-Service mosquitto
      Set-Service mosquitto -StartupType Automatic
      Test-NetConnection localhost -Port 1883   # TcpTestSucceeded should be True
      ```
    - Out of the box, Mosquitto 2.x accepts only localhost, anonymous connections. That is fine when mqtt-mcp runs on the same machine. To accept connections from other hosts, add the following to `mosquitto.conf`, restart the service, and allow inbound TCP 1883 in Windows Firewall:
      ```
      listener 1883 0.0.0.0
      allow_anonymous true
      ```
    - A broker with no publishers is empty. Whatever feeds data into it (simulator, gateway, etc.) must also be set up before the tools return anything.

2. If the repo was copied from a zip or download, unblock the scripts. Otherwise PowerShell refuses to run them with "is not digitally signed":
    ```powershell
    Get-ChildItem -Recurse | Unblock-File
    ```

3. Create and activate a virtual environment:
    ```bash
    python -m venv .venv-mqtt
    .venv-mqtt\Scripts\activate   # Windows
    source .venv-mqtt/bin/activate  # macOS/Linux
    pip install -r requirements.txt
    ```
    `requirements.txt` pins exact versions; raise them on purpose, with the tests passing.

4. Configure the broker (optional — defaults to `localhost:1883`):
    ```
    MQTT_BROKER_URL=your.broker.hostname
    MQTT_BROKER_PORT=1883
    MQTT_TOPIC_ROOT=#
    ```

### Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

The tests start an [amqtt](https://github.com/Yakifo/amqtt) broker on a free port and call every tool through an MCP client, including scans on the feed's own filter and a broker that goes away. They need no other service.

### Running

**Windows (PowerShell):**
```powershell
.\start_mqtt_mcp.ps1
```

**Direct:**
```bash
python server.py
```

### Running as a Windows Service (recommended for lab / demo VMs)

Installs MqttMCP as an auto-start Windows service via [NSSM](https://nssm.cc/download):

1. Install NSSM and add it to PATH
2. Edit the Configuration block at the top of `install_service.ps1` (broker URL/port if not localhost). If the broker is local, the script makes the service depend on `$BrokerService` (default `mosquitto`) so the broker starts first. Install Mosquitto before running the script, or the dependency is skipped with a warning.
3. Run as Administrator:
   ```powershell
   .\install_service.ps1            # port 8001, or the port the service already has
   .\install_service.ps1 -Port 8003 # a different port
   ```

Re-running the script keeps the existing service's port, so it can't move the server away from the port the aggregator lists. On the demo VM that is 8003: IIS holds 8001 there, and graccess-mcp's `install_services.ps1 -MqttMcpPort 8003` installs the service. The script refuses a port another program already holds.

To remove: `.\uninstall_service.ps1` (run as Administrator).  
Logs are written to `logs\` with 10 MB rotation and automatic restart on crash.

### Connecting to Claude Desktop or Claude Code

```json
{
  "mcpServers": {
    "mqtt": {
      "type": "sse",
      "url": "http://localhost:8001/sse"
    }
  }
}
```

---

## Limitations and Known Constraints

- **Read-only.** The server cannot publish to topics. All operations are observational.
- **In-memory state only.** The cache is built from messages received since the server started. Topics that haven't published since startup will not appear until a new message arrives.
- **No authentication.** The current implementation connects to the broker without credentials. Brokers requiring username/password authentication are not yet supported.
- **No TLS.** Encrypted broker connections are not currently supported.

---

## Roadmap Considerations

- **Broker authentication** — username/password and TLS support for secured brokers
- **Publish support** — `write_topic_value` tool to enable setting values via natural language, with appropriate confirmation gates
- **Topic persistence** — optional snapshot of the topic tree to disk so state survives server restarts
- **Multi-broker support** — session management to query across more than one broker in a single conversation

---

## License

MIT
