# Changelog

Each release is a git tag, `vX.Y.Z`. Issue numbers refer to this repo unless they name another.

## Unreleased

- **`uninstall_service.ps1` removes the service** (found upgrading the Skytap VM). It passed the name unquoted, so nssm got `AVEVA`, removed nothing, and the script still said "AVEVA Demo MqttMCP removed." It now passes the name as one argument and fails with nssm's exit code if the service is still there.

## 0.2.0

Breaking: tool names and results changed. The galaxy site plugin in smslavin/graccess-mcp needs its matching release.

- **Scans no longer freeze the cache** (#1). `scan_topics` listens on a short-lived client of its own. Before, a scan on the persistent subscription's filter (both default to `#`) unsubscribed it, and while it ran its callback kept the cache from updating, so every later read served values from scan time as current. Concurrent scans no longer end each other.
- **Values carry their age; the cache is bounded** (#2). Each value has `received_at` and `age_s`, and every result has `broker`: whether it's connected, when the newest message arrived, how many topics are cached, and how many messages were dropped. The cache holds at most `MQTT_MAX_TOPICS` topics (default 10000) and cuts payloads over 2000 characters. Tools copy the cache instead of deep-copying the tree under the lock paho's thread needs. `duration_seconds` is clamped to 1-30; a negative one used to crash.
- **Failures are tool errors** (#3). Nothing heard yet, an unknown topic, a read where no topic has a value, a scan that hears nothing, an invalid filter and an unreachable broker all raise, so the client sees `isError` instead of a cached, audited "success".
- **Shared verbs with opcua-mcp, structured output** (#5). `browse_topics(topic_path="")` replaces `list_topics` and `list_subtopics`; `browse_topic_tree(topic_path="")` replaces `get_full_topic_tree` and can start below a topic; `read_topics(topic_paths)` replaces `read_topic_value`. Every tool returns structured content; `scan_topics` returns a flat, sorted topic list like the others instead of its own nested JSON.
- **Tests and pinned dependencies** (#4). `requirements.txt` pins exact versions; `tests/` runs every tool against an amqtt broker, including the scan regression from #1; CI runs them on Python 3.11 and 3.13.
- The feed starts when `server.py` runs, not when it's imported.

## 0.1.0

- **Its own repo.** mqtt-mcp moved out of smslavin/mcp-servers, with its history, so it can be versioned and tagged. The code is unchanged from mcp-servers c37ae66.
- Already there before the move: tools declared read-only, a broker connection that retries, and an installer that keeps the service's port (smslavin/mcp-servers#7).
