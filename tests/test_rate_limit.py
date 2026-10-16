"""Tests for the per-visitor and site-wide question limits.

Run with:  python -m unittest discover tests
"""
import unittest

from rate_limit import DAY, MINUTE, RateLimiter


class RateLimiterTest(unittest.TestCase):
    def test_per_minute_limit_then_recovers(self):
        limiter = RateLimiter(per_minute=2, per_day=0, global_per_day=0)
        self.assertIsNone(limiter.check("a", now=0))
        self.assertIsNone(limiter.check("a", now=1))
        message, wait = limiter.check("a", now=2)
        self.assertIn("wait", message)
        self.assertAlmostEqual(wait, MINUTE - 2)
        self.assertIsNone(limiter.check("b", now=2))  # other visitors are not affected
        self.assertIsNone(limiter.check("a", now=MINUTE + 1))

    def test_per_day_limit(self):
        limiter = RateLimiter(per_minute=0, per_day=3, global_per_day=0)
        for t in (0, 100, 200):
            self.assertIsNone(limiter.check("a", now=t))
        self.assertIn("today", limiter.check("a", now=300)[0])
        self.assertIsNone(limiter.check("a", now=DAY + 1))

    def test_site_wide_limit(self):
        limiter = RateLimiter(per_minute=0, per_day=0, global_per_day=2)
        self.assertIsNone(limiter.check("a", now=0))
        self.assertIsNone(limiter.check("b", now=1))
        self.assertIn("UTSmartBot has reached", limiter.check("c", now=2)[0])

    def test_blocked_questions_are_not_counted(self):
        limiter = RateLimiter(per_minute=1, per_day=0, global_per_day=0)
        self.assertIsNone(limiter.check("a", now=0))
        for t in range(1, 30):
            self.assertIsNotNone(limiter.check("a", now=t))
        self.assertIsNone(limiter.check("a", now=MINUTE + 1))

    def test_zero_turns_limits_off(self):
        limiter = RateLimiter(per_minute=0, per_day=0, global_per_day=0)
        for t in range(100):
            self.assertIsNone(limiter.check("a", now=t))


if __name__ == "__main__":
    unittest.main()
