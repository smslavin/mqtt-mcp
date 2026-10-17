"""Fixtures: a real MQTT broker (amqtt, in a child process), a publisher, and the server's feed."""

import asyncio
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import paho.mqtt.client as mqtt
import pytest

import server

HERE = Path(__file__).resolve().parent


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _listening(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.2)
        return s.connect_ex(("127.0.0.1", port)) == 0


class BrokerProcess:
    """tests/broker.py in a child process, restartable on the same port."""

    def __init__(self):
        self.port = _free_port()
        self._proc: subprocess.Popen | None = None

    def start(self) -> None:
        self._proc = subprocess.Popen([sys.executable, str(HERE / "broker.py"), str(self.port)],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 30
        while not _listening(self.port):
            if self._proc.poll() is not None:
                raise RuntimeError(f"broker.py exited with {self._proc.returncode}")
            if time.monotonic() > deadline:
                raise RuntimeError("broker.py did not start listening within 30 s")
            time.sleep(0.1)

    def stop(self) -> None:
        if self._proc is not None:
            self._proc.kill()
            self._proc.wait()
            self._proc = None


@pytest.fixture(scope="session")
def broker():
    b = BrokerProcess()
    b.start()
    yield b
    b.stop()


@pytest.fixture
def prefix():
    """A topic prefix of the test's own, so retained messages don't leak between tests."""
    return f"t{uuid.uuid4().hex[:8]}"


class Publisher:
    def __init__(self, port: int):
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self._client.connect("127.0.0.1", port)
        self._client.loop_start()

    def publish(self, topic: str, payload, retain: bool = False) -> None:
        self._client.publish(topic, payload, qos=1, retain=retain).wait_for_publish(5)

    def close(self) -> None:
        self._client.disconnect()
        self._client.loop_stop()


@pytest.fixture
def publisher(broker):
    p = Publisher(broker.port)
    yield p
    p.close()


async def wait_for(condition, timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.05)


@pytest.fixture
async def feed(broker, prefix, monkeypatch):
    """The server's feed, subscribed to this test's prefix and connected."""
    f = server.Feed("127.0.0.1", broker.port, f"{prefix}/#")
    monkeypatch.setattr(server, "_feed", f)
    f.start()
    await wait_for(lambda: f.connected)
    await asyncio.sleep(0.2)  # the subscription is sent from on_connect
    yield f
    f.stop()
