"""Disposable loopback-only browser fixture. No real tokens or Telegram calls."""
import tempfile
import sys
from datetime import datetime, timezone
from pathlib import Path

from aiohttp import web
from habitbot.members import Hub
from habitbot.miniapp import create_web_app

TOKEN = '123456:' + 'A' * 35
BOT_ID = '10000000-0000-0000-0000-000000000001'


def make_fixture(api_only=False):
    directory = tempfile.TemporaryDirectory()
    clock = datetime.now(timezone.utc).replace(hour=7, minute=0, second=0, microsecond=0)
    hub = Hub(directory.name, 123, clock=lambda: clock)
    hub.owner.register(123)
    hub.owner._award('fixture', 'goal', 280, 165, 'Учебный профиль для проверки интерфейса')
    hub.owner.add_habit('Почитать для себя', 'Десять страниц любимой книги. Это время только для тебя.', 15, '21:00', list(range(7)))
    hub.join(456, hub.invite(123))
    app = create_web_app(hub, TOKEN, 'mandarin_fixture_bot') if api_only else web.Application()
    if api_only:
        @web.middleware
        async def hostile_fixture(request, handler):
            if request.path == '/api/lesson/99':
                response = web.Response(text='<script>window.habitGatewayUnsafe=true</script>', content_type='text/html')
                response.headers.update({'Set-Cookie':'cf_session=hostile; Path=/',
                                         'X-Accel-Redirect':'/version', 'Refresh':'0;url=/health',
                                         'Content-Security-Policy':"script-src 'unsafe-inline'"})
                return response
            if request.path == '/api/lesson/98':
                return web.json_response({'cookie':request.headers.get('Cookie',''),
                                          'authorization':request.headers.get('Authorization',''),
                                          'internal':request.headers.get('X-Internal-Token','')})
            return await handler(request)
        app.middlewares.insert(0, hostile_fixture)
    assets = Path(__file__).resolve().parents[1] / 'web'
    async def index(request):
        return web.FileResponse(assets / 'index.html')
    if not api_only:
        app.router.add_get('/habits/' + BOT_ID + '/', index)
        app.router.add_static('/habits-app/', assets)
        app.add_subapp('/habits/' + BOT_ID, create_web_app(hub, TOKEN, 'mandarin_fixture_bot'))
    async def cleanup(_):
        hub.close()
        directory.cleanup()
    app.on_cleanup.append(cleanup)
    return app


if __name__ == '__main__':
    api_only = '--api-only' in sys.argv
    web.run_app(make_fixture(api_only), host='0.0.0.0' if api_only else '127.0.0.1',
                port=8081 if api_only else 8765, access_log=None)
