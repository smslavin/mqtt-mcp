import os
import asyncio
from typing import Any, Dict, List, Optional
import json

from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv
import paho.mqtt.client as mqtt

# Load environment variables
load_dotenv()

MQTT_BROKER_URL = os.getenv("MQTT_BROKER_URL", "test.mosquitto.org")
MQTT_BROKER_PORT = int(os.getenv("MQTT_BROKER_PORT", "1883"))
MQTT_TOPIC_ROOT = "V/#"

# Global state for topic tree
# Structure: { "topic_part": { "_value": "payload", "subtopic": { ... } } }
topic_tree: Dict[str, Any] = {}
topic_values: Dict[str, str] = {} # distinct map for easy value lookup: full_path -> value

# Initialize MCP Server
mcp = FastMCP("mqtt-mcp")

# MQTT Client Setup
def on_connect(client, userdata, flags, rc, properties=None):
    print(f"Connected to MQTT Broker with result code {rc}")
    client.subscribe(MQTT_TOPIC_ROOT)

def on_message(client, userdata, msg):
    try:
        topic = msg.topic
        payload = msg.payload.decode("utf-8", errors="ignore")
        
        # Update topic values map
        topic_values[topic] = payload
        
        # Update tree structure
        parts = topic.split('/')
        current_level = topic_tree
        for part in parts:
            if part not in current_level:
                current_level[part] = {}
            current_level = current_level[part]
        current_level["_value"] = payload
        
    except Exception as e:
        print(f"Error processing message: {e}")

mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message

# Start MQTT loop in background
def start_mqtt():
    print(f"Connecting to {MQTT_BROKER_URL}:{MQTT_BROKER_PORT}...")
    try:
        mqtt_client.connect(MQTT_BROKER_URL, MQTT_BROKER_PORT, 60)
        mqtt_client.loop_start()
    except Exception as e:
        print(f"Failed to connect to MQTT broker: {e}")

# Call start_mqtt immediately (or we could do it in a lifespan context if FastMCP supported it easily, 
# but simplified global start is fine for this script)
start_mqtt()

@mcp.tool()
def list_topics() -> str:
    """
    List all known top-level topics currently discovered under the subscribed root.
    Returns a formatted string of the topic tree structure.
    """
    if not topic_tree:
        return "No topics discovered yet. Waiting for messages..."
    
    # Helper to pretty print tree
    def build_tree_str(current: Dict[str, Any], prefix: str = "") -> str:
        lines = []
        for key in sorted(current.keys()):
            if key == "_value":
                continue
            
            value_indicator = " (has value)" if "_value" in current[key] else ""
            lines.append(f"{prefix}- {key}{value_indicator}")
            # Limit recursion depth for display if needed, but strictly requested list topics
            # Let's show only top level for the list_topics if no args, 
            # BUT user asked: "If I give a specific topic name, I should be able to ask about subtopics"
            # So list_topics likely implies the root list.
        return "\n".join(lines)

    return f"Known topics under root:\n{build_tree_str(topic_tree)}"

@mcp.tool()
def list_subtopics(topic_path: str) -> str:
    """
    List subtopics for a given topic path.
    Args:
        topic_path: The full topic path (e.g., 'V/home/kitchen')
    """
    parts = topic_path.split('/')
    current = topic_tree
    
    # Navigate to the topic
    for part in parts:
        if part in current:
            current = current[part]
        else:
            return f"Topic '{topic_path}' not found in known topics."
            
    # List children
    subtopics = [k for k in current.keys() if k != "_value"]
    if not subtopics:
        return f"No subtopics found for '{topic_path}'."
        
    return f"Subtopics for '{topic_path}':\n" + "\n".join([f"- {s}" for s in subtopics])

@mcp.tool()
def read_topic_value(topic_path: str) -> str:
    """
    Read the last known value of a specific topic.
    Args:
        topic_path: The full topic path (e.g., 'V/home/temp')
    """
    if topic_path in topic_values:
        return f"Value for '{topic_path}': {topic_values[topic_path]}"
    else:
        # Check if it exists in tree but has no direct value
        parts = topic_path.split('/')
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

if __name__ == "__main__":
    mcp.run()
