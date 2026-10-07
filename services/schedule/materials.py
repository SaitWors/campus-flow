"""Private, streamed subject file storage; no public filesystem mount."""
import hashlib
import os
import re
import shutil
import stat
import threading
import unicodedata
from pathlib import Path
from typing import Literal
from uuid import UUID

import anyio
from fastapi import Query, Request
from fastapi.responses import FileResponse
from pydantic import Field, field_validator
from sqlalchemy import func, select
from starlette.requests import ClientDisconnect

from services.common.core import Input, env_int, fail, identity, manager, uid
from services.schedule.academic import utc_string, validate_text
from services.schedule.models import Audit, Material, MaterialStorage, Subject

Category=Literal['lecture','notes','other']
UPLOAD_PATH=re.compile(r'^/api/schedule/subjects/[a-f0-9]{64}/materials$')
IMAGE_TYPES={'png':'image/png','jpg':'image/jpeg','jpeg':'image/jpeg',
             'webp':'image/webp','gif':'image/gif'}
DOCUMENT_TYPES={'pdf':'application/pdf','doc':'application/msword',
    'docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'ppt':'application/vnd.ms-powerpoint','pptx':'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    'xls':'application/vnd.ms-excel','xlsx':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'odt':'application/vnd.oasis.opendocument.text','ods':'application/vnd.oasis.opendocument.spreadsheet',
    'odp':'application/vnd.oasis.opendocument.presentation','txt':'text/plain','md':'text/plain',
    'csv':'text/csv','zip':'application/zip','heic':'image/heic','mp3':'audio/mpeg',
    'm4a':'audio/mp4','ogg':'audio/ogg','wav':'audio/wav','mp4':'video/mp4','webm':'video/webm'}


def file_limit():
    return env_int('MATERIAL_MAX_FILE_BYTES',50*1024**2,1,1024**3)


def storage_quota():
    return env_int('MATERIAL_STORAGE_QUOTA_BYTES',5*1024**3,1,1024**4)


def upload_body_limit(request):
    return file_limit() if request.method=='POST' and UPLOAD_PATH.fullmatch(request.url.path) else None


def storage_dir():
    root=Path(os.getenv('MATERIALS_DIR',str(Path(__file__).resolve().parents[2]/'.dev-data'/'materials')))
    try:
        if root.is_symlink(): fail('storage_unavailable',503)
        root.mkdir(parents=True,exist_ok=True,mode=0o700)
        if not root.is_dir(): fail('storage_unavailable',503)
    except OSError:
        fail('storage_unavailable',503)
    return root


def acquire_file_lock(root,identifier):
    """A separate lock inode also works with Windows byte-range locking."""
    path=root/(identifier+'.lock')
    fd=os.open(path,os.O_CREAT|os.O_RDWR|getattr(os,'O_NOFOLLOW',0),0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode): raise OSError('Invalid lock inode')
        if os.name=='nt':
            import msvcrt
            if not os.fstat(fd).st_size: os.write(fd,b'1')
            os.lseek(fd,0,os.SEEK_SET);msvcrt.locking(fd,msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        return fd
    except BaseException:
        os.close(fd);raise


def release_file_lock(root,identifier,fd):
    os.close(fd)
    try:(root/(identifier+'.lock')).unlink(missing_ok=True)
    except OSError:pass


def unlink_private(path):
    try:path.unlink(missing_ok=True)
    except OSError:pass


def reconcile_storage(root,DB):
    # Acquire the publisher's lock FIRST and query a fresh DB snapshot AFTER.
    # A startup racing a commit must never act on a stale list of material ids.
    for path in root.iterdir():
        if path.suffix not in ('.part','.blob') or path.is_symlink():continue
        try:
            if str(UUID(path.stem))!=path.stem:continue
            fd=acquire_file_lock(root,path.stem)
        except (ValueError,OSError):continue  # An active publisher owns its files.
        try:
            with DB() as db:
                referenced=db.get(Material,path.stem) is not None
            if path.suffix=='.part' or not referenced:unlink_private(path)
        finally:release_file_lock(root,path.stem,fd)


def initialize_materials(app,DB=None):
    file_limit();storage_quota();storage_dir()
    if DB is not None:reconcile_storage(storage_dir(),DB)
    app.state.material_upload_limiter=anyio.CapacityLimiter(2)


def migrate_materials(conn):
    Material.__table__.create(conn,checkfirst=True)
    MaterialStorage.__table__.create(conn,checkfirst=True)
    if conn.execute(select(MaterialStorage.id).where(MaterialStorage.id==1)).scalar() is None:
        conn.execute(MaterialStorage.__table__.insert().values(id=1))


def checked_filename(value):
    value=unicodedata.normalize('NFC',value.strip())
    if not value or len(value)>240 or value in ('.','..') or any(c in '/\\' or ord(c)<32 or ord(c)==127 for c in value):
        fail('material_filename_invalid',422)
    extension=value.rsplit('.',1)[-1].lower() if '.' in value else ''
    mime={**IMAGE_TYPES,**DOCUMENT_TYPES}.get(extension)
    if not mime: fail('material_type_not_allowed',415)
    return value,extension,mime


def checked_text(value,multiline=False):
    try: return validate_text(value,multiline)
    except ValueError: fail('validation',422)


def validate_image(extension,header):
    matches={'png':header.startswith(b'\x89PNG\r\n\x1a\n'),
             'jpg':header.startswith(b'\xff\xd8\xff'),'jpeg':header.startswith(b'\xff\xd8\xff'),
             'gif':header.startswith((b'GIF87a',b'GIF89a')),
             'webp':header.startswith(b'RIFF') and header[8:12]==b'WEBP'}
    if extension in IMAGE_TYPES and not matches[extension]: fail('material_content_mismatch',415)


def file_path(root,identifier):
    try:
        if str(UUID(identifier))!=identifier: fail('material_unavailable',404)
    except (ValueError,AttributeError): fail('material_unavailable',404)
    return root/(identifier+'.blob')


def material_row(item):
    return {name:getattr(item,name) for name in ('id','subject_key','title','description','category',
        'original_filename','mime_type','size_bytes','uploader_name','revision')} | {'created_at':utc_string(item.created_at)}


def searchable(title,description,filename):
    return '\n'.join((title,description,filename)).casefold()


class MaterialEdit(Input):
    revision:int=Field(ge=1)
    title:str=Field(min_length=1,max_length=160)
    description:str=Field(default='',max_length=4000)
    category:Category='other'

    @field_validator('title')
    @classmethod
    def title_valid(cls,value): return validate_text(value)

    @field_validator('description')
    @classmethod
    def description_valid(cls,value): return validate_text(value,True)


def used_bytes(db):
    return db.scalar(select(func.coalesce(func.sum(Material.size_bytes),0)))


def record(db,user,action,item):
    db.add(Audit(actor=user['name'],action=action,target=item.id,
                 data={'subject_key':item.subject_key,'category':item.category}))


def register_material_routes(app,db_provider):
    @app.get('/api/schedule/subjects/{key}/materials')
    def list_materials(key:str,request:Request,category:Category|None=None,
                       q:str=Query(default='',max_length=160),limit:int=Query(default=50,ge=1,le=100),
                       offset:int=Query(default=0,ge=0)):
        identity(request)
        with db_provider()() as db:
            if not db.get(Subject,key): fail('not_found',404)
            predicates=[Material.subject_key==key]
            if category: predicates.append(Material.category==category)
            if q.strip(): predicates.append(Material.search_text.contains(q.strip().casefold(),autoescape=True))
            total=db.scalar(select(func.count()).select_from(Material).where(*predicates))
            rows=db.scalars(select(Material).where(*predicates).order_by(Material.created_at.desc(),Material.id.desc()).offset(offset).limit(limit))
            return {'items':[material_row(item) for item in rows],'total':total,'limit':limit,'offset':offset,
                    'storage':{'used_bytes':used_bytes(db),'quota_bytes':storage_quota(),'max_file_bytes':file_limit()}}

    @app.post('/api/schedule/subjects/{key}/materials',status_code=201)
    async def upload_material(key:str,request:Request,filename:str=Query(min_length=1,max_length=240),
                              title:str=Query(min_length=1,max_length=160),
                              description:str=Query(default='',max_length=4000),category:Category='other'):
        user=await anyio.to_thread.run_sync(identity,request);manager(user)
        filename,extension,mime=checked_filename(filename)
        title=checked_text(title.strip());description=checked_text(description.strip(),True)
        if not title: fail('validation',422)
        with db_provider()() as db:
            if not db.get(Subject,key): fail('not_found',404)
        root=storage_dir();identifier=uid();temporary=root/(identifier+'.part');published=file_path(root,identifier)
        size=0;header=b'';digest=hashlib.sha256();stream=None;lock_fd=None
        handoff=threading.Lock();ownership='request'
        async with app.state.material_upload_limiter:
            try:
                if shutil.disk_usage(root).free<file_limit()+8*1024**2: fail('storage_unavailable',503)
                lock_fd=acquire_file_lock(root,identifier)
                fd=os.open(temporary,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
                stream=os.fdopen(fd,'wb')
                async for chunk in request.stream():
                    size+=len(chunk)
                    if size>file_limit(): fail('material_too_large',413)
                    if len(header)<32: header+=chunk[:32-len(header)]
                    digest.update(chunk)
                    await anyio.to_thread.run_sync(stream.write,chunk)
                await anyio.to_thread.run_sync(stream.flush)
                await anyio.to_thread.run_sync(os.fsync,stream.fileno())
                stream.close();stream=None
                if not size: fail('material_empty',400)
                validate_image(extension,header)
                # Roles/session can be revoked while a large file is in transit.
                user=await anyio.to_thread.run_sync(identity,request);manager(user)
                def publish():
                    nonlocal ownership
                    with handoff:
                        if ownership=='cancelled':return
                        ownership='publisher'
                    try:
                        with db_provider().begin() as db:
                            db.scalar(select(MaterialStorage).where(MaterialStorage.id==1).with_for_update())
                            if used_bytes(db)+size>storage_quota(): fail('material_storage_full',409)
                            if not db.get(Subject,key): fail('not_found',404)
                            item=Material(id=identifier,subject_key=key,title=title,description=description,
                                category=category,original_filename=filename,mime_type=mime,size_bytes=size,
                                sha256=digest.hexdigest(),search_text=searchable(title,description,filename),
                                uploader_name=user['name'])
                            db.add(item);db.flush();record(db,user,'material_uploaded',item)
                            temporary.rename(published)
                            result=material_row(item)
                        return result
                    finally:
                        # Commit may succeed before an exception/cancellation is
                        # observed. Only the publisher can establish rollback.
                        retained=True
                        try:
                            with db_provider()() as db:retained=db.get(Material,identifier) is not None
                        except Exception:pass  # Keep bytes when outcome is unknown.
                        if not retained:
                            unlink_private(temporary);unlink_private(published)
                        release_file_lock(root,identifier,lock_fd)
                result=await anyio.to_thread.run_sync(publish)
                return result
            except (OSError,ClientDisconnect):
                fail('storage_unavailable',503)
            finally:
                if stream is not None: stream.close()
                with handoff:
                    if ownership=='request':
                        ownership='cancelled'
                        unlink_private(temporary);unlink_private(published)
                        if lock_fd is not None:release_file_lock(root,identifier,lock_fd)

    @app.patch('/api/schedule/materials/{identifier}')
    def edit_material(identifier:str,data:MaterialEdit,request:Request):
        user=identity(request);manager(user)
        with db_provider().begin() as db:
            item=db.scalar(select(Material).where(Material.id==identifier).with_for_update())
            if not item: fail('material_unavailable',404)
            if item.revision!=data.revision: fail('revision_conflict',409)
            item.title=data.title;item.description=data.description;item.category=data.category
            item.search_text=searchable(item.title,item.description,item.original_filename);item.revision+=1
            record(db,user,'material_edited',item)
            return material_row(item)

    @app.delete('/api/schedule/materials/{identifier}')
    def delete_material(identifier:str,request:Request,revision:int=Query(ge=1)):
        user=identity(request);manager(user);path=file_path(storage_dir(),identifier)
        with db_provider().begin() as db:
            db.scalar(select(MaterialStorage).where(MaterialStorage.id==1).with_for_update())
            item=db.scalar(select(Material).where(Material.id==identifier).with_for_update())
            if not item: fail('material_unavailable',404)
            if item.revision!=revision: fail('revision_conflict',409)
            record(db,user,'material_deleted',item);db.delete(item)
        # A failed unlink leaves a private orphan, never a downloadable record.
        try: path.unlink(missing_ok=True)
        except OSError: pass
        return {'ok':True}

    @app.get('/api/schedule/materials/{identifier}/file')
    def download_material(identifier:str,request:Request,inline:bool=False):
        identity(request);path=file_path(storage_dir(),identifier)
        with db_provider()() as db:
            item=db.get(Material,identifier)
            if not item or path.is_symlink() or not path.is_file() or path.stat().st_size!=item.size_bytes:
                fail('material_unavailable',404)
            if inline and item.mime_type not in IMAGE_TYPES.values(): fail('material_type_not_allowed',415)
            return FileResponse(path,media_type=item.mime_type,filename=item.original_filename,
                content_disposition_type='inline' if inline else 'attachment',
                headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',
                         'Content-Security-Policy':"sandbox; default-src 'none'; frame-ancestors 'none'"})
