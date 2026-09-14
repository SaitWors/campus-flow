"""Web Push with standard encryption and a fixed allowlist of browser providers."""
import base64
import ipaddress
import json
import re
import socket
from datetime import datetime, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pywebpush import webpush, WebPushException

DEFAULTS = {
    'schedule': True, 'queue': True, 'announcements': True,
    'important_popups': True, 'show_details': False,
    'quiet_enabled': False, 'quiet_start': '22:00', 'quiet_end': '08:00',
    'timezone': 'Europe/Moscow', 'language': 'ru',
}


def b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode('ascii')


def decode(value):
    if not re.fullmatch(r'[A-Za-z0-9_-]+={0,2}', value):
        raise ValueError('invalid_push_key')
    return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))


def validate_endpoint(endpoint):
    url = urlsplit(endpoint)
    host = url.hostname or ''
    # Edge/WNS uses /w/?token=...; other providers do not need query parameters.
    wns = bool(re.fullmatch(r'[a-z0-9-]+\.notify\.windows\.com', host) and url.path == '/w/')
    provider = (
        (host == 'fcm.googleapis.com' and url.path.startswith('/fcm/send/')) or
        (host == 'updates.push.services.mozilla.com' and url.path.startswith('/wpush/v2/')) or
        (re.fullmatch(r'[a-z0-9-]+\.push\.apple\.com', host) and url.path.startswith('/')) or wns
    )
    if (not provider or url.scheme != 'https' or url.port not in (None, 443)
            or url.username or url.password or (url.query and not wns) or url.fragment
            or any(ord(c) <= 32 for c in endpoint) or len(endpoint) > 2048):
        raise ValueError('unsupported_push_endpoint')
    return host


def validate_subscription(data):
    validate_endpoint(data['endpoint'])
    key = decode(data['keys']['p256dh'])
    if len(key) != 65 or len(decode(data['keys']['auth'])) != 16:
        raise ValueError('invalid_push_key')
    ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), key)


def generate_keys():
    key = ec.generate_private_key(ec.SECP256R1())
    return (
        b64(key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption())),
        b64(key.public_key().public_bytes(serialization.Encoding.X962,
                                         serialization.PublicFormat.UncompressedPoint)),
    )


def quiet(preferences, at=None):
    if not preferences['quiet_enabled']:
        return False
    clock = (at or datetime.now(timezone.utc)).astimezone(ZoneInfo(preferences['timezone'])).strftime('%H:%M')
    start, end = preferences['quiet_start'], preferences['quiet_end']
    return start <= clock < end if start < end else clock >= start or clock < end


class RestrictedSession(requests.Session):
    def __init__(self):
        super().__init__()
        self.trust_env = False

    def request(self, method, url, **kwargs):
        host = validate_endpoint(url)
        # The host is a fixed provider domain, never an arbitrary user's DNS zone.
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
            raise ValueError('non_public_push_address')
        kwargs['allow_redirects'] = False
        kwargs['timeout'] = (3, 8)
        kwargs['verify'] = True
        return super().request(method, url, **kwargs)


def send(subscription, payload, private_key, subject, ttl):
    validate_subscription(subscription)
    with RestrictedSession() as session:
        try:
            response = webpush(
                subscription_info=subscription, data=json.dumps(payload, ensure_ascii=False),
                vapid_private_key=private_key, vapid_claims={'sub': subject},
                ttl=ttl, timeout=8, requests_session=session, headers={'Urgency': 'normal'},
            )
            return response.status_code
        except WebPushException as error:
            # Exception text can contain endpoint capabilities and payloads.
            return error.response.status_code if error.response is not None else 503
