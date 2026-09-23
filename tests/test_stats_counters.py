import unittest
from stats_counters import parse_counters


class CounterTest(unittest.TestCase):
    def test_return_total_is_not_upload(self):
        snapshot = ''':NETSTATS - [0:0]
[10:100] -A NETSTATS -s 192.168.111.0/24
[30:900] -A NETSTATS -d 192.168.111.0/24
[40:1000] -A NETSTATS -j RETURN
'''
        self.assertEqual(parse_counters(snapshot, 'NETSTATS'), (100, 900, True))

    def test_multiple_subnets_and_ipv6(self):
        snapshot = ''':NETSTATS6 - [0:0]
[1:10] -A NETSTATS6 -s fd00:1::/64 -d ::/0
[1:20] -A NETSTATS6 -s ::/0 -d fd00:1::/64
[1:30] -A NETSTATS6 -s fd00:2::/64
[1:40] -A NETSTATS6 -d fd00:2::/64
[4:100] -A NETSTATS6 -j RETURN
'''
        self.assertEqual(parse_counters(snapshot, 'NETSTATS6'), (40, 60, True))

    def test_missing_and_empty_chain(self):
        self.assertEqual(parse_counters('', 'NETSTATS'), (0, 0, False))
        self.assertEqual(parse_counters(':NETSTATS - [0:0]', 'NETSTATS'), (0, 0, True))


if __name__ == '__main__':
    unittest.main()
