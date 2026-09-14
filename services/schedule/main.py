import os
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Request, Query
from fastapi.responses import Response
from pydantic import Field, model_validator
from sqlalchemy import select, or_, func
from services.common.core import database, migrate, setup_app, Input, now, fail, identity, internal, manager, service_secret
from services.schedule.models import Base, Settings, Rule, Occurrence, Audit, Event, TitleTranslation
from services.schedule.translation import TitleTranslator, with_translation

engine, DB=database('schedule')
translator=TitleTranslator(DB)
DEFAULT_SETTINGS={'group':'БВТ2302','program':'09.03.01','course':4,'semester_start':'2026-09-01','semester_end':'2027-01-31','anchor_monday':'2026-08-31','anchor_parity':'odd','timezone':'Europe/Moscow','configured':False}

@asynccontextmanager
async def lifespan(app):
    service_secret();migrate(engine,Base,(lambda conn: TitleTranslation.__table__.create(conn,checkfirst=True),))
    with DB.begin() as db:
        if not db.get(Settings,1):db.add(Settings(id=1,data=DEFAULT_SETTINGS))
    translator.start()
    try:
        yield
    finally:
        translator.stop()

app=setup_app('Campus Flow · Schedule',engine,lifespan)

def settings(db,lock=False):
    return db.scalar(select(Settings).where(Settings.id==1).with_for_update()) if lock else db.get(Settings,1)

def parity(day,config):
    d=date.fromisoformat(str(day));anchor=date.fromisoformat(config['anchor_monday'])
    week=(d-anchor).days//7
    odd=(week%2==0)==(config['anchor_parity']=='odd')
    return 'odd' if odd else 'even'

def audit(db,user,action,target,data=None):
    db.add(Audit(actor=user['name'],action=action,target=target,data=data or {}))

def event(db,kind,item):
    db.add(Event(type=kind,data={'occurrence_id':item.id,'revision':item.revision,'status':item.data['status'],'subgroup':item.data.get('subgroup',0)}))

def row(item,config,db):
    d={**item.data,'id':item.id,'rule_id':item.rule_id,'original_date':item.original_date,'date':item.date,'revision':item.revision,'overridden':item.overridden}
    tz=ZoneInfo(config['timezone'])
    for field,key in [('start','starts_at'),('end','ends_at')]:
        d[key]=datetime.fromisoformat(f'{item.date}T{d[field]}').replace(tzinfo=tz).isoformat()
    d['parity']=parity(item.date,config)
    return with_translation(d,db)

class ConfigInput(Input):
    revision:int=Field(ge=1)
    group:str=Field(min_length=2,max_length=40)
    program:str=Field(min_length=2,max_length=40)
    course:int=Field(ge=1,le=6)
    semester_start:date
    semester_end:date
    anchor_monday:date
    anchor_parity:Literal['odd','even']
    timezone:str=Field(default='Europe/Moscow',max_length=60)
    configured:bool=True
    @model_validator(mode='after')
    def valid(self):
        if self.anchor_monday.weekday()!=0:raise ValueError('anchor_must_be_monday')
        if not 0<(self.semester_end-self.semester_start).days<=366:raise ValueError('invalid_semester')
        try:ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError:raise ValueError('invalid_timezone')
        return self

class LessonInput(Input):
    title:str=Field(min_length=2,max_length=120)
    title_en:str=Field(default='',max_length=120)
    kind:Literal['lecture','lab','practice']
    teacher:str=Field(default='',max_length=100)
    room:str=Field(default='',max_length=80)
    mode:Literal['onsite','remote','hybrid']='onsite'
    meeting_url:str=Field(default='',max_length=500)
    note:str=Field(default='',max_length=500)
    start:str=Field(pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    end:str=Field(pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    subgroup:int=Field(default=0,ge=0,le=2)
    queue_enabled:bool=False
    @model_validator(mode='after')
    def valid(self):
        if self.end<=self.start:raise ValueError('end_before_start')
        if self.meeting_url and not self.meeting_url.startswith('https://'):raise ValueError('https_url_required')
        if self.kind=='lecture' and self.queue_enabled:raise ValueError('lecture_queue_unavailable')
        return self

class RuleInput(LessonInput):
    weekday:int=Field(ge=0,le=6)
    parity:Literal['all','odd','even']='all'
    revision:int=Field(default=0,ge=0)

class ExceptionInput(LessonInput):
    date:date
    status:Literal['confirmed','pending','cancelled']
    revision:int=Field(ge=1)


def generate_rule(db,rule,config):
    start=date.fromisoformat(config['semester_start']);end=date.fromisoformat(config['semester_end'])
    today=datetime.now(ZoneInfo(config['timezone'])).date()
    wanted=set();d=start
    while d<=end:
        if not rule.archived and d.weekday()==rule.data['weekday'] and (rule.data['parity']=='all' or parity(d,config)==rule.data['parity']):
            wanted.add(d.isoformat())
            oid=str(uuid.uuid5(uuid.NAMESPACE_URL,f'campus-flow:{rule.id}:{d.isoformat()}'))
            item=db.get(Occurrence,oid)
            content={k:v for k,v in rule.data.items() if k not in ('weekday','parity')}
            content.update(status='confirmed',demo=bool(rule.data.get('demo',False)))
            if not item:
                item=Occurrence(id=oid,rule_id=rule.id,original_date=d.isoformat(),date=d.isoformat(),data=content)
                db.add(item);db.flush();event(db,'occurrence.created',item)
            elif not item.overridden and d>=today and item.data!=content:
                item.data=content;item.revision+=1;item.updated_at=now();event(db,'occurrence.updated',item)
        d+=timedelta(days=1)
    db.flush()
    for item in db.scalars(select(Occurrence).where(Occurrence.rule_id==rule.id)):
        if item.original_date not in wanted and item.date>=today.isoformat() and not item.overridden:
            if item.data.get('status')!='cancelled' or not item.data.get('removed_from_template'):
                item.data={**item.data,'status':'cancelled','removed_from_template':True}
                item.revision+=1;item.updated_at=now();event(db,'occurrence.cancelled',item)
    db.flush()


def conflicts(db):
    days={}
    for item in db.scalars(select(Occurrence).order_by(Occurrence.date)):
        if item.data['status']=='cancelled':continue
        peers=days.setdefault(item.date,[])
        for p in peers:
            same_group=not p.data['subgroup'] or not item.data['subgroup'] or p.data['subgroup']==item.data['subgroup']
            if same_group and p.data['start']<item.data['end'] and item.data['start']<p.data['end']:
                fail({'code':'schedule_conflict','date':item.date,'title':p.data['title'],'other':item.data['title']},409)
        peers.append(item)

@app.get('/api/schedule/settings')
def get_settings(request:Request):
    identity(request)
    with DB() as db:
        s=settings(db);return {**s.data,'revision':s.revision}

@app.put('/api/schedule/settings')
def set_settings(data:ConfigInput,request:Request):
    u=identity(request);manager(u)
    with DB.begin() as db:
        s=settings(db,True)
        if s.revision!=data.revision:fail('revision_conflict',409)
        config=data.model_dump(mode='json',exclude={'revision'})
        s.data=config;s.revision+=1
        for rule in db.scalars(select(Rule).where(Rule.archived==False)).all():generate_rule(db,rule,config)
        conflicts(db);audit(db,u,'settings_updated','semester',config)
        return {**config,'revision':s.revision}

@app.get('/api/schedule/rules')
def rules(request:Request):
    u=identity(request);manager(u)
    with DB() as db:
        return [{**with_translation(r.data,db),'id':r.id,'revision':r.revision} for r in db.scalars(select(Rule).where(Rule.archived==False)).all()]

@app.post('/api/schedule/rules',status_code=201)
def add_rule(data:RuleInput,request:Request):
    u=identity(request);manager(u)
    with DB.begin() as db:
        config=settings(db,True).data
        r=Rule(data=data.model_dump(exclude={'revision'}));db.add(r);db.flush()
        generate_rule(db,r,config);conflicts(db);audit(db,u,'rule_created',r.id,r.data)
        return {**with_translation(r.data,db),'id':r.id,'revision':r.revision}

@app.put('/api/schedule/rules/{rule_id}')
def edit_rule(rule_id:str,data:RuleInput,request:Request):
    u=identity(request);manager(u)
    with DB.begin() as db:
        config=settings(db,True).data;r=db.get(Rule,rule_id)
        if not r or r.archived:fail('not_found',404)
        if r.revision!=data.revision:fail('revision_conflict',409)
        before=r.data;r.data=data.model_dump(exclude={'revision'});r.revision+=1
        generate_rule(db,r,config);conflicts(db);audit(db,u,'rule_updated',r.id,{'before':before,'after':r.data})
        return {**with_translation(r.data,db),'id':r.id,'revision':r.revision}

@app.delete('/api/schedule/rules/{rule_id}')
def archive_rule(rule_id:str,request:Request,revision:int):
    u=identity(request);manager(u)
    with DB.begin() as db:
        config=settings(db,True).data;r=db.get(Rule,rule_id)
        if not r:fail('not_found',404)
        if r.revision!=revision:fail('revision_conflict',409)
        r.archived=True;r.revision+=1;generate_rule(db,r,config);audit(db,u,'rule_archived',r.id)
    return {'ok':True}

@app.get('/api/schedule/occurrences')
def occurrences(request:Request,start:date,end:date,subgroup:int=Query(default=0,ge=0,le=2)):
    identity(request)
    if end<start or (end-start).days>93:fail('range_too_large',422)
    with DB() as db:
        config=settings(db).data
        items=db.scalars(select(Occurrence).where(Occurrence.date>=start.isoformat(),Occurrence.date<=end.isoformat()).order_by(Occurrence.date)).all()
        return [row(i,config,db) for i in items if not i.data.get('removed_from_template') and (not subgroup or i.data['subgroup'] in (0,subgroup))]

@app.get('/api/schedule/occurrences/{oid}')
def get_occurrence(oid:str,request:Request):
    identity(request)
    return fetch_occurrence(oid)

def fetch_occurrence(oid):
    with DB() as db:
        item=db.get(Occurrence,oid)
        if not item:fail('not_found',404)
        return row(item,settings(db).data,db)

@app.get('/internal/occurrences/{oid}')
def internal_occurrence(oid:str,request:Request):
    internal(request);return fetch_occurrence(oid)

@app.patch('/api/schedule/occurrences/{oid}')
def exception(oid:str,data:ExceptionInput,request:Request):
    u=identity(request);manager(u)
    with DB.begin() as db:
        config=settings(db,True).data
        item=db.get(Occurrence,oid)
        if not item:fail('not_found',404)
        if item.revision!=data.revision:fail('revision_conflict',409)
        if not config['semester_start']<=data.date.isoformat()<=config['semester_end']:fail('outside_semester',422)
        before=row(item,config,db)
        item.data={**data.model_dump(mode='json',exclude={'revision','date'}),'demo':item.data.get('demo',False)}
        item.date=data.date.isoformat();item.overridden=True;item.revision+=1;item.updated_at=now()
        db.flush();conflicts(db)
        event(db,'occurrence.cancelled' if data.status=='cancelled' else 'occurrence.updated',item)
        audit(db,u,'occurrence_updated',oid,{'before':before,'after':row(item,config,db)})
        return row(item,config,db)

@app.get('/api/schedule/events')
def public_events(request:Request,after:int=Query(default=0,ge=0)):
    identity(request);return get_events(after)

def get_events(after):
    with DB() as db:
        return [{'seq':e.seq,'id':e.id,'type':e.type,'data':e.data,'at':e.at.isoformat()+'Z'} for e in db.scalars(select(Event).where(Event.seq>after).order_by(Event.seq).limit(200))]

@app.get('/internal/events')
def internal_events(request:Request,after:int=Query(default=0,ge=0)):
    internal(request);return get_events(after)

@app.get('/api/schedule/audit')
def audits(request:Request):
    u=identity(request);manager(u)
    with DB() as db:
        return [{'id':a.id,'actor':a.actor,'action':a.action,'target':a.target,'at':a.at.isoformat()+'Z','data':a.data} for a in db.scalars(select(Audit).order_by(Audit.at.desc()).limit(200))]


def ics_escape(value):
    return str(value).replace('\\','\\\\').replace('\r','').replace('\n','\\n').replace(';','\\;').replace(',','\\,')

def fold_ics(line):
    # RFC 5545: 75 octets, without splitting a UTF-8 character.
    out=[];part=''
    for c in line:
        if len((part+c).encode('utf-8'))>75:
            out.append(part);part=' '+c
        else:part+=c
    out.append(part);return '\r\n'.join(out)

@app.get('/api/schedule/calendar.ics')
def export_calendar(request:Request,start:date,end:date,lang:Literal['ru','en']='ru',subgroup:int=Query(default=0,ge=0,le=2)):
    items=occurrences(request,start,end,subgroup)
    lines=['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//Campus Flow//BVТ2302//EN','CALSCALE:GREGORIAN','METHOD:PUBLISH']
    for item in items:
        def utc(key):return datetime.fromisoformat(item[key]).astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        title=(item['title_en'] or item.get('title_en_auto') or item['title']) if lang=='en' else item['title']
        lines+=['BEGIN:VEVENT',f'UID:{item["id"]}@campus-flow',f'SEQUENCE:{item["revision"]}',f'DTSTAMP:{now().strftime("%Y%m%dT%H%M%SZ")}',f'DTSTART:{utc("starts_at")}',f'DTEND:{utc("ends_at")}',f'SUMMARY:{ics_escape(title)}',f'LOCATION:{ics_escape(item["room"])}',f'DESCRIPTION:{ics_escape(item["teacher"]+" · "+item["note"]+" "+item["meeting_url"])}',f'STATUS:{"CANCELLED" if item["status"]=="cancelled" else "TENTATIVE" if item["status"]=="pending" else "CONFIRMED"}','END:VEVENT']
    lines+=['END:VCALENDAR']
    return Response('\r\n'.join(fold_ics(s) for s in lines)+'\r\n',media_type='text/calendar',headers={'Content-Disposition':'attachment; filename="campus-flow.ics"'})

@app.post('/api/schedule/demo')
def seed_demo(request:Request):
    u=identity(request);manager(u)
    with DB.begin() as db:
        s=settings(db,True)
        if db.scalar(select(Rule).limit(1)):fail('demo_requires_empty_schedule',409)
        # Explicit opt-in, clearly labelled examples, never claimed as MTUCI's schedule.
        examples=[(0,'09:30','11:05','Высоконагруженные приложения','High-load applications','lecture','all','А-421',False),(0,'11:20','12:55','Высоконагруженные приложения','High-load applications','lab','all','А-308',True),(0,'13:10','14:45','Проектирование информационных систем','Information systems design','practice','all','А-416',False),(1,'09:30','11:05','Технологии баз данных','Database technologies','lecture','all','А-312',False),(1,'11:20','12:55','Технологии баз данных','Database technologies','lab','odd','А-308',True),(2,'11:20','12:55','Распределённые системы','Distributed systems','lab','all','Онлайн',True),(3,'09:30','11:05','Информационная безопасность','Information security','practice','all','А-416',False),(4,'11:20','12:55','Распределённые системы','Distributed systems','lecture','even','А-421',False)]
        for weekday,start,end,title,title_en,kind,p,room,q in examples:
            r=Rule(data={'title':title,'title_en':title_en,'kind':kind,'teacher':'Пример преподавателя','room':room,'mode':'remote' if room=='Онлайн' else 'onsite','meeting_url':'','note':'Демонстрационное занятие. Замените реальным расписанием.','start':start,'end':end,'subgroup':0,'queue_enabled':q,'weekday':weekday,'parity':p,'demo':True})
            db.add(r);db.flush();generate_rule(db,r,s.data)
        audit(db,u,'demo_created','schedule')
    return {'ok':True}

@app.delete('/api/schedule/demo')
def remove_demo(request:Request):
    u=identity(request);manager(u)
    with DB.begin() as db:
        settings(db,True)
        for r in db.scalars(select(Rule)).all():
            if r.data.get('demo'):r.archived=True;r.revision+=1
        for item in db.scalars(select(Occurrence)).all():
            if item.data.get('demo'):
                item.data={**item.data,'status':'cancelled','removed_from_template':True};item.revision+=1;event(db,'occurrence.cancelled',item)
        audit(db,u,'demo_removed','schedule')
    return {'ok':True}

class TitlePreview(Input):
    title:str=Field(min_length=2,max_length=120)

@app.post('/api/schedule/title-preview')
def preview_title(data:TitlePreview,request:Request):
    user=identity(request);manager(user)
    translator.limit_preview(user['id'])
    value=translator.resolve(data.title)
    return {'title_en_auto':value,'translation_pending':os.getenv('TRANSLATION_WORKER','true').lower()=='true' and not bool(value),'translation_enabled':os.getenv('TRANSLATION_WORKER','true').lower()=='true'}


@app.get('/internal/events/head')
def event_head(request:Request):
    internal(request)
    with DB() as db:return {'seq':db.scalar(select(func.max(Event.seq))) or 0}


# Guest responses use explicit public fields. Never reuse an authenticated
# response wholesale: private meeting links, notes, people and queues stay private.
PUBLIC_LESSON_FIELDS = {
    'id', 'title', 'title_en', 'title_en_auto', 'kind', 'room', 'mode',
    'start', 'end', 'subgroup', 'status', 'date', 'starts_at', 'ends_at',
    'parity', 'demo',
}
PUBLIC_SETTINGS_FIELDS = set(DEFAULT_SETTINGS)


@app.get('/api/schedule/guest/settings')
def guest_settings():
    with DB() as db:
        s = settings(db)
        return {k: v for k, v in s.data.items() if k in PUBLIC_SETTINGS_FIELDS}


@app.get('/api/schedule/guest/occurrences')
def guest_occurrences(start:date, end:date, subgroup:int=Query(default=0,ge=0,le=2)):
    if end < start or (end - start).days > 42:
        fail('range_too_large',422)
    with DB() as db:
        config = settings(db).data
        items = db.scalars(select(Occurrence).where(
            Occurrence.date >= start.isoformat(), Occurrence.date <= end.isoformat()
        ).order_by(Occurrence.date, Occurrence.id)).all()
        return [{k:v for k,v in row(item,config,db).items() if k in PUBLIC_LESSON_FIELDS}
                for item in items if not item.data.get('removed_from_template') and
                (not subgroup or item.data['subgroup'] in (0,subgroup))]
