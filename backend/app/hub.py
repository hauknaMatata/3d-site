"""In-process pub/sub for WebSocket clients (one channel per project) + user confirmations."""
from __future__ import annotations

import asyncio
import time
import uuid
from collections import defaultdict
from typing import Any


class Hub:
    def __init__(self) -> None:
        self.subs: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self.confirms: dict[str, dict[str, Any]] = {}

    def subscribe(self, pid: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self.subs[pid].add(q)
        return q

    def unsubscribe(self, pid: str, q: asyncio.Queue) -> None:
        self.subs[pid].discard(q)

    def publish(self, pid: str, event: dict[str, Any]) -> None:
        event.setdefault("ts", time.time())
        for q in list(self.subs.get(pid, ())):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    async def confirm(self, pid: str, title: str, detail: str, timeout: float = 300) -> bool:
        """Ask the user (all open clients of the project) to approve a dangerous action."""
        cid = uuid.uuid4().hex[:10]
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self.confirms[cid] = {"future": fut, "project_id": pid, "title": title, "detail": detail}
        self.publish(pid, {"type": "confirm_request", "id": cid, "title": title, "detail": detail})
        try:
            return bool(await asyncio.wait_for(fut, timeout))
        except asyncio.TimeoutError:
            return False
        finally:
            self.confirms.pop(cid, None)
            self.publish(pid, {"type": "confirm_closed", "id": cid})

    def answer(self, cid: str, approve: bool) -> bool:
        c = self.confirms.get(cid)
        if not c or c["future"].done():
            return False
        c["future"].set_result(approve)
        return True


hub = Hub()
