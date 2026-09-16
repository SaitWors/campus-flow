"""Read-only production checks. Never creates accounts, lessons or queues."""
import argparse
import json
import time
import urllib.error
import urllib.request

SCHEMAS = {'auth': 3, 'schedule': 3, 'queues': 2, 'notifications': 2}


def check(base, expected_commit=None):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    timings = []
    def get(path, expected=200):
        started = time.monotonic()
        try:
            response = opener.open(base.rstrip('/')+path, timeout=10)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            if response.status != expected:
                raise RuntimeError(f'{path}: HTTP {response.status}, expected {expected}')
            data = response.read(2*1024*1024)
            timings.append(round((time.monotonic()-started)*1000, 2))
            return data, response.headers
    get('/health')
    web = json.loads(get('/version')[0])
    if expected_commit and web['commit'] != expected_commit:
        raise RuntimeError('Web image does not match the requested commit')
    versions = {}
    for service, schema in SCHEMAS.items():
        data, headers = get('/api/'+service+'/version')
        version = json.loads(data)
        if version['schema'] != schema or version['commit'] != web['commit']:
            raise RuntimeError('Service version mismatch: '+service)
        if headers.get('Cache-Control') != 'no-store' or not headers.get('X-Request-ID'):
            raise RuntimeError('Missing API response protections')
        versions[service] = version
    _, headers = get('/')
    for header in ['Content-Security-Policy','X-Content-Type-Options','X-Frame-Options','Referrer-Policy','Permissions-Policy']:
        if not headers.get(header):
            raise RuntimeError('Missing gateway header: '+header)
    if 'no-store' not in headers.get('Cache-Control', ''):
        raise RuntimeError('Application HTML is cacheable')
    get('/api/schedule/guest/settings')
    calendar, _ = get('/api/schedule/guest/calendar.ics')
    if not calendar.startswith(b'BEGIN:VCALENDAR'):
        raise RuntimeError('Public calendar is unavailable')
    get('/api/auth/users', 401)
    get('/internal/events', 404)
    result = {'commit': web['commit'], 'services': versions, 'requests':len(timings),
              'max_read_ms':max(timings), 'mean_read_ms':round(sum(timings)/len(timings), 2)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8080')
    parser.add_argument('--commit')
    args = parser.parse_args()
    check(args.base_url, args.commit)
