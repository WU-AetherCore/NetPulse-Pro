import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
from flask import Flask
import network_settings as settings


class HotspotSettingsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.config = Path(self.directory.name) / 'hostapd.conf'
        self.original = 'ssid=Old\nwpa_passphrase=oldpassword\nieee80211n=0\nwmm_enabled=0\nchannel=6\n'
        self.config.write_text(self.original)
        self.config_patch = patch.object(settings, 'AP_CONFIG', self.config)
        self.config_patch.start()
        self.addCleanup(self.config_patch.stop)
        self.addCleanup(self.directory.cleanup)
        self.app = Flask(__name__)
        self.app.register_blueprint(settings.network_settings)
        self.client = self.app.test_client()

    def test_rejects_invalid_settings_without_writing(self):
        for data in ({'ssid': 'bad\nchannel=11'}, {'ssid': '测试' * 12},
                     {'ssid': 'Valid', 'password': 'short'}, {'ssid': 'Valid', 'channel': '99'}):
            self.assertEqual(self.client.post('/api/network/hotspot', json=data).status_code, 400)
        self.assertEqual(self.config.read_text(), self.original)

    def test_blank_password_retains_secret_and_compatibility(self):
        with patch.object(settings.threading, 'Timer') as timer:
            response = self.client.post('/api/network/hotspot', json={'ssid': 'New', 'password': '', 'channel': '11'})
            try:
                self.assertEqual(response.status_code, 200)
                updated, original = timer.call_args.kwargs['args']
                self.assertIn('ssid=New\n', updated)
                self.assertIn('wpa_passphrase=oldpassword\n', updated)
                self.assertIn('ieee80211n=0\n', updated)
                self.assertEqual(original, self.original)
            finally:
                settings.change_lock.release()

    def test_failed_start_restores_original(self):
        settings.change_lock.acquire()
        with patch.object(settings, 'run', return_value=Mock(returncode=1, stdout='')):
            settings.apply_hotspot('ssid=New\n', self.original)
        self.assertEqual(self.config.read_text(), self.original)
        self.assertEqual(settings.apply_state['state'], 'error')
        self.assertFalse(settings.change_lock.locked())

    def test_rejects_cross_origin(self):
        response = self.client.post('/api/network/hotspot', json={'ssid': 'New'}, headers={'Origin': 'https://other.example'})
        self.assertEqual(response.status_code, 403)


if __name__ == '__main__':
    unittest.main()
