"""In-process pub/sub used by the SSE endpoint.

Consumers subscribe per job and receive full job snapshots, so the client never
has to reconcile partial updates. Slow consumers are dropped rather than
blocking the producer.

Everything here runs on the event loop thread: publish() is awaited from async
request handlers, and create_queue() is called synchronously by the SSE
generator before its first yield.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue]] = {}

    async def publish(self, job_id: str, payload: dict) -> None:
        for queue in list(self._subscribers.get(job_id, ())):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                # Drop the oldest snapshot; the one being published is fresher.
                try:
                    queue.get_nowait()
                    queue.put_nowait(payload)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

    def create_queue(self, job_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=32)
        self._subscribers.setdefault(job_id, set()).add(queue)
        return queue

    def remove_queue(self, job_id: str, queue: asyncio.Queue) -> None:
        self._subscribers.get(job_id, set()).discard(queue)

    async def subscribe(self, job_id: str) -> AsyncIterator[dict]:
        queue = self.create_queue(job_id)
        try:
            while True:
                yield await queue.get()
        finally:
            self.remove_queue(job_id, queue)

bus = EventBus()
