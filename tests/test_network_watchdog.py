import unittest
from network_watchdog import decide, resolve_uplink
import tempfile
from pathlib import Path


class RecoveryPolicyTests(unittest.TestCase):
    def test_mac_resolves_new_interface_name_after_reenumeration(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            device=root/'wlxnewusb';device.mkdir()
            (device/'address').write_text('02:11:22:33:44:55\n')
            self.assertEqual(resolve_uplink('02:11:22:33:44:55', 'wlan1', root), 'wlxnewusb')
            self.assertEqual(resolve_uplink('02:11:22:33:44:66', 'wlan1', root), 'wlan1')

    def test_healthy_resets_failures(self):
        self.assertEqual(decide(True, True, True, 5, 0, 1000), (0, 'healthy'))

    def test_no_reset_when_usb_missing(self):
        self.assertEqual(decide(False, False, False, 5, 0, 1000), (0, 'missing'))

    def test_single_failure_does_not_disconnect(self):
        self.assertEqual(decide(True, False, False, 0, 0, 1000), (1, 'wait'))

    def test_reconnect_only_after_three_failures(self):
        self.assertEqual(decide(True, False, False, 2, 0, 1000), (3, 'reconnect'))

    def test_cooldown_prevents_reconnect_loop(self):
        self.assertEqual(decide(True, False, False, 8, 900, 1000), (9, 'wait'))

    def test_missing_route_uses_reapply(self):
        self.assertEqual(decide(True, True, False, 2, 0, 1000), (3, 'reapply'))
