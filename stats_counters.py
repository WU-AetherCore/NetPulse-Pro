import ipaddress
import re
import shlex
import subprocess


def parse_counters(output, chain):
    upload = 0
    download = 0
    exists = any(line.startswith(':' + chain + ' ') for line in output.splitlines())
    for line in output.splitlines():
        match = re.match(r'^\[\d+:(\d+)\]\s+(.*)$', line)
        if not match:
            continue
        parts = shlex.split(match.group(2))
        if len(parts) < 2 or parts[:2] != ['-A', chain] or '-j' in parts or '-g' in parts:
            continue
        try:
            source = ipaddress.ip_network(parts[parts.index('-s') + 1]) if '-s' in parts else None
            destination = ipaddress.ip_network(parts[parts.index('-d') + 1]) if '-d' in parts else None
            source_specific = source is not None and source.prefixlen > 0
            destination_specific = destination is not None and destination.prefixlen > 0
            count = int(match.group(1))
            if source_specific and not destination_specific:
                upload += count
            elif destination_specific and not source_specific:
                download += count
        except (ValueError, IndexError):
            continue
    return upload, download, exists


def read_forward_counters():
    upload = download = 0
    ipv4_ok = False
    for binary, chain in [('iptables-save', 'NETSTATS'), ('ip6tables-save', 'NETSTATS6')]:
        try:
            result = subprocess.run(['sudo', binary, '-c', '-t', 'filter'],
                                    capture_output=True, text=True, timeout=5)
            if result.returncode != 0:
                continue
            sent, received, exists = parse_counters(result.stdout, chain)
            upload += sent
            download += received
            if binary == 'iptables-save':
                ipv4_ok = exists
        except (OSError, subprocess.TimeoutExpired):
            continue
    return upload, download, ipv4_ok
