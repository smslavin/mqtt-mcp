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

**Example interactions:**

```
"What topics are available on this broker?"
"What subtopics are under V/Plant1/Line2?"
"What is the current value of V/Plant1/Line2/Conveyor/Speed?"
"Show me everything under the pumps namespace."
```

---

## Key Capabilities

| Capability | Description |
|---|---|
| **Topic discovery** | Subscribes to a wildcard root and builds a real-time tree of all active topics |
| **Tree navigation** | Explore top-level topics or drill into specific branches of the hierarchy |
| **Value inspection** | Read the last known retained value for any discovered topic |

---

## Architecture

```
AI Assistant (Claude Desktop, Claude Code, etc.)
        │
        ▼
mqtt-mcp (FastMCP / stdio)
        │
        ├── In-memory topic tree (built from live messages)
        │
        └── MQTT Broker (paho-mqtt, background loop)
                └── Subscribes to V/# on connect
```

### Design Decisions

**Background MQTT loop.** The paho-mqtt client runs in a background thread via `loop_start()`, continuously receiving messages and updating the in-memory topic tree. MCP tool calls read from this shared state synchronously, keeping tool response times fast regardless of broker message volume.

**Dual data structures.** Topics are stored in two parallel structures — a nested dict tree for hierarchical navigation and a flat dict for direct value lookup by full path. This avoids traversal overhead on `read_topic_value` calls while preserving the tree structure needed for `list_subtopics`.

**Retained message model.** The server captures and holds the last known value for each topic. This reflects how MQTT retained messages work in practice and gives a useful snapshot of current system state without requiring a live subscription at query time.

---

## Technology Stack

| Component | Technology |
|---|---|
| MCP Server | Python, [FastMCP](https://github.com/jlowin/fastmcp) |
| MQTT Client | [paho-mqtt](https://github.com/eclipse/paho.mqtt.python) |
| Broker Compatibility | Any MQTT 3.1.1 / 5.0 compliant broker (Mosquitto, HiveMQ, etc.) |
| Runtime | Python 3.13, Conda |

---

## MCP Tools

| Tool | Description |
|---|---|
| `list_topics` | List all top-level topics discovered under the subscribed root |
| `list_subtopics` | List subtopics for a given topic path |
| `read_topic_value` | Read the last known value for a specific topic path |

---

## Setup

### Prerequisites

- [Conda](https://docs.conda.io/en/latest/miniconda.html)
- An accessible MQTT broker

### Installation

1. Create the conda environment:
    ```bash
    conda create -n mqtt-mcp python=3.13
    conda activate mqtt-mcp
    pip install -r requirements.txt
    ```

2. Configure credentials:
    ```bash
    cp .env.example .env
    ```
    Edit `.env`:
    ```
    MQTT_BROKER_URL=your.broker.hostname
    MQTT_BROKER_PORT=1883
    ```

### Connecting from Claude Desktop (Windows, server running in WSL)

```json
{
  "mcpServers": {
    "mqtt": {
      "command": "wsl.exe",
      "args": [
        "/home/yourname/miniconda3/envs/mqtt-mcp/bin/python",
        "/home/yourname/code/mcp-servers/mqtt-mcp/server.py"
      ]
    }
  }
}
```

### Connecting from Claude Code or Claude Desktop (macOS/Linux)

```json
{
  "mcpServers": {
    "mqtt": {
      "command": "/path/to/miniconda3/envs/mqtt-mcp/bin/python",
      "args": ["/path/to/mcp-servers/mqtt-mcp/server.py"]
    }
  }
}
```

---

## Limitations and Known Constraints

- **Read-only.** The server cannot publish to topics. All operations are observational.
- **Topic root is hardcoded.** The subscription root defaults to `V/#`, which reflects the AVEVA System Platform topic convention. Configurable root via `.env` is on the roadmap.
- **In-memory state only.** The topic tree is built from messages received since the server started. Topics that haven't published since startup will not appear until a new message arrives.
- **No authentication.** The current implementation connects to the broker without credentials. Brokers requiring username/password authentication are not yet supported.
- **No TLS.** Encrypted broker connections are not currently supported.

---

## Roadmap Considerations

- **Configurable topic root** — move the `V/#` subscription root to `.env` to support arbitrary broker topic conventions
- **Broker authentication** — username/password and TLS support for secured brokers
- **Publish support** — `write_topic_value` tool to enable setting values via natural language, with appropriate confirmation gates
- **Topic persistence** — optional snapshot of the topic tree to disk so state survives server restarts
- **Multi-broker support** — session management to query across more than one broker in a single conversation

---

## License

MIT
