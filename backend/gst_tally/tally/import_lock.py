import threading
import uuid

from django.core.cache import cache


class TallyImportLock:
    """Owned cache lock with heartbeat and automatic stale recovery."""
    ttl = 60
    heartbeat_seconds = 15

    def __init__(self, batch_id):
        self.key = f"tally-import-batch-{batch_id}"
        self.owner = uuid.uuid4().hex
        self.acquired = False
        self._stop = threading.Event()
        self._thread = None

    def acquire(self):
        self.acquired = cache.add(self.key, self.owner, timeout=self.ttl)
        if self.acquired:
            self._thread = threading.Thread(target=self._heartbeat, name=f"{self.key}-heartbeat", daemon=True)
            self._thread.start()
        return self.acquired

    def _heartbeat(self):
        while not self._stop.wait(self.heartbeat_seconds):
            if cache.get(self.key) != self.owner:
                return
            cache.set(self.key, self.owner, timeout=self.ttl)

    def release(self):
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=1)
        if self.acquired and cache.get(self.key) == self.owner:
            cache.delete(self.key)
        self.acquired = False

    def __enter__(self):
        if not self.acquire():
            raise RuntimeError("TALLY_IMPORT_IN_PROGRESS")
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.release()
        return False
