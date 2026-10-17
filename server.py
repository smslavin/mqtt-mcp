"""MQTT MCP server: browse and read the topics on one MQTT broker.

At startup a paho client subscribes to MQTT_TOPIC_ROOT on MQTT_BROKER_URL and
keeps the last payload of every topic it hears, with the time it arrived.
browse_topics, browse_topic_tree and read_topics answer from that cache, and
every result says whether the broker is connected and how old each value is.
scan_topics listens on a client of its own for a few seconds, so it never
touches the cache's subscription.

Every tool returns structured content. Every failure is raised as a ToolError,
so the client sees isError and doesn't cache or audit a failure as a result.
"""

import asyncio
import functools
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from typing_extensions import NotRequired, TypedDict

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("mqtt-mcp")

MQTT_BROKER_URL = os.getenv("MQTT_BROKER_URL", "localhost")
MQTT_BROKER_PORT = int(os.getenv("MQTT_BROKER_PORT", "1883"))
MQTT_TOPIC_ROOT = os.getenv("MQTT_TOPIC_ROOT", "#")

# The cache keeps at most this many topics; messages on further new topics are
# counted and dropped. Topics named after batches or serial numbers would
# otherwise grow it for as long as the service runs.
MAX_TOPICS = int(os.getenv("MQTT_MAX_TOPICS", "10000"))
MAX_PAYLOAD_CHARS = 2000      # longer payloads are cut, saying so
MAX_CHILDREN = 500            # browse_topics
MAX_TREE_TOPICS = 500         # browse_topic_tree
MAX_READ_TOPICS = 200         # read_topics
MAX_SCAN_TOPICS = 2000        # scan_topics
MIN_SCAN_SECONDS, MAX_SCAN_SECONDS = 1, 30
SCAN_CONNECT_TIMEOUT_S = 5

# mcp 2.0's MCPServer constructor dropped the port= shortcut FastMCP had;
# port is now passed to run() below instead.
mcp = MCPServer("mqtt-mcp")

# Every tool here only observes the plant, so all are declared read-only.
# Clients (the chat UI, AGGREGATOR_READ_ONLY) rely on this: a tool without
# annotations is treated as a write and gated.
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True)


def _payload(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace")
    if len(text) > MAX_PAYLOAD_CHARS:
        return f"{text[:MAX_PAYLOAD_CHARS]}… [cut: {len(raw)} bytes]"
    return text


def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds") if ts else None


class Feed:
    """The persistent subscription and the cache it fills.

    paho's network thread writes the cache; tools read it. The lock is held
    only to store one message or to copy the dict, never while a tool builds
    its result. paho reconnects with backoff, and on_connect resubscribes.
    """

    def __init__(self, host: str, port: int, topic_root: str = "#", max_topics: int = MAX_TOPICS):
        self.host, self.port, self.topic_root, self.max_topics = host, port, topic_root, max_topics
        self.connected = False
        self.last_message_at: float | None = None
        self.dropped = 0
        self._values: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_connect_fail = self._on_connect_fail
        self._client.on_message = self._on_message
        self._client.reconnect_delay_set(min_delay=1, max_delay=30)

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}"

    def start(self) -> None:
        # connect_async lets the loop thread own the connection, so paho retries
        # with backoff if the broker is down at startup or drops later.
        logger.info("Connecting to %s, subscribing to %s", self.address, self.topic_root)
        self._client.connect_async(self.host, self.port, 60)
        self._client.loop_start()

    def stop(self) -> None:
        self._client.disconnect()
        self._client.loop_stop()

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code.is_failure:
            logger.warning("MQTT broker %s refused the connection: %s", self.address, reason_code)
            return
        logger.info("Connected to MQTT broker %s", self.address)
        self.connected = True
        client.subscribe(self.topic_root)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        self.connected = False
        logger.warning("Disconnected from MQTT broker %s (%s); retrying in background", self.address, reason_code)

    def _on_connect_fail(self, client, userdata):
        logger.warning("MQTT broker %s unreachable; retrying", self.address)

    def _on_message(self, client, userdata, msg):
        now = time.time()
        payload = _payload(msg.payload)
        with self._lock:
            if msg.topic not in self._values and len(self._values) >= self.max_topics:
                self.dropped += 1
                return
            self._values[msg.topic] = (payload, now)
            self.last_message_at = now

    def snapshot(self) -> dict[str, tuple[str, float]]:
        with self._lock:
            return dict(self._values)

    def status(self, cached: int) -> "Broker":
        status: Broker = {"address": self.address, "connected": self.connected,
                          "last_message_at": _iso(self.last_message_at), "topics_cached": cached}
        if self.dropped:
            status["messages_dropped"] = self.dropped
        return status


_feed: Feed | None = None


def _get_feed() -> Feed:
    if _feed is None:
        raise ToolError("The MQTT feed isn't running.")
    return _feed


def _tool(fn):
    """Register fn as a read-only tool, raising every failure as a ToolError.

    mcp 2.x shows the client a ToolError's message; any other exception
    reaches it as a bare "Error executing tool".
    """
    @functools.wraps(fn)
    async def guarded(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except ToolError:
            raise
        except Exception as e:
            logger.exception("%s failed", fn.__name__)
            raise ToolError(f"{fn.__name__} failed: {e or type(e).__name__}") from e

    return mcp.tool(annotations=READ_ONLY)(guarded)


# ------------------------------------------------------------------
# Result shapes
# ------------------------------------------------------------------

class Broker(TypedDict):
    """Every result carries this, so a stale value can't pass for a live one."""
    address: str
    connected: bool
    last_message_at: str | None    # the newest message the cache has heard, UTC
    topics_cached: int
    messages_dropped: NotRequired[int]   # on new topics, while the cache was full


class Topic(TypedDict):
    """One topic, as every tool reports it. Fields that don't apply are left out."""
    topic: str
    depth: NotRequired[int]          # browse_topic_tree only; the root's children are 0
    child_count: NotRequired[int]    # subtopics directly below
    value: NotRequired[str]          # the last payload, for a topic that has one
    received_at: NotRequired[str]    # UTC
    age_s: NotRequired[float]
    error: NotRequired[str]          # read_topics only


def _topic(topic: str, entry: tuple[str, float] | None, now: float) -> Topic:
    t: Topic = {"topic": topic}
    if entry is not None:
        t["value"], ts = entry
        t["received_at"] = _iso(ts)
        t["age_s"] = round(now - ts, 1)
    return t


def _children(topics, path: str) -> dict[str, set[str]]:
    """Each direct subtopic of path -> the names of its own direct subtopics."""
    prefix = f"{path}/" if path else ""
    out: dict[str, set[str]] = {}
    for t in topics:
        if t.startswith(prefix) and t != path:
            parts = t[len(prefix):].split("/", 2)
            kids = out.setdefault(parts[0], set())
            if len(parts) > 1:
                kids.add(parts[1])
    return out


def _valid_filter(f: str) -> bool:
    """An MQTT subscription filter: '#' only as the whole last level, '+' only as a whole level."""
    if not f or len(f.encode()) > 65535:
        return False
    levels = f.split("/")
    for i, level in enumerate(levels):
        if "#" in level and (level != "#" or i != len(levels) - 1):
            return False
        if "+" in level and level != "+":
            return False
    return True


def _check_path(snapshot: dict, path: str, feed: Feed) -> None:
    if not snapshot:
        state = "connected" if feed.connected else "not connected"
        raise ToolError(f"No topics heard yet from the MQTT broker at {feed.address} ({state}).")
    if path and path not in snapshot and not any(t.startswith(f"{path}/") for t in snapshot):
        raise ToolError(f"Topic {path!r} has not been seen. Browse from the root to see what has.")


# ------------------------------------------------------------------
# Tools
# ------------------------------------------------------------------

class Browse(TypedDict):
    broker: Broker
    topic: Topic                # the browsed topic; the root is ""
    children: list[Topic]       # the first 500
    total_children: int


@_tool
async def browse_topics(topic_path: str = "") -> Browse:
    """List the subtopics directly below a topic, with their last values.

    Pass no topic_path to list the top-level topics. Each subtopic carries
    child_count, and its value, arrival time and age if it has a payload.

    Args:
        topic_path: Topic to look below, e.g. "Plant/WTP/Pump1". Blank for the top level.
    """
    feed = _get_feed()
    path = topic_path.strip("/")
    snapshot = feed.snapshot()
    _check_path(snapshot, path, feed)
    now = time.time()
    below = _children(snapshot, path)
    children = []
    for name in sorted(below)[:MAX_CHILDREN]:
        topic = f"{path}/{name}" if path else name
        child = _topic(topic, snapshot.get(topic), now)
        child["child_count"] = len(below[name])
        children.append(child)
    return {"broker": feed.status(len(snapshot)), "topic": _topic(path, snapshot.get(path), now),
            "children": children, "total_children": len(below)}


class Tree(TypedDict):
    broker: Broker
    root: str
    topics: list[Topic]
    complete: bool              # false when the 500-topic limit cut it short


@_tool
async def browse_topic_tree(topic_path: str = "") -> Tree:
    """Return every known topic below a topic, depth-first, with last values and ages.

    The cache holds everything heard since the server started. Stops after
    500 topics (complete is then false); browse a branch for the rest.

    Args:
        topic_path: Topic to start below, e.g. "Plant/WTP". Blank for everything.
    """
    feed = _get_feed()
    path = topic_path.strip("/")
    snapshot = feed.snapshot()
    _check_path(snapshot, path, feed)
    now = time.time()
    prefix = f"{path}/" if path else ""
    nested: dict = {}
    for t in snapshot:
        if t.startswith(prefix) and t != path:
            level = nested
            for part in t[len(prefix):].split("/"):
                level = level.setdefault(part, {})
    out: list[Topic] = []

    def walk(level: dict, parent: str, depth: int) -> bool:
        for name in sorted(level):
            if len(out) >= MAX_TREE_TOPICS:
                return False
            topic = f"{parent}/{name}" if parent else name
            t = _topic(topic, snapshot.get(topic), now)
            t["depth"] = depth
            if level[name]:
                t["child_count"] = len(level[name])
            out.append(t)
            if not walk(level[name], topic, depth + 1):
                return False
        return True

    complete = walk(nested, path, 0)
    return {"broker": feed.status(len(snapshot)), "root": path, "topics": out, "complete": complete}


class Readings(TypedDict):
    broker: Broker
    readings: list[Topic]


@_tool
async def read_topics(topic_paths: list[str]) -> Readings:
    """Read the last payload of one or more topics, with when it arrived and its age.

    Values come from the cache, so check broker.connected and age_s before
    treating one as live. A topic without a value gets an error entry; the
    call fails only when none has one.

    Args:
        topic_paths: Full topic paths, e.g. ["Plant/WTP/Pump1/Speed"].
    """
    if not topic_paths:
        raise ToolError("Pass at least one topic path.")
    if len(topic_paths) > MAX_READ_TOPICS:
        raise ToolError(f"Read at most {MAX_READ_TOPICS} topics per call ({len(topic_paths)} given).")
    feed = _get_feed()
    snapshot = feed.snapshot()
    now = time.time()
    readings = []
    for path in topic_paths:
        r = _topic(path, snapshot.get(path), now)
        if "value" not in r:
            if any(t.startswith(f"{path}/") for t in snapshot):
                r["error"] = "A parent topic with no value of its own; browse it for its subtopics."
            else:
                r["error"] = "Not seen."
        readings.append(r)
    if all("error" in r for r in readings):
        state = "connected" if feed.connected else "not connected"
        raise ToolError(f"None of the topics has a value (broker {feed.address}, {state}): " +
                        "; ".join(f"{r['topic']}: {r['error']}" for r in readings))
    return {"broker": feed.status(len(snapshot)), "readings": readings}


class Scan(TypedDict):
    broker: str
    filter: str
    duration_seconds: int
    unique_topics: int
    topics: list[Topic]         # sorted; the first 2000
    complete: bool


@_tool
async def scan_topics(topic_filter: str = "#", duration_seconds: int = 10) -> Scan:
    """Listen to a topic filter for a few seconds and return every topic heard, with its last payload.

    Use it to discover what an MQTT source publishes, e.g. for brownfield
    onboarding. Unlike the other tools it hears only what arrives during the
    scan (and retained messages), on a connection of its own.

    Args:
        topic_filter:     MQTT subscription filter, e.g. "Plant/WTP/#" or "#".
                          Wildcards: # matches everything below, + matches one level.
        duration_seconds: How long to listen, 1 to 30 (default 10).
    """
    feed = _get_feed()
    duration = max(MIN_SCAN_SECONDS, min(int(duration_seconds), MAX_SCAN_SECONDS))
    if not _valid_filter(topic_filter):
        raise ToolError(f"Not a valid topic filter: {topic_filter!r}. "
                        "'#' may only be a whole last level, and '+' only a whole level.")

    heard: dict[str, tuple[str, float]] = {}
    seen_more: set[str] = set()   # topics past MAX_SCAN_TOPICS, counted only
    lock = threading.Lock()
    connected = threading.Event()
    refused: list[str] = []

    def on_connect(client, userdata, flags, reason_code, properties=None):
        if reason_code.is_failure:
            refused.append(str(reason_code))
            connected.set()
            return
        result, _ = client.subscribe(topic_filter)
        if result != mqtt.MQTT_ERR_SUCCESS:
            refused.append(mqtt.error_string(result))
        connected.set()

    def on_message(client, userdata, msg):
        now = time.time()
        payload = _payload(msg.payload)
        with lock:
            if msg.topic in heard or len(heard) < MAX_SCAN_TOPICS:
                heard[msg.topic] = (payload, now)
            else:
                seen_more.add(msg.topic)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"mqtt-mcp-scan-{uuid.uuid4().hex[:8]}")
    client.on_connect = on_connect
    client.on_message = on_message
    try:
        client.connect_async(feed.host, feed.port, 60)
        client.loop_start()
        deadline = time.monotonic() + SCAN_CONNECT_TIMEOUT_S
        while not connected.is_set():
            if time.monotonic() > deadline:
                raise ToolError(f"Cannot connect to the MQTT broker at {feed.address} to scan.")
            await asyncio.sleep(0.05)
        if refused:
            raise ToolError(f"The MQTT broker at {feed.address} refused the scan: {refused[0]}")
        await asyncio.sleep(duration)
    finally:
        client.disconnect()
        client.loop_stop()

    with lock:
        found = dict(heard)
        unique = len(found) + len(seen_more)
    if not found:
        raise ToolError(f"No messages on {topic_filter!r} in {duration} s from {feed.address}. "
                        "Check the filter and that something publishes on it.")
    now = time.time()
    return {"broker": feed.address, "filter": topic_filter, "duration_seconds": duration,
            "unique_topics": unique, "topics": [_topic(t, found[t], now) for t in sorted(found)],
            "complete": not seen_more}


if __name__ == "__main__":
    _feed = Feed(MQTT_BROKER_URL, MQTT_BROKER_PORT, MQTT_TOPIC_ROOT)
    _feed.start()
    mcp.run("sse", port=int(os.getenv("FASTMCP_PORT", "8001")))
