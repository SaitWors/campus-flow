"""Real account boundaries and streamed material bodies through HTTP."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import socket
import time

import pytest

from test_study_http import academic_cluster, subject


@pytest.fixture
def material_cluster(request, monkeypatch):
    monkeypatch.setenv('MATERIAL_MAX_FILE_BYTES', '150000')
    monkeypatch.setenv('MATERIAL_STORAGE_QUOTA_BYTES', '220000')
    return request.getfixturevalue('academic_cluster')


def upload(c, key, data, **params):
    return c.http.post(c.urls['schedule']+'/api/schedule/subjects/'+key+'/materials',
                       params={'filename':'Лекция.pdf', 'title':'Лекция о SQL',
                               'category':'lecture', **params}, content=data,
                       headers={'X-CSRF-Token':c.csrf, 'Content-Type':'application/octet-stream'})


def test_manager_uploads_and_members_read_private_files(material_cluster):
    c=material_cluster; admin,student,guest=c['admin'],c['students'][0],c['guest']
    key=subject(c)['key']; path='/api/schedule/subjects/'+key+'/materials'
    content=b'%PDF-1.4\n'+b'lecture '*10000
    assert len(content)>65536
    response=upload(admin,key,content,description='Конспект первой пары')
    assert response.status_code==201, response.text
    item=response.json(); assert item['size_bytes']==len(content)
    assert item['original_filename']=='Лекция.pdf' and item['category']=='lecture'
    assert item['uploader_name']==admin.user['name'] and item['revision']==1
    assert not {'path','storage_path','storage_key','sha256'} & item.keys()
    listed=student.request('GET',path)
    assert listed['total']==1 and listed['items']==[item]
    assert listed['storage']=={'used_bytes':len(content),'quota_bytes':220000,'max_file_bytes':150000}
    assert c['students'][2].request('GET',path)['items']==[item]
    file_path='/api/schedule/materials/'+item['id']+'/file'
    download=student.response('GET',file_path)
    assert download.status_code==200 and download.content==content
    assert download.headers['cache-control']=='no-store'
    assert download.headers['x-content-type-options']=='nosniff'
    assert download.headers['content-disposition'].startswith('attachment;')
    assert 'filename*=utf-8' in download.headers['content-disposition'].lower()
    assert 'sandbox' in download.headers['content-security-policy']
    guest.request('GET',path,expected=401)
    assert guest.response('GET',file_path).status_code==401
    assert upload(student,key,content).status_code==403
    assert upload(guest,key,content).status_code==401
    old_csrf=admin.csrf; admin.csrf=''
    assert upload(admin,key,content).status_code==403
    admin.csrf=old_csrf
    assert student.response('GET',file_path+'?inline=true').status_code==415
    assert student.request('GET',path+'?q=sql&category=lecture')['items']==[item]
    assert student.request('GET',path+'?q=КОНСПЕКТ')['items']==[item]
    assert student.request('GET',path+'?q=%25')['items']==[]
    assert student.request('GET',path+'?category=notes')['items']==[]
    for role in ('head','deputy'):
        admin.request('PATCH','/api/auth/users/'+student.user['id'],
                      {'role':role,'status':'active','subgroup':1})
        r=upload(student,key,b'notes',filename=role+'.txt',title=role,category='notes')
        assert r.status_code==201, r.text
    assert admin.request('GET',path+'?limit=1&offset=1')['total']==3
    assert len(admin.request('GET',path+'?limit=1&offset=1')['items'])==1
    admin.request('PATCH','/api/auth/users/'+student.user['id'],
                  {'role':'student','status':'blocked','subgroup':1})
    assert student.response('GET',file_path).status_code in (401,403)


def test_limits_types_chunked_body_and_quota(material_cluster,tmp_path):
    c=material_cluster; admin=c['admin']; key=subject(c)['key']
    for data,filename,expected in ((b'', 'empty.pdf',400),(b'html','unsafe.html',415),
                                   (b'<svg/>','unsafe.svg',415),(b'html','fake.png',415),
                                   (b'a','../file.pdf',422),(b'a','C:\\file.pdf',422),
                                   (b'a','bad\nname.pdf',422),(b'a'*150001,'large.txt',413)):
        r=upload(admin,key,data,filename=filename)
        assert r.status_code==expected,(filename,r.status_code,r.text)
    # Chunked transfers must have the same cap as Content-Length bodies.
    r=upload(admin,key,(b'a'*70000 for _ in range(3)),filename='chunked.txt')
    assert r.status_code==413,r.text
    assert not list((tmp_path/'materials').glob('*.part'))
    for n in range(2):
        assert upload(admin,key,b'a'*100000,filename=str(n)+'.txt').status_code==201
    r=upload(admin,key,b'a'*30000,filename='over-quota.txt')
    assert r.status_code==409 and r.json()['detail']=='material_storage_full'
    assert not list((tmp_path/'materials').glob('*.part'))
    assert len(list((tmp_path/'materials').glob('*.blob')))==2
    # The existing 64KiB JSON boundary remains strict.
    r=admin.http.post(admin.urls['schedule']+'/api/schedule/title-preview',
                     content=b'a'*70000,headers={'X-CSRF-Token':admin.csrf})
    assert r.status_code==413 and r.json()['detail']=='body_too_large'


def test_revision_edits_deletion_and_image_preview(material_cluster):
    c=material_cluster; admin,student=c['admin'],c['students'][0]; key=subject(c)['key']
    png=bytes.fromhex('89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489')
    r=upload(admin,key,png,filename='Заметка.png',title='Схема',category='notes')
    assert r.status_code==201,r.text
    item=r.json(); path='/api/schedule/materials/'+item['id']
    preview=student.response('GET',path+'/file?inline=true')
    assert preview.status_code==200 and preview.content==png
    assert preview.headers['content-type']=='image/png'
    assert preview.headers['content-disposition'].startswith('inline;')
    body={'revision':1,'title':'Новая схема','description':'Пояснения','category':'other'}
    student.request('PATCH',path,body,expected=403)
    changed=admin.request('PATCH',path,body)
    assert changed['revision']==2 and changed['title']=='Новая схема'
    admin.request('PATCH',path,body,expected=409)
    student.request('DELETE',path+'?revision=2',expected=403)
    admin.request('DELETE',path+'?revision=1',expected=409)
    assert admin.request('DELETE',path+'?revision=2')=={'ok':True}
    assert student.response('GET',path+'/file').status_code==404
    assert admin.request('GET','/api/schedule/subjects/'+key+'/materials')['storage']['used_bytes']==0


def test_simultaneous_uploads_do_not_overrun_quota(material_cluster):
    c=material_cluster; admin=c['admin']; key=subject(c)['key']
    assert upload(admin,key,b'a'*100000,filename='first.txt').status_code==201
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda n:upload(admin,key,b'b'*80000,filename=str(n)+'.txt'),range(2)))
    assert sorted(r.status_code for r in results)==[201,409]
    listed=admin.request('GET','/api/schedule/subjects/'+key+'/materials')
    assert listed['total']==2 and listed['storage']['used_bytes']==180000


def test_maximum_unicode_metadata_accepts_a_fragmented_request_line(material_cluster):
    c=material_cluster;admin=c['admin'];key=subject(c)['key']
    prepared=admin.http.build_request('POST',admin.urls['schedule']+'/api/schedule/subjects/'+key+'/materials',
        params={'filename':'🧠'*236+'.txt','title':'🧠'*160,'description':'🧠'*4000},
        headers={'X-CSRF-Token':admin.csrf,'Connection':'close'},content=b'notes')
    wire=b'POST '+prepared.url.raw_path+b' HTTP/1.1\r\n'+b''.join(
        name+b': '+value+b'\r\n' for name,value in prepared.headers.raw)+b'\r\nnotes'
    assert len(prepared.url.raw_path)>50000
    # Make the first transport read end inside the request line, so this also
    # checks the server parser's incomplete-header limit on slower networks.
    with socket.create_connection((prepared.url.host,prepared.url.port),timeout=5) as connection:
        connection.sendall(wire[:20000]);time.sleep(.05)
        try:connection.sendall(wire[20000:])
        except BrokenPipeError:pass
        received=b''
        while True:
            try:chunk=connection.recv(65536)
            except ConnectionResetError:break
            if not chunk:break
            received+=chunk
    assert received.startswith(b'HTTP/1.1 201'),received[:500]
    listed=admin.request('GET','/api/schedule/subjects/'+key+'/materials')
    assert listed['total']==1 and listed['items'][0]['description']=='🧠'*4000
