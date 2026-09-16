"""Bound concurrent Argon2 memory use without changing its security parameters."""
from threading import BoundedSemaphore

from argon2 import PasswordHasher
from services.common.core import env_int, fail


class BoundedPasswordHasher:
    def __init__(self, hasher=None):
        self._hasher = hasher or PasswordHasher()
        self._slots = BoundedSemaphore(env_int('AUTH_HASH_CONCURRENCY', 4, 1, 8))
        self._timeout = env_int('AUTH_HASH_TIMEOUT', 5, 1, 30)

    def _run(self, method, *args):
        if not self._slots.acquire(timeout=self._timeout):
            fail('too_many_attempts', 429)
        try:
            return getattr(self._hasher, method)(*args)
        finally:
            self._slots.release()

    def hash(self, password):
        return self._run('hash', password)

    def verify(self, encoded, password):
        return self._run('verify', encoded, password)

    def check_needs_rehash(self, encoded):
        return self._hasher.check_needs_rehash(encoded)
