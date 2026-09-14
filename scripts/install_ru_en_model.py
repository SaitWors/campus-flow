"""Install exactly RU -> EN 1.9, with bounded retries and resumable downloads."""
import hashlib
import json
import os
import time
import zipfile
from pathlib import Path
import requests

MODEL_URL = 'https://argos-net.com/v1/translate-ru_en-1_9.argosmodel'
# Downloaded from the official index URL and verified in CI run 34857579640.
MODEL_SHA256 = 'e9ba8bf722d10a4a4c39f74289d5938fd47eac08dbe4ed0afd22d89445a5c3ac'
MAX_BYTES = 400 * 1024 * 1024


def checksum(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def verify(path):
    actual = checksum(path)
    if actual != MODEL_SHA256:
        raise ValueError('model_checksum_mismatch')
    with zipfile.ZipFile(path) as archive:
        names = [n for n in archive.namelist() if n.endswith('/metadata.json') or n == 'metadata.json']
        if len(names) != 1:
            raise ValueError('model_metadata_missing')
        data = json.loads(archive.read(names[0]))
        if (data['from_code'], data['to_code'], data['package_version']) != ('ru', 'en', '1.9'):
            raise ValueError('wrong_translation_direction')
        if archive.testzip():
            raise ValueError('model_archive_corrupted')
    return actual


def download(path, session=None, wait=time.sleep):
    session = session or requests.Session()
    session.trust_env = False
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            verify(path)
            return path
        except (ValueError, zipfile.BadZipFile, KeyError):
            path.unlink()
    partial = path.with_suffix('.part')
    for attempt in range(1, 6):
        try:
            size = partial.stat().st_size if partial.exists() else 0
            with session.get(MODEL_URL, headers={'Range': f'bytes={size}-'} if size else {},
                             stream=True, timeout=(15, 45), allow_redirects=False) as response:
                if response.status_code not in (200, 206):
                    raise ValueError('model_download_http_error')
                resume = response.status_code == 206
                if resume and not response.headers.get('Content-Range', '').startswith(f'bytes {size}-'):
                    partial.unlink(missing_ok=True)
                    raise ValueError('invalid_resume_range')
                if not resume:
                    size = 0
                expected = int(response.headers.get('Content-Length', 0))
                if size + expected > MAX_BYTES:
                    raise ValueError('model_too_large')
                received = 0
                with partial.open('ab' if resume else 'wb') as output:
                    for block in response.iter_content(1024 * 1024):
                        received += len(block)
                        if size + received > MAX_BYTES:
                            raise ValueError('model_too_large')
                        output.write(block)
                if expected and received != expected:
                    raise ValueError('incomplete_model_download')
            try:
                actual = verify(partial)
            except (ValueError, zipfile.BadZipFile, KeyError):
                partial.unlink(missing_ok=True)
                raise
            partial.replace(path)
            print('Verified RU -> EN 1.9 SHA256:', actual, flush=True)
            return path
        except (requests.RequestException, OSError, ValueError, zipfile.BadZipFile, KeyError):
            print(f'RU -> EN download attempt {attempt}/5 failed', flush=True)
            if attempt == 5:
                raise RuntimeError('RU -> EN model unavailable. The base stack works without the translation profile.') from None
            wait(min(20, attempt * 4))


def main():
    import argostranslate.package
    path = download(Path(os.getenv('MODEL_CACHE', '/tmp/model-cache')) / 'ru_en-1.9.argosmodel')
    argostranslate.package.install_from_path(path)
    installed = argostranslate.package.get_installed_packages()
    if {(p.from_code, p.to_code) for p in installed} != {('ru', 'en')}:
        raise RuntimeError('Expected exactly one RU -> EN model')


if __name__ == '__main__':
    main()
