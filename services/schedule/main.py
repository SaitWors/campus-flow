import os
import hashlib
import hmac
import json
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
from services.schedule.models import Base, Settings, Rule, Occurrence, Audit, Event, TitleTranslation, Subject, TimePresets
from services.schedule.translation import TitleTranslator, with_translation
from services.schedule.catalog import migrate_catalog, remember_subject, PresetsInput

engine, DB=database('schedule')
translator=TitleTranslator(DB)
DEFAULT_SETTINGS={'group':'БВТ2302','program':'09.03.01','course':4,'semester_start':'2026-09-01','semester_end':'2027-01-31','anchor_monday':'2026-08-31','anchor_parity':'odd','timezone':'Europe/Moscow','configured':False}

@asynccontextmanager
async def lifespan(app):
    service_secret();migrate(engine,Base,(lambda conn: TitleTranslation.__table__.create(conn,checkfirst=True),migrate_catalog))
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
    preview_token:str=Field(default='',max_length=64)

class ExceptionInput(LessonInput):
    date:date
    status:Literal['confirmed','pending','cancelled']
    revision:int=Field(ge=1)


def generate_rule(db,rule,config,future_only=False):
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
            if not item and not (future_only and d<today):
                item=Occurrence(id=oid,rule_id=rule.id,original_date=d.isoformat(),date=d.isoformat(),data=content)
                db.add(item);db.flush();event(db,'occurrence.created',item)
            elif item and not item.overridden and d>=today and item.data!=content:
                item.data=content;item.revision+=1;item.updated_at=now();event(db,'occurrence.updated',item)
        d+=timedelta(days=1)
    db.flush()
    for item in db.scalars(select(Occurrence).where(Occurrence.rule_id==rule.id)):
        if item.original_date not in wanted and (rule.archived or (item.date>=today.isoformat() and not item.overridden)):
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
        items=db.scalars(select(Rule).where(Rule.archived==False)).all()
        items.sort(key=lambda r:(r.data.get('title','').casefold(),r.data.get('weekday',0),r.data.get('start','')))
        return [{**with_translation(r.data,db),'id':r.id,'revision':r.revision} for r in items]

@app.post('/api/schedule/rules',status_code=201)
def add_rule(data:RuleInput,request:Request):
    u=identity(request);manager(u)
    with DB.begin() as db:
        config=settings(db,True).data
        r=Rule(data=data.model_dump(exclude={'revision','preview_token'}));db.add(r);db.flush()
        generate_rule(db,r,config);conflicts(db);remember_subject(db,r.data);audit(db,u,'rule_created',r.id,r.data)
        return {**with_translation(r.data,db),'id':r.id,'revision':r.revision}

@app.put('/api/schedule/rules/{rule_id}')
def edit_rule(rule_id:str,data:RuleInput,request:Request):
    u=identity(request);manager(u)
    with DB.begin() as db:
        config=settings(db,True).data;r=db.get(Rule,rule_id)
        if not r or r.archived:fail('not_found',404)
        if r.revision!=data.revision:fail('revision_conflict',409)
        if not data.preview_token:fail('preview_required',409)
        if not hmac.compare_digest(data.preview_token,rule_preview_token(db,r,data,config)):fail('preview_stale',409)
        before=r.data;r.data={**data.model_dump(exclude={'revision','preview_token'}),**({'demo':r.data['demo']} if 'demo' in r.data else {})};r.revision+=1
        generate_rule(db,r,config,future_only=True);conflicts(db);remember_subject(db,r.data);audit(db,u,'rule_updated',r.id,{'before':before,'after':r.data})
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
        return [row(i,config,db) for i in items if not i.data.get('removed_from_template') and not i.data.get('removed_from_schedule') and (not subgroup or i.data['subgroup'] in (0,subgroup))]

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
        remember_subject(db,item.data)
        event(db,'occurrence.cancelled' if data.status=='cancelled' else 'occurrence.updated',item)
        audit(db,u,'occurrence_updated',oid,{'before':before,'after':row(item,config,db)})
        return row(item,config,db)

@app.delete('/api/schedule/occurrences/{oid}')
def delete_occurrence(oid:str,request:Request,revision:int):
    u=identity(request)
    if u['role'] not in ('admin','head'):fail('forbidden',403)
    with DB.begin() as db:
        config=settings(db,True).data
        item=db.get(Occurrence,oid)
        if not item or item.data.get('removed_from_schedule') or item.data.get('removed_from_template'):fail('not_found',404)
        if item.revision!=revision:fail('revision_conflict',409)
        before=row(item,config,db)
        item.data={**item.data,'status':'cancelled','removed_from_schedule':True}
        item.overridden=True;item.revision+=1;item.updated_at=now()
        db.flush();event(db,'occurrence.cancelled',item)
        audit(db,u,'occurrence_deleted',oid,{'before':before})
    return {'ok':True}

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
                not item.data.get('removed_from_schedule') and
                (not subgroup or item.data['subgroup'] in (0,subgroup))]

@app.get('/api/schedule/subjects')
def subjects(request:Request):
    manager(identity(request))
    with DB() as db:
        return [with_translation({'title':s.title,'title_en':s.title_en},db)
                for s in db.scalars(select(Subject).order_by(Subject.title))]


@app.get('/api/schedule/time-presets')
def time_presets(request:Request):
    manager(identity(request))
    with DB() as db:
        p=db.get(TimePresets,1)
        return {'items':p.data,'revision':p.revision}


@app.put('/api/schedule/time-presets')
def update_time_presets(data:PresetsInput,request:Request):
    u=identity(request);manager(u)
    with DB.begin() as db:
        p=db.scalar(select(TimePresets).where(TimePresets.id==1).with_for_update())
        if p.revision!=data.revision:fail('revision_conflict',409)
        p.data=[s.model_dump() for s in sorted(data.items,key=lambda s:s.start)]
        p.revision+=1
        audit(db,u,'time_presets_updated','presets',{'items':p.data})
        return {'items':p.data,'revision':p.revision}


def rule_preview_token(db,rule,data,config):
    state={
        'rule':rule.id, 'request':data.model_dump(exclude={'preview_token'}),
        'settings':settings(db).revision,
        'date':datetime.now(ZoneInfo(config['timezone'])).date().isoformat(),
        'rules':[(r.id,r.revision,r.archived) for r in db.scalars(select(Rule).order_by(Rule.id))],
        'occurrences':[(i.id,i.revision) for i in db.scalars(select(Occurrence).order_by(Occurrence.id))],
    }
    return hmac.new(service_secret().encode(),json.dumps(state,sort_keys=True).encode(),hashlib.sha256).hexdigest()


def preview_item(item):
    return {'id':item.id,'date':item.date,**item.data}


@app.post('/api/schedule/rules/{rule_id}/preview')
def preview_rule(rule_id:str,data:RuleInput,request:Request):
    manager(identity(request))
    # Simulate the actual generator under the same lock, then roll everything
    # back: neither occurrences, events, translations nor audits are committed.
    with DB() as db:
        config=settings(db,True).data
        rule=db.get(Rule,rule_id)
        if not rule or rule.archived:fail('not_found',404)
        if rule.revision!=data.revision:fail('revision_conflict',409)
        token=rule_preview_token(db,rule,data,config)
        items=db.scalars(select(Occurrence).where(Occurrence.rule_id==rule_id)).all()
        before={i.id:preview_item(i) for i in items}
        exceptions=sum(i.overridden for i in items)
        today=datetime.now(ZoneInfo(config['timezone'])).date().isoformat()
        past=sum(i.date<today for i in items)
        rule.data={**data.model_dump(exclude={'revision','preview_token'}),**({'demo':rule.data['demo']} if 'demo' in rule.data else {})}
        generate_rule(db,rule,config,future_only=True)
        conflicts(db)
        changes=[]
        for item in db.scalars(select(Occurrence).where(Occurrence.rule_id==rule_id).order_by(Occurrence.date,Occurrence.id)):
            after=preview_item(item)
            if before.get(item.id)!=after:
                changes.append({'before':before.get(item.id),'after':after})
        return {'preview_token':token,'changes':changes,'preserved_exceptions':exceptions,'past_lessons':past}


@app.get('/api/schedule/guest/calendar.ics')
def subscribe_calendar(lang:Literal['ru','en']='ru',subgroup:int=Query(default=0,ge=0,le=2)):
    """Stable subscription URL. Only fields already present in the guest API."""
    with DB() as db:
        config=settings(db).data
        lines=['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//Campus Flow//Timetable//EN',
               'CALSCALE:GREGORIAN','METHOD:PUBLISH','REFRESH-INTERVAL;VALUE=DURATION:PT1H',
               'X-PUBLISHED-TTL:PT1H',f'X-WR-CALNAME:{ics_escape(config["group"])}']
        for occurrence in db.scalars(select(Occurrence).order_by(Occurrence.date,Occurrence.id)):
            item=row(occurrence,config,db)
            # Tombstones remain in the feed so moved/removed lessons disappear
            # from calendars already subscribed to a subgroup.
            cancelled=item['status']=='cancelled' or item.get('removed_from_template') or item.get('removed_from_schedule') or (subgroup and item['subgroup'] not in (0,subgroup))
            title=(item['title_en'] or item.get('title_en_auto') or item['title']) if lang=='en' else item['title']
            def utc(key):return datetime.fromisoformat(item[key]).astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
            stamp=occurrence.updated_at.strftime('%Y%m%dT%H%M%SZ')
            lines+=['BEGIN:VEVENT',f'UID:{item["id"]}@campus-flow',f'SEQUENCE:{occurrence.revision}',
                    f'DTSTAMP:{stamp}',f'LAST-MODIFIED:{stamp}',f'DTSTART:{utc("starts_at")}',
                    f'DTEND:{utc("ends_at")}',f'SUMMARY:{ics_escape(title)}',
                    f'LOCATION:{ics_escape(item["room"])}',
                    f'STATUS:{"CANCELLED" if cancelled else "TENTATIVE" if item["status"]=="pending" else "CONFIRMED"}','END:VEVENT']
        lines+=['END:VCALENDAR']
        return Response('\r\n'.join(fold_ics(s) for s in lines)+'\r\n',media_type='text/calendar')
