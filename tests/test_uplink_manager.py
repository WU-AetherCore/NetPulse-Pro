"""Uplink interface resolution tests.

USB adapters are renamed to MAC-derived ``wlx*`` names by udev, so the uplink
must not assume the literal ``wlan1`` default from the environment example.
"""
import os
import unittest
from unittest.mock import patch

import uplink_manager


class UsbUplinkResolutionTest(unittest.TestCase):
    def setUp(self):
        uplink_manager._usb_cache = None
        self.addCleanup(setattr, uplink_manager, '_usb_cache', None)
        self.addCleanup(os.environ.pop, 'NETPULSE_UPLINK', None)
        os.environ.pop('NETPULSE_UPLINK', None)

    def test_prefers_default_route_device(self):
        with patch.object(uplink_manager, '_wireless_names', return_value=['wlan0', 'wlxabc123']), \
             patch.object(uplink_manager, '_default_device', return_value='wlxabc123'), \
             patch.object(uplink_manager, '_connected', return_value=False):
            self.assertEqual(uplink_manager.usb_device(), 'wlxabc123')

    def test_falls_back_to_connected_wireless_interface(self):
        with patch.object(uplink_manager, '_wireless_names', return_value=['wlan0', 'wlan2']), \
             patch.object(uplink_manager, '_default_device', return_value='eth0'), \
             patch.object(uplink_manager, '_connected', side_effect=lambda name: name == 'wlan2'):
            self.assertEqual(uplink_manager.usb_device(), 'wlan2')

    def test_onboard_radio_is_never_the_uplink(self):
        with patch.object(uplink_manager, '_wireless_names', return_value=['wlan0']), \
             patch.object(uplink_manager, '_default_device', return_value='wlan0'), \
             patch.object(uplink_manager, '_connected', return_value=True):
            self.assertEqual(uplink_manager.usb_device(), 'wlan1')

    def test_explicit_environment_wins(self):
        os.environ['NETPULSE_UPLINK'] = 'wlxfixed'
        with patch.object(uplink_manager, '_wireless_names', return_value=['wlan0', 'wlxother']), \
             patch.object(uplink_manager, '_default_device', return_value='wlxother'), \
             patch.object(uplink_manager, '_connected', return_value=False):
            self.assertEqual(uplink_manager.usb_device(), 'wlxfixed')

    def test_result_is_cached_and_refreshable(self):
        with patch.object(uplink_manager, '_wireless_names', return_value=['wlxfirst']), \
             patch.object(uplink_manager, '_default_device', return_value='wlxfirst'), \
             patch.object(uplink_manager, '_connected', return_value=False):
            self.assertEqual(uplink_manager.usb_device(), 'wlxfirst')
        with patch.object(uplink_manager, '_wireless_names', return_value=['wlxsecond']), \
             patch.object(uplink_manager, '_default_device', return_value='wlxsecond'), \
             patch.object(uplink_manager, '_connected', return_value=False):
            self.assertEqual(uplink_manager.usb_device(), 'wlxfirst')
            self.assertEqual(uplink_manager.usb_device(refresh=True), 'wlxsecond')

    def test_device_for_keeps_other_interfaces_stable(self):
        self.assertEqual(uplink_manager.device_for('onboard'), 'wlan0')
        self.assertEqual(uplink_manager.device_for('wired'), 'eth0')
        self.assertEqual(uplink_manager.device_for('unknown'), '')


if __name__ == '__main__':
    unittest.main()
