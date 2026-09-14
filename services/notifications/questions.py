"""Private, explicitly addressed conversations. Administrative power does not
grant access to other people's conversations in this API."""
import json
from datetime import timedelta

from fastapi import Query, Request
from pydantic import Field, model_validator
from sqlalchemy import func, or_, select

from services.common.core import Input, digest, fail, now
from services.notifications.models import Audit, Question, QuestionMessage

STAFF = ('head', 'deputy', 'admin')


def accessible(data, user):
    return user['id'] == data['owner_id'] or (
        user['id'] == data['recipient_id'] and user['role'] in STAFF)


def key(request, user):
    value = request.headers.get('Idempotency-Key', '')
    if not 16 <= len(value) <= 100 or any(ord(c) < 33 or ord(c) > 126 for c in value):
        fail('idempotency_key_required', 422)
    return user['id'] + ':' + value


def fingerprint(data):
    return digest(json.dumps(data, sort_keys=True))


class MessageInput(Input):
    body: str = Field(min_length=2, max_length=2000)

    @model_validator(mode='after')
    def valid(self):
        if any(ord(ch) < 32 and ch not in '\n\r\t' for ch in self.body):
            raise ValueError('invalid_message')
        return self


class QuestionInput(MessageInput):
    recipient_id: str = Field(pattern=r'^[0-9a-f-]{36}$')
    title: str = Field(min_length=2, max_length=120)

    @model_validator(mode='after')
    def valid_title(self):
        if any(ord(ch) < 32 for ch in self.title):
            raise ValueError('invalid_title')
        return self


class StateInput(Input):
    revision: int = Field(ge=1)
    closed: bool


class ReadInput(Input):
    revision: int = Field(ge=1)


def migrate_questions(conn):
    Question.__table__.create(conn, checkfirst=True)
    QuestionMessage.__table__.create(conn, checkfirst=True)


def install(app, DB, identity, auth, guard, notify):
    def load(db, qid, user):
        q = db.get(Question, qid)
        if not q or not accessible({'owner_id':q.owner_id, 'recipient_id':q.recipient_id}, user):
            fail('not_found', 404)
        return q

    def summary(q, user):
        return {'id':q.id, 'owner_id':q.owner_id, 'recipient_id':q.recipient_id,
                'owner_name':q.owner_name, 'recipient_name':q.recipient_name, 'title':q.title,
                'closed':q.closed, 'revision':q.revision,
                'unread':(q.owner_seen if user['id']==q.owner_id else q.recipient_seen) < q.revision,
                'created_at':q.created_at.isoformat()+'Z', 'updated_at':q.updated_at.isoformat()+'Z'}

    def staff():
        return auth('/internal/question-recipients')

    def recipient(q):
        if not any(u['id'] == q.recipient_id for u in staff()):
            fail('question_recipient_unavailable', 409)

    def send_update(db, q, actor, suffix):
        target = q.recipient_id if actor['id'] == q.owner_id else q.owner_id
        # Neither subject nor message text enters push payloads / notification inbox.
        notify(db, {'id':target}, 'question:'+q.id+':'+suffix, 'questions', {
            'title':'Новое сообщение по вопросу', 'title_en':'New question message',
            'body':'Откройте личную переписку в разделе «Вопросы».',
            'body_en':'Open your private conversation in Questions.',
            'route':'#questions', 'important':False, 'audience':'question',
            'owner_id':q.owner_id, 'recipient_id':q.recipient_id,
        }, now()+timedelta(days=30))

    @app.get('/api/notifications/questions/recipients')
    def recipients(request:Request):
        u = identity(request)
        return [r for r in staff() if r['id'] != u['id']]

    @app.get('/api/notifications/questions')
    def questions(request:Request, offset:int=Query(default=0,ge=0,le=10000), closed:bool=False):
        u = identity(request)
        allowed = Question.owner_id == u['id']
        if u['role'] in STAFF:
            allowed = or_(allowed, Question.recipient_id == u['id'])
        with DB() as db:
            query = select(Question).where(allowed, Question.closed == closed)
            count = db.scalar(select(func.count()).select_from(query.subquery()))
            items = db.scalars(query.order_by(Question.updated_at.desc(), Question.id).offset(offset).limit(20))
            return {'items':[summary(q,u) for q in items], 'total':count}

    @app.post('/api/notifications/questions', status_code=201)
    def create(data:QuestionInput, request:Request):
        u = identity(request)
        request_key = key(request,u)
        fp = fingerprint(data.model_dump())
        r = next((r for r in staff() if r['id']==data.recipient_id and r['id']!=u['id']), None)
        if not r:
            fail('question_recipient_unavailable', 409)
        with DB.begin() as db:
            guard(db)
            old = db.scalar(select(Question).where(Question.request_key==request_key))
            if old:
                if old.fingerprint != fp: fail('idempotency_conflict',409)
                return summary(old,u)
            count = db.scalar(select(func.count()).select_from(Question).where(
                Question.owner_id==u['id'], Question.closed==False))
            recent = db.scalar(select(func.count()).select_from(Question).where(
                Question.owner_id==u['id'], Question.created_at>now()-timedelta(days=1)))
            if count >= 5: fail('question_open_limit',429)
            if recent >= 20: fail('too_many_attempts',429)
            q = Question(owner_id=u['id'], recipient_id=r['id'], owner_name=u['name'],
                recipient_name=r['name'], title=data.title, request_key=request_key, fingerprint=fp)
            db.add(q); db.flush()
            msg = QuestionMessage(question_id=q.id, author_id=u['id'], author_name=u['name'],
                body=data.body, request_key=request_key, fingerprint=fp)
            db.add(msg); db.flush()
            send_update(db,q,u,msg.id)
            db.add(Audit(actor=u['name'],action='question_created',target=q.id))
            return summary(q,u)

    @app.get('/api/notifications/questions/{qid}')
    def detail(qid:str, request:Request):
        u = identity(request)
        with DB() as db:
            q = load(db,qid,u)
            return {**summary(q,u), 'messages':[
                {'id':m.id,'author_id':m.author_id,'author_name':m.author_name,'body':m.body,
                 'created_at':m.created_at.isoformat()+'Z'}
                for m in db.scalars(select(QuestionMessage).where(QuestionMessage.question_id==q.id)
                                   .order_by(QuestionMessage.created_at,QuestionMessage.id))]}

    @app.post('/api/notifications/questions/{qid}/messages', status_code=201)
    def reply(qid:str, data:MessageInput, request:Request):
        u = identity(request)
        request_key = key(request,u)
        fp = fingerprint({'qid':qid, **data.model_dump()})
        with DB() as db:
            q = load(db,qid,u)
            recipient(q)
        with DB.begin() as db:
            guard(db)
            q = load(db,qid,u)
            old = db.scalar(select(QuestionMessage).where(QuestionMessage.request_key==request_key))
            if old:
                if old.fingerprint != fp: fail('idempotency_conflict',409)
                return {'id':old.id}
            if q.closed: fail('question_closed',409)
            count = db.scalar(select(func.count()).select_from(QuestionMessage).where(QuestionMessage.question_id==qid))
            if count >= 200: fail('question_message_limit',409)
            recent = db.scalar(select(func.count()).select_from(QuestionMessage).where(
                QuestionMessage.author_id==u['id'],QuestionMessage.created_at>now()-timedelta(minutes=10)))
            if recent >= 30: fail('too_many_attempts',429)
            m = QuestionMessage(question_id=qid,author_id=u['id'],author_name=u['name'],
                                body=data.body,request_key=request_key,fingerprint=fp)
            db.add(m); q.revision += 1; q.updated_at = now()
            if u['id']==q.owner_id: q.owner_seen=q.revision
            else: q.recipient_seen=q.revision
            db.flush(); send_update(db,q,u,m.id)
            return {'id':m.id}

    @app.post('/api/notifications/questions/{qid}/read')
    def read_question(qid:str, data:ReadInput, request:Request):
        u = identity(request)
        with DB.begin() as db:
            guard(db)
            q=load(db,qid,u)
            seen=min(data.revision,q.revision)
            if u['id']==q.owner_id: q.owner_seen=max(q.owner_seen,seen)
            else: q.recipient_seen=max(q.recipient_seen,seen)
        return {'ok':True}

    @app.patch('/api/notifications/questions/{qid}')
    def state(qid:str, data:StateInput, request:Request):
        u = identity(request)
        if not data.closed:
            with DB() as db: recipient(load(db,qid,u))
        with DB.begin() as db:
            guard(db)
            q=load(db,qid,u)
            if q.revision != data.revision: fail('revision_conflict',409)
            if q.closed != data.closed:
                if not data.closed and db.scalar(select(func.count()).select_from(Question).where(
                    Question.owner_id==q.owner_id,Question.closed==False)) >= 5:
                    fail('question_open_limit',429)
                q.closed=data.closed; q.revision+=1; q.updated_at=now()
                if u['id']==q.owner_id: q.owner_seen=q.revision
                else: q.recipient_seen=q.revision
                db.add(Audit(actor=u['name'],action='question_closed' if q.closed else 'question_reopened',target=q.id))
            return summary(q,u)
