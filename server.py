import os
import asyncio
import logging
import threading
import time
from typing import Any, Dict, List, Optional
import json

from mcp.server.mcpserver import MCPServer
from dotenv import load_dotenv
import paho.mqtt.client as mqtt

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("mqtt-mcp")

MQTT_BROKER_URL = os.getenv("MQTT_BROKER_URL", "localhost")
MQTT_BROKER_PORT = int(os.getenv("MQTT_BROKER_PORT", "1883"))
MQTT_TOPIC_ROOT = os.getenv("MQTT_TOPIC_ROOT", "#")

MAX_SCAN_SECONDS = 30
MAX_TREE_TOPICS = 200

# Global state for topic tree
# Structure: { "topic_part": { "_value": "payload", "subtopic": { ... } } }
topic_tree: Dict[str, Any] = {}
topic_values: Dict[str, str] = {}
_state_lock = threading.Lock()

# Initialize MCP Server
# mcp 2.0's MCPServer constructor dropped the port= shortcut FastMCP had;
# port is now passed to run() below instead.
mcp = MCPServer("mqtt-mcp")

# MQTT Client Setup
def on_connect(client, userdata, flags, rc, properties=None):
    logger.info("Connected to MQTT broker with result code %s", rc)
    client.subscribe(MQTT_TOPIC_ROOT)

def on_message(client, userdata, msg):
    try:
        topic = msg.topic
        payload = msg.payload.decode("utf-8", errors="ignore")

        with _state_lock:
            topic_values[topic] = payload
            parts = topic.split("/")
            current_level = topic_tree
            for part in parts:
                if part not in current_level:
                    current_level[part] = {}
                current_level = current_level[part]
            current_level["_value"] = payload

    except Exception as e:
        logger.error("Error processing message: %s", e)

mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message

def start_mqtt():
    logger.info("Connecting to %s:%s...", MQTT_BROKER_URL, MQTT_BROKER_PORT)
    try:
        mqtt_client.connect(MQTT_BROKER_URL, MQTT_BROKER_PORT, 60)
        mqtt_client.loop_start()
    except Exception as e:
        logger.error("Failed to connect to MQTT broker: %s", e)

start_mqtt()

@mcp.tool()
def list_topics() -> str:
    """
    List all known top-level topics currently discovered under the subscribed root.
    Returns a formatted string of top-level topic names (one level deep).
    """
    with _state_lock:
        snapshot = dict(topic_tree)

    if not snapshot:
        return "No topics discovered yet. Waiting for messages..."

    def build_tree_str(current: Dict[str, Any], prefix: str = "") -> str:
        lines = []
        for key in sorted(current.keys()):
            if key == "_value":
                continue
            value_indicator = " (has value)" if "_value" in current[key] else ""
            lines.append(f"{prefix}- {key}{value_indicator}")
        return "\n".join(lines)

    return f"Known topics under root:\n{build_tree_str(snapshot)}"

@mcp.tool()
def list_subtopics(topic_path: str) -> str:
    """
    List the immediate subtopics for a given topic path.

    Args:
        topic_path: Full MQTT topic path, e.g. "Plant/WTP/Pump1".

    Returns a list of child topic names (one level below the given path).
    Returns an error string if the path has not been seen.
    """
    with _state_lock:
        parts = topic_path.split("/")
        current = topic_tree
        for part in parts:
            if part in current:
                current = current[part]
            else:
                return f"Topic '{topic_path}' not found in known topics."
        subtopics = [k for k in current.keys() if k != "_value"]

    if not subtopics:
        return f"No subtopics found for '{topic_path}'."

    return f"Subtopics for '{topic_path}':\n" + "\n".join([f"- {s}" for s in subtopics])

@mcp.tool()
def read_topic_value(topic_path: str) -> str:
    """
    Read the last known value of a specific MQTT topic.

    Args:
        topic_path: Full MQTT topic path, e.g. "Plant/WTP/Pump1/Speed".

    Returns the most recently received payload as a string, or an informative
    message if the topic exists as a parent node but carries no value, or has
    not been seen at all.
    """
    with _state_lock:
        if topic_path in topic_values:
            return f"Value for '{topic_path}': {topic_values[topic_path]}"

        parts = topic_path.split("/")
        current = topic_tree
        found = True
        for part in parts:
            if part in current:
                current = current[part]
            else:
                found = False
                break

    if found:
        return f"Topic '{topic_path}' exists but has no direct value recorded (might be a parent topic)."

    return f"Topic '{topic_path}' has not been seen."

@mcp.tool()
def get_full_topic_tree() -> str:
    """
    Return the complete known topic tree with all values from the persistent subscription.
    Useful for seeing everything received since the server started.

    Returns a hierarchical text tree. Capped at 200 topics; a warning is appended if truncated.
    """
    with _state_lock:
        import copy
        tree_snapshot = copy.deepcopy(topic_tree)

    if not tree_snapshot:
        return "No topics discovered yet."

    lines = []
    truncated = False

    def build_full_tree(node: Dict[str, Any], prefix: str = "") -> None:
        nonlocal truncated
        for key in sorted(node.keys()):
            if key == "_value":
                continue
            if len(lines) >= MAX_TREE_TOPICS:
                truncated = True
                return
            child = node[key]
            value = child.get("_value", "")
            value_str = f" = {value}" if value else ""
            lines.append(f"{prefix}{key}{value_str}")
            build_full_tree(child, prefix + "  ")

    build_full_tree(tree_snapshot)
    result = "\n".join(lines)
    if truncated:
        result += f"\n\n[Truncated: showing first {MAX_TREE_TOPICS} topics. Use list_subtopics to explore specific branches.]"
    return result


@mcp.tool()
def scan_topics(topic_filter: str = "#", duration_seconds: int = 10) -> str:
    """
    Subscribe to a topic pattern, collect messages for a fixed duration, then return
    a structured topic tree as JSON. Use this to discover what topics an MQTT source
    is publishing.

    This is the primary tool for brownfield onboarding: subscribe to a wildcard pattern,
    observe what topics appear, and use the result to design a System Platform template model.

    Args:
        topic_filter:     MQTT subscription filter, e.g. "Plant/WTP/#" or "#".
                          Wildcards: # matches everything below, + matches one level.
        duration_seconds: How long to listen before returning results (default 10, max 30).

    Returns JSON with keys: filter, duration_seconds, unique_topics, tree (nested dict).
    """
    duration_seconds = min(duration_seconds, MAX_SCAN_SECONDS)

    scan_tree: Dict[str, Any] = {}
    scan_values: Dict[str, str] = {}
    scan_lock = threading.Lock()

    def on_scan_message(client, userdata, msg):
        try:
            topic = msg.topic
            payload = msg.payload.decode("utf-8", errors="ignore")
            with scan_lock:
                scan_values[topic] = payload
                parts = topic.split("/")
                current = scan_tree
                for part in parts:
                    if part not in current:
                        current[part] = {}
                    current = current[part]
                current["_value"] = payload
        except Exception:
            pass

    mqtt_client.message_callback_add(topic_filter, on_scan_message)
    mqtt_client.subscribe(topic_filter)

    time.sleep(duration_seconds)

    mqtt_client.unsubscribe(topic_filter)
    mqtt_client.message_callback_remove(topic_filter)

    with scan_lock:
        unique_topics = len(scan_values)
        tree_copy = scan_tree.copy()

    if not tree_copy:
        return json.dumps({
            "filter": topic_filter,
            "duration_seconds": duration_seconds,
            "unique_topics": 0,
            "error": f"No messages received on '{topic_filter}' in {duration_seconds}s. Check broker address and publisher.",
        })

    def strip_value_keys(node: Dict[str, Any]) -> Dict[str, Any]:
        """Convert internal tree (with _value sentinel) to clean nested dict."""
        result = {}
        for key, child in node.items():
            if key == "_value":
                continue
            result[key] = {
                "value": child.get("_value"),
                "children": strip_value_keys(child),
            }
        return result

    return json.dumps({
        "filter": topic_filter,
        "duration_seconds": duration_seconds,
        "unique_topics": unique_topics,
        "tree": strip_value_keys(tree_copy),
    }, indent=2)


if __name__ == "__main__":
    mcp.run("sse", port=int(os.getenv("FASTMCP_PORT", "8001")))
