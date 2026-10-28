"""Small in-process token bucket for the single-replica local agent."""
import math
import time


class RateLimiter:
    def __init__(self, per_minute: int, burst: int):
        self.per_minute = max(0, per_minute)
        self.burst = max(0, burst)
        self.state = {}

    def allow(self, key: str, now: float | None = None) -> tuple[bool, int]:
        if not self.per_minute or not self.burst:
            return True, 0
        now = time.monotonic() if now is None else now
        tokens, updated = self.state.get(key, (float(self.burst), now))
        rate = self.per_minute / 60.0
        tokens = min(float(self.burst), tokens + max(0.0, now - updated) * rate)
        if tokens >= 1.0:
            self.state[key] = (tokens - 1.0, now)
            return True, 0
        self.state[key] = (tokens, now)
        retry = max(1, math.ceil((1.0 - tokens) / rate))
        return False, retry
