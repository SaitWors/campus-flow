import hashlib
import io
import json
import zipfile
from pathlib import Path
import pytest
import requests
from scripts import install_ru_en_model as installer


def model():
    data=io.BytesIO()
    with zipfile.ZipFile(data,'w') as z:
        z.writestr('ru_en/metadata.json',json.dumps({'from_code':'ru','to_code':'en','package_version':'1.9'}))
        z.writestr('ru_en/model.bin',b'test model data')
    return data.getvalue()


def test_resume_after_incomplete_read_and_verified_cache(tmp_path,monkeypatch):
    data=model();monkeypatch.setattr(installer,'MODEL_SHA256',hashlib.sha256(data).hexdigest())
    calls=[]
    class Response:
        status_code=200
        headers={'Content-Length':str(len(data))}
        def __enter__(self):return self
        def __exit__(self,*_):pass
        def iter_content(self,_):
            if len(calls)==1:
                yield data[:40]
                raise requests.ConnectionError('cut connection')
            yield data[40:]
    class Session:
        def get(self,url,**kwargs):
            calls.append((url,kwargs))
            assert url==installer.MODEL_URL and kwargs['allow_redirects'] is False
            r=Response()
            if len(calls)>1:
                assert kwargs['headers']['Range']=='bytes=40-'
                r.status_code=206;r.headers={'Content-Range':f'bytes 40-{len(data)-1}/{len(data)}','Content-Length':str(len(data)-40)}
            return r
    session=Session();path=tmp_path/'model.argosmodel'
    installer.download(path,session=session,wait=lambda _:None)
    assert path.read_bytes()==data and len(calls)==2 and session.trust_env is False
    installer.download(path,session=session,wait=lambda _:None)
    assert len(calls)==2


def test_checksum_mismatch_cannot_install_and_retries_are_bounded(tmp_path):
    data=model()
    class Response:
        status_code=200;headers={'Content-Length':str(len(data))}
        def __enter__(self):return self
        def __exit__(self,*_):pass
        def iter_content(self,_):yield data
    class Session:
        calls=0
        def get(self,*a,**kw):self.calls+=1;return Response()
    session=Session();path=tmp_path/'model.argosmodel'
    with pytest.raises(RuntimeError):installer.download(path,session=session,wait=lambda _:None)
    assert session.calls==5 and not path.exists() and not path.with_suffix('.part').exists()


def test_official_host_failure_falls_back_to_verified_mirror(tmp_path,monkeypatch):
    data=model();monkeypatch.setattr(installer,'MODEL_SHA256',hashlib.sha256(data).hexdigest())
    calls=[]
    class Response:
        status_code=200;headers={'Content-Length':str(len(data))}
        def __enter__(self):return self
        def __exit__(self,*_):pass
        def iter_content(self,_):yield data
    class Session:
        def get(self,url,**kwargs):
            calls.append(url)
            assert kwargs['allow_redirects'] is False
            if url==installer.MODEL_URLS[0]:raise requests.ConnectionError('fixture host unavailable')
            return Response()
    path=installer.download(tmp_path/'model.argosmodel',session=Session(),wait=lambda _:None)
    assert calls==[installer.MODEL_URLS[0],installer.MODEL_URLS[0],installer.MODEL_URLS[1]]
    assert path.read_bytes()==data
