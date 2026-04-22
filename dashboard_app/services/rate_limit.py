"""
Tiny in-process rate limiter.

Two buckets matter for Day 2 security:

  - /auth/login     prevents email-bombing a known address
  - /api/heartbeat  prevents a stolen shared-secret from flooding storage

Both are per-key (email or tenant_id) with a sliding window. This is
explicitly single-process (dict in memory); when we scale past one
container we'll swap to Redis. Today we run one container.
"""

import threading
import time
from collections import deque


class SlidingWindowLimiter:
    def __init__(self, max_events: int, window_seconds: int) -> None:
        self.max = max_events
        self.window = window_seconds
        self._buckets: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        floor = now - self.window
        with self._lock:
            q = self._buckets.setdefault(key, deque())
            while q and q[0] < floor:
                q.popleft()
            if len(q) >= self.max:
                return False
            q.append(now)
            return True


login_limiter = SlidingWindowLimiter(max_events=5, window_seconds=900)  # 5 / 15min / email
heartbeat_limiter = SlidingWindowLimiter(max_events=120, window_seconds=60)  # 120 / min / tenant
