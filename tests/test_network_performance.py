import tempfile
import unittest
from pathlib import Path
from network_performance import online_mask, default_interface, apply


class PerformanceTests(unittest.TestCase):
    def test_sparse_cores_and_large_masks(self):
        self.assertEqual(online_mask('0-3'), 'f')
        self.assertEqual(online_mask('0,2,35'), '8,00000005')
        with self.assertRaises(ValueError):
            online_mask('4-2')

    def test_route_does_not_tune_downstream_or_tunnel(self):
        routes = ('Iface Destination Gateway Flags RefCnt Use Metric Mask\n'
                  'eth0 00000000 01000000 0003 0 0 1 00000000\n'
                  'tailscale0 00000000 01000000 0003 0 0 0 00000000\n'
                  'wlxfirst 00000000 01000000 0003 0 0 600 00000000\n'
                  'wlxsecond 00000000 01000000 0003 0 0 100 00000000\n')
        self.assertEqual(default_interface(routes), 'wlxsecond')

    def test_tunes_usb_and_leaves_multi_queue_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            net_root = root / 'usb2' / 'net'
            device = net_root / 'wlxusb'
            queue = device / 'queues' / 'rx-0'
            queue.mkdir(parents=True)
            (device / 'device').mkdir()
            target = queue / 'rps_cpus'
            target.write_text('0\n')
            cpu = root / 'online'; cpu.write_text('0-3')
            route = root / 'route'
            route.write_text('Iface Destination Gateway Flags RefCnt Use Metric Mask\n'
                             'wlxusb 00000000 01000000 0003 0 0 100 00000000\n')
            self.assertTrue(apply(net_root, route, cpu)['applied'])
            self.assertEqual(target.read_text(), 'f\n')
            (device / 'queues' / 'rx-1').mkdir()
            self.assertEqual(apply(net_root, route, cpu)['reason'], 'multiple_queues')
