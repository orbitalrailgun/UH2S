"""Тесты cron-матчера планировщика (app/scheduler.py) — офлайн, без БД."""
import datetime
import unittest

from app.scheduler import cron_matches, validate_cron, next_run


def dt(y, mo, d, h, mi):
    return datetime.datetime(y, mo, d, h, mi)


class TestValidateCron(unittest.TestCase):
    def test_ok(self):
        for expr in ("* * * * *", "*/5 * * * *", "0 9 * * 1-5", "30 8,20 1 1 *", "0 0 1-7 * 0"):
            self.assertTrue(validate_cron(expr)[0], expr)

    def test_bad(self):
        for expr in ("", "* * * *", "60 * * * *", "* 24 * * *", "* * 0 * *", "* * * 13 *", "*/0 * * * *", "a * * * *"):
            self.assertFalse(validate_cron(expr)[0], expr)


class TestCronMatches(unittest.TestCase):
    def test_star(self):
        self.assertTrue(cron_matches("* * * * *", dt(2026, 7, 3, 12, 34)))

    def test_minute_hour(self):
        self.assertTrue(cron_matches("30 9 * * *", dt(2026, 7, 3, 9, 30)))
        self.assertFalse(cron_matches("30 9 * * *", dt(2026, 7, 3, 9, 31)))
        self.assertFalse(cron_matches("30 9 * * *", dt(2026, 7, 3, 10, 30)))

    def test_step(self):
        self.assertTrue(cron_matches("*/5 * * * *", dt(2026, 7, 3, 0, 0)))
        self.assertTrue(cron_matches("*/5 * * * *", dt(2026, 7, 3, 0, 15)))
        self.assertFalse(cron_matches("*/5 * * * *", dt(2026, 7, 3, 0, 7)))

    def test_list_and_range(self):
        self.assertTrue(cron_matches("0 8,20 * * *", dt(2026, 7, 3, 20, 0)))
        self.assertTrue(cron_matches("0 9 * * 1-5", dt(2026, 7, 3, 9, 0)))    # 2026-07-03 = пятница
        self.assertFalse(cron_matches("0 9 * * 1-5", dt(2026, 7, 4, 9, 0)))   # суббота

    def test_dow_sunday_zero_and_seven(self):
        sunday = dt(2026, 7, 5, 6, 0)   # 2026-07-05 = воскресенье
        self.assertTrue(cron_matches("0 6 * * 0", sunday))
        self.assertTrue(cron_matches("0 6 * * 7", sunday))
        self.assertFalse(cron_matches("0 6 * * 1", sunday))

    def test_dom_or_dow_both_restricted(self):
        # оба заданы -> совпадение по любому (стандартный Vixie cron)
        # 2026-07-03 = пятница (dow=5), день=3
        self.assertTrue(cron_matches("0 0 3 * 1", dt(2026, 7, 3, 0, 0)))   # совпал day=3
        self.assertTrue(cron_matches("0 0 15 * 5", dt(2026, 7, 3, 0, 0)))  # совпал dow=пятница
        self.assertFalse(cron_matches("0 0 15 * 1", dt(2026, 7, 3, 0, 0))) # ни день, ни dow


class TestNextRun(unittest.TestCase):
    def test_next_matches(self):
        base = dt(2026, 7, 3, 9, 31)
        nxt = next_run("*/5 * * * *", base)
        self.assertIsNotNone(nxt)
        self.assertTrue(cron_matches("*/5 * * * *", nxt))
        self.assertGreater(nxt, base)
        self.assertEqual(nxt, dt(2026, 7, 3, 9, 35))

    def test_daily(self):
        nxt = next_run("0 9 * * *", dt(2026, 7, 3, 10, 0))
        self.assertEqual(nxt, dt(2026, 7, 4, 9, 0))


if __name__ == "__main__":
    unittest.main()
