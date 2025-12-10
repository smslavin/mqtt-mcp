# WORK IN PROGRESS

# MQTT MCP Server

An [MCP](https://modelcontextprotocol.io/) server that interfaces with an MQTT broker. It allows AI agents to explore MQTT topics, subtopics, and inspect their values.

## Features

- **Topic Discovery**: Subscribes to a root topic (default `V/#`) and builds a real-time tree of active topics.
- **Tools**:
  - `list_topics()`: View the hierarchy of discovered topics.
  - `list_subtopics(topic_path)`: Explore specific branches of the topic tree.
  - `read_topic_value(topic_path)`: Get the latest retained value of a topic.

## Setup

### Prerequisites

- Python 3.13+
- Conda (recommended)

### Installation

1. Create the environment:
    ```bash
    conda create -n mqttMcp python=3.13
    conda activate mqttMcp
    ```

2. Install dependencies:
    ```bash
    pip install -r requirements.txt
    ```

3. Configure environment:
   Create a `.env` file (see `.env.example` or create one based on requirements):
   ```
   MQTT_BROKER_URL=test.mosquitto.org
   MQTT_BROKER_PORT=1883
   ```

## Usage

Run the server:
```bash
python server.py
```

This starts the MCP server over Stdio. Integrate it with your MCP client by pointing it to the python executable and the `server.py` script.
