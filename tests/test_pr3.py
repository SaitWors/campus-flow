import base64
import pytest
from services.auth.security import totp

@pytest.mark.parametrize('at,expected',[(59,'94287082'),(1111111109,'07081804'),(1111111111,'14050471'),(1234567890,'89005924'),(2000000000,'69279037'),(20000000000,'65353130')])
def test_totp_rfc6238_vectors(at,expected):
    secret=base64.b32encode(b'12345678901234567890').decode()
    assert totp(secret,at//30,digits=8)==expected
