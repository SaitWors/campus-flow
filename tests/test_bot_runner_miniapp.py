import json
from types import SimpleNamespace

import pytest
from scripts import bot_runner as runner

BOT = dict(id='10000000-0000-0000-0000-000000000001', platform='telegram',
           app_origin='https://campus.example', image='ghcr.io/saitwors/habits@sha256:' + 'a'*64)


def engine(monkeypatch, label='habits-v1', network=True):
    calls=[]
    def docker(*args, **kwargs):
        calls.append(args)
        if args[:2] == ('image', 'inspect'):
            labels={'com.campus-flow.bot.storage':'data-v1'}
            if label is not None:
                labels['com.campus-flow.bot.miniapp']=label
            value=[dict(Config=dict(Labels=labels))]
        else:
            value=[dict(Driver='bridge', Internal=False, Options={}, Labels={'com.campus-flow.miniapp-network':'habits-v1'})]
        return SimpleNamespace(returncode=0 if network else 1, stdout=json.dumps(value))
    monkeypatch.setattr(runner, 'docker', docker)
    return calls


def test_miniapp_uses_fixed_network_and_https_url(monkeypatch):
    engine(monkeypatch)
    network, env = runner.miniapp_environment(BOT)
    assert network == 'campus-flow-habits'
    assert env == ['BOT_APP_URL=https://campus.example/habits/' + BOT['id'] + '/']


@pytest.mark.parametrize('origin', ['http://campus.example', 'https://x@y.example', 'https://campus.example/path',
                                    'https://campus.example?x=1', 'https://campus.example\nBAD=value'])
def test_invalid_origin_never_starts_web_contract(monkeypatch, origin):
    engine(monkeypatch)
    with pytest.raises(RuntimeError, match='origin'):
        runner.miniapp_environment({**BOT, 'app_origin':origin})


def test_legacy_image_needs_no_web_network(monkeypatch):
    calls=engine(monkeypatch, label=None)
    assert runner.miniapp_environment(BOT) == ('bridge', [])
    assert not any(c[0] == 'network' for c in calls)


def test_network_must_exist_and_contract_is_versioned(monkeypatch):
    engine(monkeypatch, network=False)
    with pytest.raises(RuntimeError):
        runner.miniapp_environment(BOT)
    engine(monkeypatch, label='unsafe')
    with pytest.raises(RuntimeError):
        runner.miniapp_environment(BOT)
