import unittest
from unittest.mock import patch
from flask import Flask
from integrations import adguard_url, integrations

class AdguardLinks(unittest.TestCase):
    def test_addresses_follow_access_host(self):
        for host in ('192.168.111.1', '192.168.112.1', 'netpulse.local'):
            self.assertEqual(adguard_url(f'http://{host}:8081/'), f'http://{host}:3000/')
        self.assertEqual(adguard_url('http://[::1]:8081/'), 'http://[::1]:3000/')

    def test_proxy_override_and_port(self):
        self.assertEqual(adguard_url('http://localhost/', 'https://dns.example.com/admin/'), 'https://dns.example.com/admin/')
        self.assertEqual(adguard_url('http://localhost/', port=8443, scheme='https'), 'https://localhost:8443/')

    def test_invalid_config(self):
        for override in ('javascript:alert(1)', 'http://user:password@example.com/', 'https://example.com:invalid/'):
            with self.assertRaises(ValueError):
                adguard_url('http://localhost/', override)
        for port in (0, 65536, 'invalid'):
            with self.assertRaises(ValueError):
                adguard_url('http://localhost/', port=port)

    def test_redirect_and_config_failure(self):
        app=Flask(__name__);app.register_blueprint(integrations)
        with patch.dict('os.environ', {}, clear=True):
            response=app.test_client().get('/adguard', base_url='http://192.168.112.1:8081')
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.location, 'http://192.168.112.1:3000/')
        with patch.dict('os.environ', {'NETPULSE_ADGUARD_WEB_PORT':'wrong'}, clear=True):
            self.assertEqual(app.test_client().get('/adguard').status_code, 503)

if __name__ == '__main__':
    unittest.main()
