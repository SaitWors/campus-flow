"""Private subject files through real services/proxy, disposable accounts only."""
import base64
import hashlib
from scripts.smoke import Client, PASSWORD

PDF=b'%PDF-1.4\n'+b'Campus materials restore fixture\n'*33000
PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX0kAAAAASUVORK5CYII=')


def run(urls):
    admin=Client(urls).login('admin@example.test')
    student=Client(urls).login('student0@example.test',PASSWORD+'new')
    guest=Client(urls)
    try:
        subject=student.request('GET','/api/schedule/subjects')[0]
        path='/api/schedule/subjects/'+subject['key']+'/materials'
        def upload(client,data,filename,title,**metadata):
            return client.http.post(urls['schedule']+path,params={
                'filename':filename,'title':title,'category':'lecture',**metadata},content=data,
                headers={'X-CSRF-Token':client.csrf,'Content-Type':'application/octet-stream'})
        assert guest.http.get(urls['schedule']+path).status_code==401
        assert upload(student,PDF,'lecture.pdf','forbidden').status_code==403
        response=upload(admin,PDF,'Лекция для восстановления.pdf','Materials restore fixture')
        assert response.status_code==201,response.text
        item=response.json();assert item['size_bytes']==len(PDF)>65536
        file='/api/schedule/materials/'+item['id']+'/file'
        downloaded=student.http.get(urls['schedule']+file)
        assert downloaded.status_code==200 and hashlib.sha256(downloaded.content).digest()==hashlib.sha256(PDF).digest()
        assert downloaded.headers['cache-control']=='no-store'
        assert downloaded.headers['content-disposition'].startswith('attachment;')
        assert guest.http.get(urls['schedule']+file).status_code==401
        assert student.request('GET',path+'?q=RESTORE')['items'][0]['id']==item['id']
        r=upload(admin,PNG,'Конспект.png','Materials image fixture')
        assert r.status_code==201,r.text
        image=r.json();image_path='/api/schedule/materials/'+image['id']
        r=student.http.get(urls['schedule']+image_path+'/file?inline=true')
        assert r.status_code==200 and r.content==PNG and r.headers['content-type']=='image/png'
        edit={'revision':1,'title':'Materials updated image','description':'Edited notes','category':'notes'}
        student.request('PATCH',image_path,edit,expected=403)
        assert admin.request('PATCH',image_path,edit)['revision']==2
        admin.request('PATCH',image_path,edit,expected=409)
        student.request('DELETE',image_path+'?revision=2',expected=403)
        admin.request('DELETE',image_path+'?revision=2')
        assert student.http.get(urls['schedule']+image_path+'/file').status_code==404
        assert upload(admin,b'<svg/>','unsafe.svg','forbidden').status_code==415
        # Maximum valid Unicode metadata produces a >50KiB request line. The
        # real gateway must accept it as well as a short ASCII query string.
        long=upload(admin,b'Unicode metadata boundary','🧠'*236+'.txt','🧠'*160,
                    description='🧠'*4000)
        assert long.status_code==201,long.text
        assert long.json()['description']=='🧠'*4000
        admin.request('DELETE','/api/schedule/materials/'+long.json()['id']+'?revision=1')
        # Stream the exact default boundary through the real proxy and small-VM
        # API container; no 50MiB buffer is needed by this test sender either.
        limit=admin.request('GET',path)['storage']['max_file_bytes']
        if limit==50*1024**2:
            block=b'Lecture recording notes\n'*(65536//24+1)
            def chunks():
                remaining=limit
                while remaining:
                    value=block[:min(len(block),remaining)];remaining-=len(value);yield value
            bulk=upload(admin,chunks(),'boundary.txt','Materials size boundary')
            assert bulk.status_code==201,bulk.text
            assert bulk.json()['size_bytes']==limit
            admin.request('DELETE','/api/schedule/materials/'+bulk.json()['id']+'?revision=1')
        print('PASS: streamed subject files through gateway, 50MiB default boundary, maximum Unicode metadata, manager writes, member downloads, guest boundary, search, image preview, revisions and deletion',flush=True)
    finally:
        for client in (admin,student,guest):client.close()


if __name__=='__main__':
    run({service:'http://localhost:8080' for service in ('auth','schedule','notifications')})
