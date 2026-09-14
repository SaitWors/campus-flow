import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import Request, Query, HTTPException
from pydantic import Field
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from services.common.core import database, migrate, setup_app, Input, now, uid, digest, fail, identity, manager, remote, service_secret, internal
from services.queue.models import Base, Queue, Entry, Command, Audit, Cursor, Event

engine,DB=database('queue')
ACTIVE=('waiting','called')
logger=logging.getLogger('campus.queue')

def schedule(path):return remote(os.getenv('SCHEDULE_URL','http://schedule:8000'),path)

def lesson(oid):return schedule('/internal/occurrences/'+oid)

def student(user_id):return remote(os.getenv('AUTH_URL','http://auth:8000'),'/internal/users/'+user_id)

def current_utc():return datetime.now(timezone.utc)

def sync_queue(db,q,item):
    """Keep places on edits; retire all active places on cancellation/expiry."""
    reason=''
    if item['status']=='cancelled':reason='lesson_cancelled'
    elif not item['queue_enabled']:reason='queue_unavailable'
    elif current_utc()>=datetime.fromisoformat(item['ends_at']):reason='lesson_ended'
    changed=q.lesson_revision!=item['revision']
    if reason:
        active=db.scalars(select(Entry).where(Entry.queue_id==q.id,Entry.status.in_(ACTIVE))).all()
        if q.state=='closed' and q.blocked_reason==reason and not active and not changed:return
        q.state='closed';q.blocked_reason=reason
        for e in active:
            e.status='expired' if reason=='lesson_ended' else 'cancelled';e.ended_at=now()
    elif changed:
        q.state='paused';q.blocked_reason='lesson_changed'
    else:return
    q.lesson_revision=item['revision'];q.revision+=1;q.updated_at=now()
    audit(db,q,{'name':'system'},'lesson_reconciled',item['id'],{'revision':item['revision'],'reason':reason})


def reconcile():
    with DB() as db:after=db.get(Cursor,1).seq
    events=schedule(f'/internal/events?after={after}')
    for e in events:
        # Obtain schedule before queue lock. Cross-service cancellation is eventual,
        # commands ALSO verify current schedule synchronously (fail closed).
        oid=e['data']['occurrence_id']
        with DB() as db:exists=db.scalar(select(Queue.id).where(Queue.occurrence_id==oid))
        item=lesson(oid) if exists else None
        with DB.begin() as db:
            cur=db.scalar(select(Cursor).where(Cursor.id==1).with_for_update())
            if cur.seq>=e['seq']:continue
            q=db.scalar(select(Queue).where(Queue.occurrence_id==oid).with_for_update())
            if q and item:sync_queue(db,q,item)
            cur.seq=e['seq']
    # Time passes without a schedule event. Release called students at class end.
    with DB() as db:pending=[(q.id,q.occurrence_id) for q in db.scalars(select(Queue).where(Queue.state!='closed'))]
    for qid,oid in pending:
        item=lesson(oid)
        with DB.begin() as db:sync_queue(db,locked(db,qid),item)

async def event_worker():
    while True:
        try:await asyncio.to_thread(reconcile)
        except asyncio.CancelledError:raise
        except Exception:logger.warning('Schedule reconciliation postponed; will retry.')
        await asyncio.sleep(3)

@asynccontextmanager
async def lifespan(app):
    service_secret();migrate(engine,Base,(lambda conn: Event.__table__.create(conn,checkfirst=True),))
    with DB.begin() as db:
        if not db.get(Cursor,1):db.add(Cursor(id=1))
    task=asyncio.create_task(event_worker()) if os.getenv('EVENT_WORKER','true')=='true' else None
    yield
    if task:
        task.cancel()
        try:await task
        except asyncio.CancelledError:pass

app=setup_app('Campus Flow · Queues',engine,lifespan)

def audit(db,q,u,action,target,data=None):
    db.add(Audit(queue_id=q.id,actor=u['name'],action=action,target=target,data=data or {}))

def locked(db,qid):
    q=db.scalar(select(Queue).where(Queue.id==qid).with_for_update())
    if not q:fail('not_found',404)
    return q

def item_json(e):
    return {'id':e.id,'user_id':e.user_id,'name':e.name,'ticket':e.ticket,'task':e.task,'status':e.status,'joined_at':e.joined_at.isoformat()+'Z','called_at':e.called_at.isoformat()+'Z' if e.called_at else None}

def can_join(q,item):
    if item['status']=='cancelled':return 'lesson_cancelled'
    if item['status']!='confirmed':return 'lesson_unconfirmed'
    if not item['queue_enabled']:return 'queue_unavailable'
    if current_utc()>=datetime.fromisoformat(item['ends_at']):return 'lesson_ended'
    if q.lesson_revision!=item['revision'] or q.blocked_reason=='lesson_changed':return 'lesson_changed'
    if q.state!='open':return 'queue_'+q.state
    if current_utc()<datetime.fromisoformat(item['starts_at'])-timedelta(hours=q.opens_before_hours):return 'queue_too_early'
    if current_utc()>=datetime.fromisoformat(item['ends_at']):return 'lesson_ended'
    return ''

def queue_json(db,q,item,u):
    entries=db.scalars(select(Entry).where(Entry.queue_id==q.id).order_by(Entry.ticket)).all()
    active=[e for e in entries if e.status in ACTIVE]
    active.sort(key=lambda e:(0 if e.status=='called' else 1,e.ticket))
    my=next((e for e in active if e.user_id==u['id']),None)
    return {'id':q.id,'occurrence_id':q.occurrence_id,'state':q.state,'capacity':q.capacity,'minutes_per_student':q.minutes_per_student,'opens_before_hours':q.opens_before_hours,'revision':q.revision,'lesson_revision':q.lesson_revision,'blocked_reason':can_join(q,item),'opens_at':(datetime.fromisoformat(item['starts_at'])-timedelta(hours=q.opens_before_hours)).isoformat(),'entries':[item_json(e) for e in active],'history':[item_json(e) for e in entries if e.status not in ACTIVE][-100:],'my_position':active.index(my)+1 if my else None,'my_entry':item_json(my) if my else None,'lesson':item,'server_time':current_utc().isoformat(),'closed_reason':q.blocked_reason}

class Create(Input):
    occurrence_id:str=Field(min_length=36,max_length=36)
    capacity:int=Field(default=30,ge=1,le=100)
    minutes_per_student:int=Field(default=7,ge=1,le=60)
    opens_before_hours:int=Field(default=24,ge=0,le=168)

@app.post('/api/queues',status_code=201)
def create(data:Create,request:Request):
    u=identity(request);manager(u);item=lesson(data.occurrence_id)
    if not item['queue_enabled'] or item['status']=='cancelled':fail('queue_unavailable',409)
    with DB.begin() as db:
        # Serialize concurrent creation by locking singleton cursor.
        db.execute(select(Cursor).where(Cursor.id==1).with_for_update()).scalar_one()
        q=db.scalar(select(Queue).where(Queue.occurrence_id==data.occurrence_id))
        if not q:
            q=Queue(**data.model_dump(),lesson_revision=item['revision']);db.add(q);db.flush()
            audit(db,q,u,'queue_created',q.id)
        return queue_json(db,q,item,u)

@app.get('/api/queues')
def list_queues(request:Request,occurrence_id:str|None=None,mine:bool=False):
    u=identity(request)
    with DB() as db:
        query=select(Queue)
        if occurrence_id:query=query.where(Queue.occurrence_id==occurrence_id)
        if mine:query=query.where(Queue.id.in_(select(Entry.queue_id).where(Entry.user_id==u['id'],Entry.status.in_(ACTIVE))))
        queues=db.scalars(query.order_by(Queue.updated_at.desc()).limit(100)).all()
        # Counts are cheap; details pull authoritative schedule on opening.
        return [{'id':q.id,'occurrence_id':q.occurrence_id,'state':q.state,'revision':q.revision,'capacity':q.capacity,'active_count':db.scalar(select(func.count()).select_from(Entry).where(Entry.queue_id==q.id,Entry.status.in_(ACTIVE))),'mine':bool(db.scalar(select(Entry.id).where(Entry.queue_id==q.id,Entry.user_id==u['id'],Entry.status.in_(ACTIVE))))} for q in queues]

@app.get('/api/queues/{qid}')
def get_queue(qid:str,request:Request):
    u=identity(request)
    with DB() as db:
        q=db.get(Queue,qid)
        if not q:fail('not_found',404)
        oid=q.occurrence_id
    item=lesson(oid)
    with DB.begin() as db:
        q=locked(db,qid);sync_queue(db,q,item);db.flush()
        return queue_json(db,q,item,u)

class Action(Input):
    action:Literal['open','pause','close','join','leave','next','done','skip','remove','configure']
    entry_id:str=Field(default='',max_length=36)
    task:str=Field(default='',max_length=120)
    reason:str=Field(default='',max_length=250)
    revision:int|None=Field(default=None,ge=1)
    capacity:int=Field(default=30,ge=1,le=100)
    minutes_per_student:int=Field(default=7,ge=1,le=60)
    opens_before_hours:int=Field(default=24,ge=0,le=168)

@app.post('/api/queues/{qid}/actions')
def action(qid:str,data:Action,request:Request):
    u=identity(request)
    key=request.headers.get('Idempotency-Key','')
    if not 16<=len(key)<=100:fail('idempotency_key_required',422)
    fingerprint=digest(json.dumps(data.model_dump(),sort_keys=True))
    command_key=f'{u["id"]}:{qid}:{key}'
    if data.action not in ('join','leave'):manager(u)
    with DB() as db:
        q=db.get(Queue,qid)
        if not q:fail('not_found',404)
        oid=q.occurrence_id
    # Never admit or call a student using cached schedule.
    item=lesson(oid)
    with DB.begin() as db:
        # Serialize event sequence allocation with commit order and reconciliation.
        db.execute(select(Cursor).where(Cursor.id==1).with_for_update()).scalar_one()
        q=locked(db,qid)
        old=db.get(Command,command_key)
        if old:
            if old.fingerprint!=fingerprint:fail('idempotency_conflict',409)
            return queue_json(db,q,item,u)
        if data.action not in ('join','leave'):
            if data.revision is None or data.revision!=q.revision:fail('revision_conflict',409)
        active=db.scalars(select(Entry).where(Entry.queue_id==qid,Entry.status.in_(ACTIVE)).order_by(Entry.ticket)).all()
        called=next((e for e in active if e.status=='called'),None)
        mine=next((e for e in active if e.user_id==u['id']),None)
        target=next((e for e in active if e.id==data.entry_id),None)
        if data.action=='join':
            if mine:
                db.add(Command(key=command_key,fingerprint=fingerprint))
                return queue_json(db,q,item,u)
            reason=can_join(q,item)
            if reason:fail(reason,409)
            if item['subgroup'] and item['subgroup']!=u['subgroup']:fail('wrong_subgroup',403)
            if len(active)>=q.capacity:fail('queue_full',409)
            if not data.task.strip():fail('task_required',422)
            if db.scalar(select(Entry).where(Entry.queue_id==qid,Entry.user_id==u['id'],Entry.status=='done')):
                fail('already_completed',409)
            q.last_ticket+=1
            e=Entry(queue_id=qid,user_id=u['id'],name=u['name'],ticket=q.last_ticket,task=data.task.strip());db.add(e);db.flush()
            audit(db,q,u,'joined',e.id,{'ticket':e.ticket,'task':e.task})
        elif data.action=='leave':
            if mine:
                if mine.status=='called':fail('already_called',409)
                mine.status='left';mine.ended_at=now();audit(db,q,u,'left',mine.id)
        elif data.action=='configure':
            if data.capacity<len(active):fail('capacity_below_active',409)
            q.capacity,q.minutes_per_student,q.opens_before_hours=data.capacity,data.minutes_per_student,data.opens_before_hours
            audit(db,q,u,'queue_configured',qid,data.model_dump())
        elif data.action=='open':
            if item['status']!='confirmed' or not item['queue_enabled']:fail('queue_unavailable',409)
            if current_utc()>=datetime.fromisoformat(item['ends_at']):fail('lesson_ended',409)
            q.state='open';q.lesson_revision=item['revision'];q.blocked_reason='';audit(db,q,u,'queue_opened',qid)
        elif data.action=='pause':
            if q.state!='open':fail('invalid_transition',409)
            q.state='paused';audit(db,q,u,'queue_paused',qid)
        elif data.action=='close':
            if not data.reason:fail('reason_required',422)
            q.state='closed';q.blocked_reason=data.reason
            for e in active:e.status='cancelled';e.ended_at=now()
            audit(db,q,u,'queue_closed',qid,{'reason':data.reason})
        elif data.action=='next':
            reason=can_join(q,item)
            if reason:fail(reason,409)
            if current_utc()<datetime.fromisoformat(item['starts_at']):fail('lesson_not_started',409)
            if called:fail('finish_current_first',409)
            waiting=[e for e in active if e.status=='waiting']
            if not waiting:fail('queue_empty',409)
            e=waiting[0]
            member=student(e.user_id)
            if member['status']!='active' or (item['subgroup'] and member['subgroup']!=item['subgroup']):fail('student_ineligible',409)
            if db.scalar(select(Entry).where(Entry.user_id==e.user_id,Entry.status=='called')):fail('student_busy_elsewhere',409)
            e.status='called';e.called_at=now()
            try:db.flush()
            except IntegrityError:fail('student_busy_elsewhere',409)
            audit(db,q,u,'called',e.id)
            db.add(Event(type='queue.called',data={'user_id':e.user_id,'entry_id':e.id,'occurrence_id':q.occurrence_id}))
        elif data.action in ('done','skip','remove'):
            if not target:fail('entry_not_active',409)
            if data.action in ('done','skip'):
                if item['status']=='cancelled':fail('lesson_cancelled',409)
                if item['status']!='confirmed':fail('lesson_unconfirmed',409)
                if current_utc()>=datetime.fromisoformat(item['ends_at']):fail('lesson_ended',409)
                if q.lesson_revision!=item['revision'] or q.blocked_reason=='lesson_changed':fail('lesson_changed',409)
                if target.status!='called':fail('entry_not_called',409)
            if data.action=='remove' and not data.reason:fail('reason_required',422)
            target.status={'done':'done','skip':'skipped','remove':'removed'}[data.action];target.ended_at=now()
            audit(db,q,u,target.status,target.id,{'reason':data.reason})
        q.revision+=1;q.updated_at=now()
        db.add(Command(key=command_key,fingerprint=fingerprint))
        db.flush()
        return queue_json(db,q,item,u)

@app.get('/api/queues/{qid}/audit')
def audits(qid:str,request:Request):
    u=identity(request);manager(u)
    with DB() as db:
        return [{'id':a.id,'actor':a.actor,'action':a.action,'target':a.target,'at':a.at.isoformat()+'Z','data':a.data} for a in db.scalars(select(Audit).where(Audit.queue_id==qid).order_by(Audit.at.desc()).limit(200))]

@app.get('/api/queues/activity/log')
def activity(request:Request):
    u=identity(request);manager(u)
    with DB() as db:
        return [{'id':a.id,'actor':a.actor,'action':a.action,'target':a.target,'at':a.at.isoformat()+'Z','data':a.data} for a in db.scalars(select(Audit).order_by(Audit.at.desc()).limit(200))]


@app.get('/internal/events/head')
def event_head(request:Request):
    internal(request)
    with DB() as db:return {'seq':db.scalar(select(func.max(Event.seq))) or 0}

@app.get('/internal/events')
def event_feed(request:Request,after:int=Query(default=0,ge=0)):
    internal(request)
    with DB() as db:
        return [{'seq':e.seq,'id':e.id,'type':e.type,'data':e.data,'at':e.at.isoformat()+'Z'}
                for e in db.scalars(select(Event).where(Event.seq>after).order_by(Event.seq).limit(200))]

@app.get('/internal/calls/{entry_id}')
def call_status(entry_id:str,request:Request):
    internal(request)
    with DB() as db:
        entry=db.get(Entry,entry_id)
        if not entry or entry.status!='called':return {'active':False}
        q=db.get(Queue,entry.queue_id)
        oid=q.occurrence_id
        active=q.state=='open'
        revision=q.lesson_revision
    item=lesson(oid)
    return {'active':active and item['revision']==revision and item['status']=='confirmed' and item['queue_enabled'] and current_utc()<datetime.fromisoformat(item['ends_at']),
            'ends_at':datetime.fromisoformat(item['ends_at']).astimezone(timezone.utc).isoformat()}
