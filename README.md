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

It also supports on-demand scanning: the `scan_topics` tool subscribes to any wildcard pattern for a configurable duration and returns a structured snapshot of what was observed — designed for brownfield discovery workflows where the topic structure is unknown in advance.

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
| **Persistent discovery** | Subscribes to a configurable wildcard root at startup and builds a real-time tree of all active topics |
| **On-demand scanning** | `scan_topics` subscribes to any pattern for a fixed duration — useful for discovering unknown topic structures |
| **Tree navigation** | Explore top-level topics or drill into specific branches of the hierarchy |
| **Value inspection** | Read the last known value for any discovered topic |

---

## Architecture

```
AI Assistant (Claude Desktop, Claude Code, etc.)
        │
        ▼
mqtt-mcp (FastMCP / SSE)
        │
        ├── In-memory topic tree (built from live messages)
        │
        └── MQTT Broker (paho-mqtt, background loop)
                └── Subscribes to MQTT_TOPIC_ROOT on connect (default: #)
```

### Design Decisions

**Background MQTT loop.** The paho-mqtt client runs in a background thread via `loop_start()`, continuously receiving messages and updating the in-memory topic tree. MCP tool calls read from this shared state synchronously, keeping tool response times fast regardless of broker message volume.

**Dual data structures.** Topics are stored in two parallel structures — a nested dict tree for hierarchical navigation and a flat dict for direct value lookup by full path. This avoids traversal overhead on `read_topic_value` calls while preserving the tree structure needed for `list_subtopics`.

**Isolated scan callbacks.** `scan_topics` uses paho's `message_callback_add` / `message_callback_remove` to route messages for the scan filter into a fresh dict without disturbing the persistent subscription. The subscription is removed after the scan window closes.

**Retained message model.** The server captures and holds the last known value for each topic. This reflects how MQTT retained messages work in practice and gives a useful snapshot of current system state without requiring a live subscription at query time.

---

## Technology Stack

| Component | Technology |
|---|---|
| MCP Server | Python, [FastMCP](https://github.com/jlowin/fastmcp) |
| MQTT Client | [paho-mqtt](https://github.com/eclipse/paho.mqtt.python) |
| Broker Compatibility | Any MQTT 3.1.1 / 5.0 compliant broker (Mosquitto, HiveMQ, etc.) |
| Runtime | Python 3.13 |

---

## MCP Tools

| Tool | Description |
|---|---|
| `list_topics` | List top-level topics discovered under the persistent subscription root |
| `list_subtopics` | List subtopics for a given topic path |
| `read_topic_value` | Read the last known value for a specific topic path |
| `get_full_topic_tree` | Return the complete accumulated topic tree with all values inline |
| `scan_topics` | Subscribe to any wildcard pattern for N seconds and return a structured snapshot |

---

## Configuration

| Environment Variable | Default | Description |
|---|---|---|
| `MQTT_BROKER_URL` | `localhost` | Hostname or IP of the MQTT broker |
| `MQTT_BROKER_PORT` | `1883` | Broker port |
| `MQTT_TOPIC_ROOT` | `#` | Wildcard subscription root for the persistent background subscription |
| `FASTMCP_PORT` | `8001` | Port for the FastMCP SSE server |

---

## Setup

### Prerequisites

- Python 3.13
- An accessible MQTT broker (e.g. [Mosquitto](https://mosquitto.org/))

### Installation

1. Create and activate a virtual environment:
    ```bash
    python -m venv .venv-mqtt
    .venv-mqtt\Scripts\activate   # Windows
    source .venv-mqtt/bin/activate  # macOS/Linux
    pip install -r requirements.txt
    ```

2. Configure the broker (optional — defaults to `localhost:1883`):
    ```
    MQTT_BROKER_URL=your.broker.hostname
    MQTT_BROKER_PORT=1883
    MQTT_TOPIC_ROOT=#
    ```

### Running

**Windows (PowerShell):**
```powershell
.\start_mqtt_mcp.ps1
```

**Direct:**
```bash
python server.py
```

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
- **In-memory state only.** The topic tree is built from messages received since the server started. Topics that haven't published since startup will not appear until a new message arrives.
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
