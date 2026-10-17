"""A throwaway MQTT broker for the tests: python tests/broker.py <port>."""

import asyncio
import sys

from amqtt.broker import Broker


async def main(port: int) -> None:
    broker = Broker({"listeners": {"default": {"type": "tcp", "bind": f"127.0.0.1:{port}"}}})
    await broker.start()
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1])))
