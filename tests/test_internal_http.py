import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from services.common import core


def test_internal_requests_reuse_connection_and_shutdown_releases_pool(monkeypatch):
    monkeypatch.setenv('INTERNAL_TOKEN', 'test-secret-that-is-at-least-32-characters')
    peers = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def do_GET(self):
            peers.append(self.client_address[1])
            payload = json.dumps({'ok': True}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = 'http://127.0.0.1:' + str(server.server_port)
        assert core.remote(base, '/first') == {'ok': True}
        assert core.remote(base, '/second') == {'ok': True}
        assert len(set(peers)) == 1
        core.close_internal_client()
        assert core.remote(base, '/after-shutdown') == {'ok': True}
        assert peers[-1] != peers[0]
    finally:
        if hasattr(core, 'close_internal_client'):
            core.close_internal_client()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
