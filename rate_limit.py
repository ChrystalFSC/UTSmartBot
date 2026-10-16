"""Per-visitor and site-wide limits on questions, so a public deployment cannot use up
the free API quotas.

Set in the environment (0 turns a limit off):
    RATE_LIMIT_PER_MINUTE=6           questions per visitor per minute
    RATE_LIMIT_PER_DAY=60             questions per visitor per 24 hours
    RATE_LIMIT_GLOBAL_PER_DAY=500     questions for the whole site per 24 hours

Counts are kept in memory, so they reset when the app restarts. That is fine for one
small server; several servers would need a shared store such as Redis.
"""
import os
import threading
import time
from collections import defaultdict, deque

MINUTE, DAY = 60, 24 * 60 * 60


def env_int(name, default):
    try:
        return max(0, int(os.environ.get(name, default)))
    except ValueError:
        return default


class RateLimiter:
    def __init__(self, per_minute=6, per_day=60, global_per_day=500):
        self.per_minute, self.per_day, self.global_per_day = per_minute, per_day, global_per_day
        self.visitors = defaultdict(deque)  # visitor -> times of their questions in the last day
        self.everyone = deque()
        self.lock = threading.Lock()

    @classmethod
    def from_env(cls):
        return cls(env_int("RATE_LIMIT_PER_MINUTE", 6), env_int("RATE_LIMIT_PER_DAY", 60),
                   env_int("RATE_LIMIT_GLOBAL_PER_DAY", 500))

    def check(self, visitor, now=None):
        """Record a question if allowed. Returns None, or (message, seconds to wait)."""
        now = time.time() if now is None else now
        with self.lock:
            times = self.visitors[visitor]
            for queue in (times, self.everyone):
                while queue and queue[0] <= now - DAY:
                    queue.popleft()

            if self.global_per_day and len(self.everyone) >= self.global_per_day:
                wait = self.everyone[0] + DAY - now
                return ("UTSmartBot has reached its question limit for today. Please try again tomorrow.", wait)
            if self.per_day and len(times) >= self.per_day:
                wait = times[0] + DAY - now
                return ("You've reached today's question limit. Please try again tomorrow.", wait)
            recent = [t for t in times if t > now - MINUTE]
            if self.per_minute and len(recent) >= self.per_minute:
                wait = recent[0] + MINUTE - now
                return (f"You're asking questions a little fast. Please wait {max(1, round(wait))} seconds.", wait)

            times.append(now)
            self.everyone.append(now)
            if len(self.visitors) > 10_000:  # forget visitors with nothing left in the window
                for key in [k for k, q in self.visitors.items() if not q]:
                    del self.visitors[key]
            return None
