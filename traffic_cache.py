"""In-memory telemetry with transactional, retryable write batching."""
from collections import defaultdict
from datetime import datetime
from functools import wraps
from threading import RLock


class TrafficCache:
    def __init__(self):
        self.lock = RLock()
        self.pending = defaultdict(lambda: [0, 0])
        self.rates = {}

    def add(self, mac, upload, download, at=None):
        upload, download = max(0, int(upload)), max(0, int(download))
        if not upload and not download:
            return
        at = at or datetime.now()
        key = (mac, at.strftime('%Y-%m-%d %H:00:00'), at.strftime('%Y-%m-%d'))
        with self.lock:
            self.pending[key][0] += upload
            self.pending[key][1] += download

    def set_rates(self, mac, upload, download):
        with self.lock:
            self.rates[mac] = (max(0, upload), max(0, download))

    def overlay(self, row):
        if row is None:
            return None
        with self.lock:
            rates = self.rates.get(row['mac'], (0, 0)) if row['is_online'] else (0, 0)
        row['current_upload_rate'], row['current_download_rate'] = rates
        return row

    def flush(self, writer):
        with self.lock:
            if not self.pending:
                return 0
            rows = [(mac, hour, date, values[0], values[1])
                    for (mac, hour, date), values in self.pending.items()]
            writer(rows)  # A failed transaction retains the complete batch for retry.
            self.pending.clear()
            return len(rows)

    def barrier(self, function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            with self.lock:
                return function(*args, **kwargs)
        return wrapped
