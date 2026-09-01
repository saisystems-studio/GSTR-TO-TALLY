from django.core.cache import cache
from django.test import SimpleTestCase

from gst_tally.tally.import_lock import TallyImportLock


class TallyImportLockTests(SimpleTestCase):
    def tearDown(self):
        cache.clear()

    def test_concurrent_owner_is_rejected_and_next_owner_is_allowed_after_release(self):
        first = TallyImportLock(81)
        second = TallyImportLock(81)
        self.assertTrue(first.acquire())
        self.assertFalse(second.acquire())
        first.release()
        self.assertTrue(second.acquire())
        second.release()

    def test_exception_path_releases_lock(self):
        lock = TallyImportLock(81)
        with self.assertRaises(ValueError):
            with lock:
                raise ValueError("test failure")
        replacement = TallyImportLock(81)
        self.assertTrue(replacement.acquire())
        replacement.release()

    def test_expired_or_crashed_owner_can_be_recovered(self):
        abandoned = TallyImportLock(81)
        self.assertTrue(abandoned.acquire())
        abandoned._stop.set()  # simulate a dead worker: heartbeat stops, cache TTL owns recovery
        cache.delete(abandoned.key)  # deterministic expiry without sleeping in the test
        replacement = TallyImportLock(81)
        self.assertTrue(replacement.acquire())
        replacement.release()
