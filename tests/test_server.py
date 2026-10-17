"""The tools as an MCP client sees them, against a real broker."""

import asyncio

import pytest
from mcp.client import Client

import server
from conftest import BrokerProcess, wait_for

TOOLS = {"browse_topics", "browse_topic_tree", "read_topics", "scan_topics"}


async def call(name, args=None):
    async with Client(server.mcp) as c:
        return await c.call_tool(name, args or {})


async def ok(name, args=None) -> dict:
    result = await call(name, args)
    assert not result.is_error, _text(result)
    assert result.structured_content is not None
    return result.structured_content


async def error(name, args=None) -> str:
    result = await call(name, args)
    assert result.is_error, result.structured_content
    return _text(result)


def _text(result) -> str:
    return "\n".join(b.text for b in result.content if hasattr(b, "text"))


async def publish_and_wait(feed, publisher, topics: dict) -> None:
    for topic, payload in topics.items():
        publisher.publish(topic, payload)
    await wait_for(lambda: all(feed.snapshot().get(t, ("",))[0] == str(p) for t, p in topics.items()))


@pytest.fixture
async def plant(feed, publisher, prefix):
    await publish_and_wait(feed, publisher, {
        f"{prefix}/WTP/Pump1/Speed": "1450",
        f"{prefix}/WTP/Pump1/Running": "true",
        f"{prefix}/WTP/Pump2/Speed": "0",
        f"{prefix}/WTP/Tank1/Level": "72.5",
    })
    return prefix


async def test_tool_surface():
    async with Client(server.mcp) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
    assert set(tools) == TOOLS
    for t in tools.values():
        assert t.annotations.read_only_hint is True, t.name
        assert t.output_schema and "result" not in t.output_schema.get("properties", {}), t.name
        assert t.description, t.name


async def test_nothing_heard_yet_is_an_error(feed):
    # An empty cache must not be cached by the client as "no topics" (#3).
    assert "No topics heard yet from the MQTT broker" in await error("browse_topics")
    assert "(connected)" in await error("browse_topic_tree")


async def test_browse_top_level(plant):
    out = await ok("browse_topics")
    assert out["broker"]["connected"] is True
    assert out["broker"]["topics_cached"] == 4
    assert out["broker"]["last_message_at"]
    assert out["topic"] == {"topic": ""}
    assert out["children"] == [{"topic": plant, "child_count": 1}]


async def test_browse_a_topic(plant):
    out = await ok("browse_topics", {"topic_path": f"{plant}/WTP/Pump1"})
    assert out["total_children"] == 2
    speed, running = sorted(out["children"], key=lambda c: c["topic"], reverse=True)
    assert speed["topic"] == f"{plant}/WTP/Pump1/Speed" and speed["value"] == "1450"
    assert speed["child_count"] == 0
    assert 0 <= speed["age_s"] < 10 and speed["received_at"]
    wtp = await ok("browse_topics", {"topic_path": f"{plant}/WTP/"})  # a trailing slash is fine
    assert {c["topic"]: c["child_count"] for c in wtp["children"]} == {
        f"{plant}/WTP/Pump1": 2, f"{plant}/WTP/Pump2": 1, f"{plant}/WTP/Tank1": 1}


async def test_browse_unknown_topic_is_an_error(plant):
    assert "has not been seen" in await error("browse_topics", {"topic_path": f"{plant}/Nope"})


async def test_browse_children_limit(plant, monkeypatch):
    monkeypatch.setattr(server, "MAX_CHILDREN", 1)
    out = await ok("browse_topics", {"topic_path": f"{plant}/WTP"})
    assert (len(out["children"]), out["total_children"]) == (1, 3)


async def test_topic_tree(plant):
    out = await ok("browse_topic_tree", {"topic_path": f"{plant}/WTP"})
    assert out["complete"] is True
    assert [(t["topic"].removeprefix(f"{plant}/WTP/"), t["depth"], t.get("value")) for t in out["topics"]] == [
        ("Pump1", 0, None), ("Pump1/Running", 1, "true"), ("Pump1/Speed", 1, "1450"),
        ("Pump2", 0, None), ("Pump2/Speed", 1, "0"),
        ("Tank1", 0, None), ("Tank1/Level", 1, "72.5"),
    ]
    assert out["topics"][0]["child_count"] == 2


async def test_topic_tree_limit(plant, monkeypatch):
    monkeypatch.setattr(server, "MAX_TREE_TOPICS", 3)
    out = await ok("browse_topic_tree")
    assert len(out["topics"]) == 3 and out["complete"] is False


async def test_read_topics(plant):
    out = await ok("read_topics", {"topic_paths": [
        f"{plant}/WTP/Tank1/Level", f"{plant}/WTP/Tank1", f"{plant}/Nope"]})
    level, parent, missing = out["readings"]
    assert level["value"] == "72.5" and level["age_s"] >= 0
    assert "parent topic" in parent["error"]
    assert missing["error"] == "Not seen."
    assert out["broker"]["connected"] is True


async def test_read_topics_none_found_is_an_error(plant):
    text = await error("read_topics", {"topic_paths": [f"{plant}/Nope"]})
    assert "None of the topics has a value" in text and "connected" in text
    assert "at least one" in await error("read_topics", {"topic_paths": []})


async def test_cache_is_bounded(broker, publisher, prefix, monkeypatch):
    f = server.Feed("127.0.0.1", broker.port, f"{prefix}/#", max_topics=2)
    monkeypatch.setattr(server, "_feed", f)
    f.start()
    try:
        await wait_for(lambda: f.connected)
        await asyncio.sleep(0.2)
        for i in range(3):
            publisher.publish(f"{prefix}/serial/{i}", "x")
        publisher.publish(f"{prefix}/serial/0", "updated")  # a known topic still updates
        await wait_for(lambda: f.dropped == 1 and f.snapshot().get(f"{prefix}/serial/0", ("",))[0] == "updated")
        out = await ok("browse_topics", {"topic_path": f"{prefix}/serial"})
        assert out["broker"]["topics_cached"] == 2 and out["broker"]["messages_dropped"] == 1
    finally:
        f.stop()


async def test_long_payload_is_cut(feed, publisher, prefix, monkeypatch):
    monkeypatch.setattr(server, "MAX_PAYLOAD_CHARS", 5)
    await publish_and_wait(feed, publisher, {})
    publisher.publish(f"{prefix}/blob", "0123456789")
    await wait_for(lambda: f"{prefix}/blob" in feed.snapshot())
    out = await ok("read_topics", {"topic_paths": [f"{prefix}/blob"]})
    assert out["readings"][0]["value"] == "01234… [cut: 10 bytes]"


async def scan(publisher, args: dict, messages: dict) -> object:
    """Call scan_topics while publishing messages every 0.2 s.

    Live messages, not retained ones: amqtt (the test broker) hands a retained
    message to a later, narrower subscription once any client has subscribed
    to a filter that matches it.
    """
    async def keep_publishing():
        while True:
            for topic, payload in messages.items():
                publisher.publish(topic, payload)
            await asyncio.sleep(0.2)

    task = asyncio.create_task(keep_publishing())
    try:
        return await call("scan_topics", args)
    finally:
        task.cancel()


async def test_scan(feed, publisher, prefix):
    result = await scan(publisher, {"topic_filter": f"{prefix}/#", "duration_seconds": 1},
                        {f"{prefix}/a/x": "1", f"{prefix}/b": "2"})
    out = result.structured_content
    assert not result.is_error and out["unique_topics"] == 2 and out["complete"] is True
    assert [(t["topic"], t["value"]) for t in out["topics"]] == [(f"{prefix}/a/x", "1"), (f"{prefix}/b", "2")]
    assert all(t["age_s"] < 2 for t in out["topics"])


async def test_scan_does_not_freeze_the_cache(feed, publisher, prefix):
    # The bug (#1): a scan on the feed's own filter unsubscribed it, and its
    # callback kept on_message from updating the cache during the scan.
    topic = f"{prefix}/Pump1/Speed"
    await publish_and_wait(feed, publisher, {topic: "100"})
    result = await scan(publisher, {"topic_filter": f"{prefix}/#", "duration_seconds": 1}, {topic: "200"})
    assert [t["value"] for t in result.structured_content["topics"]] == ["200"]
    assert feed.snapshot()[topic][0] == "200"  # updated during the scan

    await publish_and_wait(feed, publisher, {topic: "300"})  # and after it: still subscribed
    assert (await ok("read_topics", {"topic_paths": [topic]}))["readings"][0]["value"] == "300"


async def test_concurrent_scans(feed, publisher, prefix):
    a, b = await asyncio.gather(
        scan(publisher, {"topic_filter": f"{prefix}/#", "duration_seconds": 1}, {f"{prefix}/x": "1"}),
        scan(publisher, {"topic_filter": f"{prefix}/#", "duration_seconds": 2}, {f"{prefix}/y": "2"}))
    assert {t["topic"] for t in a.structured_content["topics"]} <= {f"{prefix}/x", f"{prefix}/y"}
    assert {t["topic"] for t in b.structured_content["topics"]} == {f"{prefix}/x", f"{prefix}/y"}


async def test_scan_limits_and_errors(feed, publisher, prefix):
    result = await scan(publisher, {"topic_filter": f"{prefix}/#", "duration_seconds": -5}, {f"{prefix}/x": "1"})
    assert result.structured_content["duration_seconds"] == 1  # clamped; negative used to crash time.sleep
    quiet = await scan(publisher, {"topic_filter": f"{prefix}/quiet/#", "duration_seconds": 1}, {f"{prefix}/x": "1"})
    assert quiet.is_error and "No messages on" in _text(quiet)  # hearing nothing isn't a result (#3)
    assert "Not a valid topic filter" in await error("scan_topics", {"topic_filter": "a/#/b"})
    assert "Not a valid topic filter" in await error("scan_topics", {"topic_filter": "a/b+"})


async def test_scan_topic_limit(feed, publisher, prefix, monkeypatch):
    monkeypatch.setattr(server, "MAX_SCAN_TOPICS", 1)
    result = await scan(publisher, {"topic_filter": f"{prefix}/#", "duration_seconds": 1},
                        {f"{prefix}/x": "1", f"{prefix}/y": "2"})
    out = result.structured_content
    assert (len(out["topics"]), out["unique_topics"], out["complete"]) == (1, 2, False)


async def test_scan_unreachable_broker_is_an_error(monkeypatch):
    monkeypatch.setattr(server, "_feed", server.Feed("127.0.0.1", 1))
    monkeypatch.setattr(server, "SCAN_CONNECT_TIMEOUT_S", 1)
    assert "Cannot connect to the MQTT broker at 127.0.0.1:1" in await error("scan_topics", {"duration_seconds": 1})


async def test_values_report_a_lost_broker(prefix, monkeypatch):
    # Cached values stay readable while the broker is down, flagged as such (#2).
    own = BrokerProcess()
    own.start()
    f = server.Feed("127.0.0.1", own.port, f"{prefix}/#")
    monkeypatch.setattr(server, "_feed", f)
    f.start()
    try:
        await wait_for(lambda: f.connected)
        await asyncio.sleep(0.2)
        from conftest import Publisher
        p = Publisher(own.port)
        p.publish(f"{prefix}/v", "1")
        p.close()
        await wait_for(lambda: f"{prefix}/v" in f.snapshot())
        own.stop()
        await wait_for(lambda: not f.connected)
        out = await ok("read_topics", {"topic_paths": [f"{prefix}/v"]})
        assert out["broker"]["connected"] is False
        assert out["readings"][0]["value"] == "1"
    finally:
        f.stop()
        own.stop()
