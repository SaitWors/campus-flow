"""Runner storage opt-in never exposes arbitrary host mounts or deletes data."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import bot_runner as runner

BOT_ID='10000000-0000-0000-0000-000000000001'
IMAGE='ghcr.io/saitwors/campus-flow-habit-bot@sha256:'+'a'*64
TOKEN='123456789:'+'x'*35


def fake_engine(monkeypatch,tmp_path,label='data-v1',existing=None):
    calls=[]
    env=[]
    volume=[existing]
    def docker(*args,**kwargs):
        calls.append(args)
        if args[:2]==('image','inspect'):
            labels={} if label is None else {'com.campus-flow.bot.storage':label}
            return SimpleNamespace(returncode=0,stdout=json.dumps([{'Config':{'Labels':labels}}]))
        if args[:2]==('volume','inspect'):
            return SimpleNamespace(returncode=0 if volume[0] else 1,stdout=json.dumps([volume[0]]) if volume[0] else '')
        if args[:2]==('volume','create'):
            volume[0]={'Driver':'local','Options':None,'Labels':{
                'campus-flow.bot-managed':'true','campus-flow.bot-id':BOT_ID}}
            return SimpleNamespace(returncode=0,stdout=args[-1])
        if args[0]=='run':
            path=Path(args[args.index('--env-file')+1])
            env.append(path.read_text())
        return SimpleNamespace(returncode=0,stdout='',stderr='')
    monkeypatch.setattr(runner,'docker',docker)
    mkstemp=runner.tempfile.mkstemp
    monkeypatch.setattr(runner.tempfile,'mkstemp',lambda **kwargs:mkstemp(prefix=kwargs['prefix'],dir=tmp_path))
    bot=dict(id=BOT_ID,image=IMAGE,token=TOKEN,platform='telegram',revision=1)
    return bot,calls,env


def test_opt_in_image_gets_only_its_named_volume_and_data_env(monkeypatch,tmp_path):
    bot,calls,env=fake_engine(monkeypatch,tmp_path)
    runner.create(bot,'cfbot-'+BOT_ID,pre_pulled=True)
    run=next(c for c in calls if c[0]=='run')
    assert run[run.index('--mount')+1]=='type=volume,source=cfbot-data-'+BOT_ID+',target=/data'
    assert 'BOT_DATA_DIR=/data\n' in env[0]
    assert '--read-only' in run and run[run.index('--user')+1]=='10001:10001'
    assert TOKEN not in str(calls)


def test_existing_volume_is_reused_after_container_revision_changes(monkeypatch,tmp_path):
    bot,calls,env=fake_engine(monkeypatch,tmp_path)
    runner.create(bot,'cfbot-'+BOT_ID,pre_pulled=True)
    bot['revision']=2
    runner.create(bot,'cfbot-'+BOT_ID,pre_pulled=True)
    mounts=[c[c.index('--mount')+1] for c in calls if c[0]=='run']
    assert len(mounts)==2 and mounts[0]==mounts[1]
    assert len([c for c in calls if c[:2]==('volume','create')])==1
    assert not any(c[:2]==('volume','rm') for c in calls)


def test_unlabelled_image_keeps_existing_tmp_only_contract(monkeypatch,tmp_path):
    bot,calls,env=fake_engine(monkeypatch,tmp_path,label=None)
    runner.create(bot,'cfbot-'+BOT_ID,pre_pulled=True)
    assert not any(c[0]=='volume' for c in calls)
    run=next(c for c in calls if c[0]=='run')
    assert '--mount' not in run and 'BOT_DATA_DIR=' not in env[0]


@pytest.mark.parametrize('existing',[
    {'Driver':'local','Options':None,'Labels':{}},
    {'Driver':'local','Options':None,'Labels':{'campus-flow.bot-managed':'true','campus-flow.bot-id':'another-bot'}},
    {'Driver':'local','Options':{'type':'none','device':'/','o':'bind'},'Labels':{'campus-flow.bot-managed':'true','campus-flow.bot-id':BOT_ID}},
])
def test_volume_collisions_and_host_bind_options_are_rejected(monkeypatch,tmp_path,existing):
    bot,calls,env=fake_engine(monkeypatch,tmp_path,existing=existing)
    with pytest.raises(RuntimeError,match='volume'):
        runner.create(bot,'cfbot-'+BOT_ID,pre_pulled=True)
    assert not env and not any(c[0]=='run' for c in calls)


def test_unknown_storage_contract_does_not_silently_discard_progress(monkeypatch,tmp_path):
    bot,calls,env=fake_engine(monkeypatch,tmp_path,label='/etc')
    with pytest.raises(RuntimeError,match='storage'):
        runner.create(bot,'cfbot-'+BOT_ID,pre_pulled=True)
    assert not env


def test_orphan_cleanup_never_removes_persistent_volumes(monkeypatch):
    calls=[]
    monkeypatch.setattr(runner,'docker',lambda *args,**kw:(calls.append(args) or SimpleNamespace(returncode=0,stdout='cfbot-'+BOT_ID+'\n')))
    monkeypatch.setattr(runner,'details',lambda name:{'labels':{'campus-flow.bot-managed':'true','campus-flow.bot-id':BOT_ID}})
    runner.purge_orphans(set())
    assert ('rm','-f','cfbot-'+BOT_ID) in calls
    assert not any(c[0]=='volume' for c in calls)
