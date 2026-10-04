"""Links to services on the same board, without fixed deployment addresses."""
import os
from urllib.parse import urlsplit, urlunsplit
from flask import Blueprint, redirect, request

integrations = Blueprint('integrations', __name__)

def adguard_url(host_url, override='', port=3000, scheme='http'):
    if override:
        parsed = urlsplit(override)
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError('Invalid AdGuard port')
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('AdGuard web URL must be an HTTP(S) URL without credentials')
        return override
    hostname = urlsplit(host_url).hostname
    if not hostname or scheme not in ('http', 'https') or not 1 <= int(port) <= 65535:
        raise ValueError('Invalid AdGuard web address')
    if ':' in hostname:
        hostname = '[' + hostname + ']'
    return urlunsplit((scheme, f'{hostname}:{int(port)}', '/', '', ''))

@integrations.get('/adguard')
def open_adguard():
    try:
        target = adguard_url(request.host_url,
                             os.environ.get('NETPULSE_ADGUARD_WEB_URL', ''),
                             os.environ.get('NETPULSE_ADGUARD_WEB_PORT', '3000'),
                             os.environ.get('NETPULSE_ADGUARD_WEB_SCHEME', 'http'))
    except ValueError:
        return 'AdGuard Home 地址配置有误，请检查服务配置。', 503
    return redirect(target, code=302)
