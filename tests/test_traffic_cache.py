import logging
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
import database as db
from telemetry_logging import PollingFilter


class BatchedTrafficTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = str(Path(self.directory.name) / 'test.db')
        self.patch = patch.object(db, 'DB_PATH', self.path)
        self.patch.start(); self.addCleanup(self.patch.stop)
        with db.traffic_cache.lock:
            db.traffic_cache.pending.clear(); db.traffic_cache.rates.clear()
        db.init_db()
        db.upsert_device('02:00:00:00:00:01', '192.168.111.2')
        db.reset_current_period()
        self.mac = '02:00:00:00:00:01'

    def query(self, sql):
        with closing(sqlite3.connect(self.path)) as conn:
            return conn.execute(sql).fetchall()

    def test_many_samples_one_commit_and_live_rates_without_disk_writes(self):
        original = db.get_db
        commits = []
        def traced():
            connection = original()
            connection.set_trace_callback(lambda sql: commits.append(sql) if sql == 'COMMIT' else None)
            return connection
        with patch.object(db, 'get_db', traced):
            for _ in range(20):
                db.update_traffic(self.mac, 100, 200)
                db.update_current_rates(self.mac, 12, 24)
            self.assertEqual(commits, [])
            self.assertEqual(db.get_device_by_mac(self.mac)['current_upload_rate'], 12)
            self.assertEqual(db.get_summary()['avg_download_rate'], 24)
            self.assertEqual(self.query('SELECT total_upload FROM devices')[0][0], 0)
            self.assertEqual(db.flush_traffic(), 1)
            self.assertEqual(len(commits), 1)
            self.assertEqual(db.flush_traffic(), 0)
        self.assertEqual(self.query('SELECT total_upload,total_download FROM devices'), [(2000,4000)])
        self.assertEqual(self.query('SELECT current_upload_rate,current_download_rate FROM devices'), [(0,0)])

    def test_sql_failure_rolls_back_and_retry_is_exactly_once(self):
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("CREATE TRIGGER fail_batch BEFORE INSERT ON traffic_daily BEGIN SELECT RAISE(ABORT,'simulate disk failure'); END")
        db.update_traffic(self.mac, 10, 20)
        with self.assertRaises(sqlite3.DatabaseError):
            db.flush_traffic()
        self.assertEqual(self.query('SELECT total_upload,total_download FROM devices'), [(0,0)])
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute('DROP TRIGGER fail_batch')
        db.flush_traffic(); db.flush_traffic()
        self.assertEqual(self.query('SELECT total_upload,total_download FROM devices'), [(10,20)])

    def test_samples_keep_their_original_hour_and_date(self):
        db.traffic_cache.add(self.mac, 10, 20, datetime(2026,10,10,23,59))
        db.traffic_cache.add(self.mac, 30, 40, datetime(2026,10,11,0,0))
        db.flush_traffic()
        self.assertEqual(self.query('SELECT hour,upload,download FROM traffic_hourly ORDER BY hour'),
                         [('2026-10-10 23:00:00',10,20),('2026-10-11 00:00:00',30,40)])
        self.assertEqual(self.query('SELECT date,upload FROM traffic_daily ORDER BY date'),
                         [('2026-10-10',10),('2026-10-11',30)])

    def test_reset_saves_pending_bytes_to_previous_period(self):
        db.update_traffic(self.mac, 100, 200)
        db.reset_current_period()
        self.assertEqual(self.query('SELECT total_upload,total_download FROM devices'), [(0,0)])
        self.assertEqual(self.query('SELECT total_upload,total_download FROM traffic_periods WHERE is_current=0'), [(100,200)])
        self.assertEqual(db.flush_traffic(), 0)

    def test_unchanged_device_observation_does_not_rewrite_last_seen(self):
        before = self.query('SELECT last_seen FROM devices')[0][0]
        with patch.object(db.time, 'time', return_value=before+5):
            db.upsert_device(self.mac, '192.168.111.2')
        self.assertEqual(self.query('SELECT last_seen FROM devices')[0][0], before)
        with patch.object(db.time, 'time', return_value=before+31):
            db.upsert_device(self.mac, '192.168.111.2')
        self.assertEqual(self.query('SELECT last_seen FROM devices')[0][0], before+31)
        self.assertEqual(len(self.query('SELECT * FROM connection_events')), 1)
        db.mark_device_offline(self.mac)
        self.assertEqual(db.get_device_by_mac(self.mac)['current_upload_rate'], 0)


class PollingLogTests(unittest.TestCase):
    def test_keeps_errors_and_mutations(self):
        filter = PollingFilter()
        def allowed(message, level=logging.INFO):
            return filter.filter(logging.LogRecord('werkzeug',level,'',0,message,(),None))
        self.assertFalse(allowed('127.0.0.1 - "GET /api/summary HTTP/1.1" 200 -'))
        self.assertTrue(allowed('127.0.0.1 - "POST /api/device/update HTTP/1.1" 200 -'))
        self.assertTrue(allowed('127.0.0.1 - "GET /api/summary HTTP/1.1" 500 -'))
        self.assertTrue(allowed('disk I/O error', logging.ERROR))
