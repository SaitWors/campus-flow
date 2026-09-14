import hmac
import os
import re
import secrets
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Literal

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import Request, Response
from pydantic import Field
from sqlalchemy import select, delete, func
from sqlalchemy.exc import IntegrityError
from services.common.core import database, migrate, setup_app, Input, now, digest, fail, internal, manager, service_secret
from services.auth.models import Base, User, Session, Invitation, Reset, Audit, Gate

engine, DB = database('auth')
hasher = PasswordHasher()
DUMMY = hasher.hash(secrets.token_urlsafe(32))

@asynccontextmanager
async def lifespan(app):
    service_secret()
    migrate(engine, Base)
    with DB.begin() as db:
        if not db.get(Gate, 'admin-guard'):
            db.add(Gate(id='admin-guard'))
    yield

app = setup_app('Campus Flow · Identity', engine, lifespan)

def user_json(u):
    return {'id': u.id, 'email': u.email, 'name': u.name, 'role': u.role, 'status': u.status, 'subgroup': u.subgroup}

def audit(db, actor, action, target, data=None):
    db.add(Audit(actor=actor, action=action, target=target, data=data or {}))

def rate(request, key, limit=12):
    # Gateway replaces this header; service ports are never published.
    ip = request.headers.get('x-real-ip', request.client.host if request.client else 'local')
    bucket = 'rate:' + digest(f'{key}:{ip}')
    with DB.begin() as db:
        # Serialize creation too. One row; traffic is tiny for one student group.
        db.execute(select(Gate).where(Gate.id == 'admin-guard').with_for_update()).scalar_one()
        gate = db.get(Gate, bucket)
        if not gate:
            gate = Gate(id=bucket, value=0, until=now()+timedelta(minutes=15))
            db.add(gate)
        if gate.until < now():
            gate.value, gate.until = 0, now()+timedelta(minutes=15)
        if gate.value >= limit:
            fail('too_many_attempts', 429)
        gate.value += 1

def credentials(email, password):
    email = email.lower().strip()
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email) or len(email)>254:
        fail('invalid_email', 422)
    if len(password)<12 or len(password)>128:
        fail('password_length', 422)
    return email

def check_session(db, token, csrf='', mutation=False):
    session = db.get(Session, digest(token))
    if not session or session.expires < now():
        fail('unauthorized', 401)
    u = db.get(User, session.user_id)
    if not u or u.status != 'active':
        fail('account_inactive', 403)
    if mutation and not hmac.compare_digest(session.csrf, csrf):
        fail('csrf_invalid', 403)
    return u, session

def current(request, db):
    return check_session(db, request.cookies.get('cf_session',''), request.headers.get('X-CSRF-Token',''), request.method not in ('GET','HEAD','OPTIONS'))[0]

def login_response(db, u, response):
    token, csrf = secrets.token_urlsafe(40), secrets.token_urlsafe(32)
    db.execute(delete(Session).where(Session.expires < now()))
    # Bound sessions per account while preserving parallel devices.
    existing = db.scalars(select(Session).where(Session.user_id == u.id).order_by(Session.expires.desc())).all()
    for s in existing[4:]:
        db.delete(s)
    db.add(Session(token_hash=digest(token), user_id=u.id, csrf=csrf, expires=now()+timedelta(days=7)))
    response.set_cookie('cf_session', token, httponly=True, secure=os.getenv('COOKIE_SECURE','false').lower()=='true', samesite='lax', max_age=604800, path='/')
    return {'user':user_json(u), 'csrf':csrf}

class Login(Input):
    email: str = Field(min_length=3,max_length=254)
    password: str = Field(min_length=1,max_length=128)

class Register(Login):
    name: str = Field(min_length=2,max_length=80)
    subgroup: int = Field(ge=1,le=2,default=1)
    invite: str = Field(min_length=8,max_length=200)

class Bootstrap(Login):
    name: str = Field(min_length=2,max_length=80)
    setup_key: str = Field(min_length=16,max_length=200)

@app.get('/api/auth/status')
def status():
    with DB() as db:
        return {'needs_setup':not bool(db.scalar(select(func.count()).select_from(User))), 'registration':'invite_and_approval'}

@app.post('/api/auth/setup', status_code=201)
def bootstrap(data:Bootstrap,request:Request,response:Response):
    rate(request,'setup',10)
    email = credentials(data.email,data.password)
    expected=os.getenv('SETUP_KEY','')
    if len(expected)<16 or not hmac.compare_digest(expected,data.setup_key):
        fail('setup_key_invalid',403)
    with DB.begin() as db:
        db.execute(select(Gate).where(Gate.id=='admin-guard').with_for_update()).scalar_one()
        if db.scalar(select(func.count()).select_from(User)):
            fail('already_configured',409)
        u=User(email=email,name=data.name,password_hash=hasher.hash(data.password),role='admin',status='active')
        db.add(u);db.flush()
        audit(db,u.name,'setup',u.id)
        return login_response(db,u,response)

@app.post('/api/auth/register',status_code=201)
def register(data:Register,request:Request):
    rate(request,'register',120)
    email=credentials(data.email,data.password)
    with DB.begin() as db:
        inv=db.scalar(select(Invitation).where(Invitation.token_hash==digest(data.invite)).with_for_update())
        if not inv or inv.revoked or inv.expires<now() or inv.uses>=inv.max_uses:
            fail('invite_invalid',403)
        if db.scalar(select(User).where(User.email==email)):
            fail('email_used',409)
        u=User(email=email,name=data.name,password_hash=hasher.hash(data.password),subgroup=data.subgroup)
        db.add(u)
        try:
            db.flush()
        except IntegrityError:
            fail('email_used',409)
        inv.uses+=1
        audit(db,data.name,'registration_requested',u.id)
    return {'status':'pending'}

@app.post('/api/auth/login')
def login(data:Login,request:Request,response:Response):
    rate(request,'login',300)
    rate(request,'login-account:'+digest(data.email.lower().strip()),20)
    with DB.begin() as db:
        u=db.scalar(select(User).where(User.email==data.email.lower().strip()))
        try:
            hasher.verify(u.password_hash if u else DUMMY,data.password)
        except VerificationError:
            fail('invalid_credentials',401)
        if not u:
            fail('invalid_credentials',401)
        if u.status!='active':
            fail('approval_pending' if u.status=='pending' else 'account_inactive',403)
        if hasher.check_needs_rehash(u.password_hash):
            u.password_hash=hasher.hash(data.password)
        return login_response(db,u,response)

@app.get('/api/auth/me')
def me(request:Request):
    with DB() as db:
        u,s=check_session(db,request.cookies.get('cf_session',''))
        return {'user':user_json(u),'csrf':s.csrf}

@app.post('/api/auth/logout')
def logout(request:Request,response:Response):
    with DB.begin() as db:
        current(request,db)
        db.execute(delete(Session).where(Session.token_hash==digest(request.cookies.get('cf_session',''))))
    response.delete_cookie('cf_session',path='/')
    return {'ok':True}

class Verify(Input):
    token:str=Field(max_length=200)
    csrf:str=Field(default='',max_length=200)
    mutation:bool=False

@app.post('/internal/verify')
def verify(data:Verify,request:Request):
    internal(request)
    with DB() as db:
        return user_json(check_session(db,data.token,data.csrf,data.mutation)[0])

@app.get('/internal/users/{user_id}')
def internal_user(user_id:str,request:Request):
    internal(request)
    with DB() as db:
        u=db.get(User,user_id)
        if not u:fail('not_found',404)
        return user_json(u)

@app.get('/api/auth/users')
def users(request:Request):
    with DB() as db:
        manager(user_json(current(request,db)))
        return [user_json(u) for u in db.scalars(select(User).order_by(User.created_at)).all()]

class UpdateUser(Input):
    role:Literal['student','deputy','head','admin']
    status:Literal['active','pending','blocked']
    subgroup:int=Field(ge=1,le=2)

@app.patch('/api/auth/users/{user_id}')
def update_user(user_id:str,data:UpdateUser,request:Request):
    with DB.begin() as db:
        actor=current(request,db);manager(user_json(actor))
        db.execute(select(Gate).where(Gate.id=='admin-guard').with_for_update()).scalar_one()
        u=db.get(User,user_id)
        if not u:fail('not_found',404)
        if actor.role!='admin' and (u.role!='student' or data.role!='student'):
            fail('forbidden',403)
        if u.id==actor.id and (data.role!=u.role or data.status!='active'):
            fail('self_demotion',409)
        if u.role=='admin' and u.status=='active' and (data.role!='admin' or data.status!='active'):
            if db.scalar(select(func.count()).select_from(User).where(User.role=='admin',User.status=='active'))<=1:
                fail('last_admin',409)
        before=user_json(u)
        u.role,u.status,u.subgroup=data.role,data.status,data.subgroup
        if data.status!='active':db.execute(delete(Session).where(Session.user_id==u.id))
        audit(db,actor.name,'user_updated',u.id,{'before':before,'after':user_json(u)})
        return user_json(u)

class InviteInput(Input):
    max_uses:int=Field(default=1,ge=1,le=100)
    days:int=Field(default=7,ge=1,le=30)

@app.post('/api/auth/invitations',status_code=201)
def invite(data:InviteInput,request:Request):
    with DB.begin() as db:
        u=current(request,db);manager(user_json(u))
        token=secrets.token_urlsafe(24)
        inv=Invitation(token_hash=digest(token),expires=now()+timedelta(days=data.days),max_uses=data.max_uses)
        db.add(inv);db.flush();audit(db,u.name,'invite_created',inv.id,{'max_uses':data.max_uses})
        return {'id':inv.id,'token':token,'expires':inv.expires.isoformat()+'Z'}

@app.get('/api/auth/invitations')
def invitations(request:Request):
    with DB() as db:
        manager(user_json(current(request,db)))
        return [{'id':i.id,'expires':i.expires.isoformat()+'Z','uses':i.uses,'max_uses':i.max_uses,'revoked':i.revoked} for i in db.scalars(select(Invitation).order_by(Invitation.expires.desc()).limit(100))]

@app.delete('/api/auth/invitations/{invite_id}')
def revoke(invite_id:str,request:Request):
    with DB.begin() as db:
        u=current(request,db);manager(user_json(u))
        inv=db.get(Invitation,invite_id)
        if not inv:fail('not_found',404)
        inv.revoked=True;audit(db,u.name,'invite_revoked',inv.id)
    return {'ok':True}

class Password(Input):
    current_password:str=Field(min_length=1,max_length=128)
    new_password:str=Field(min_length=12,max_length=128)

@app.post('/api/auth/password')
def password(data:Password,request:Request,response:Response):
    with DB.begin() as db:
        u=current(request,db)
        try:hasher.verify(u.password_hash,data.current_password)
        except VerificationError:fail('invalid_credentials',401)
        u.password_hash=hasher.hash(data.new_password)
        db.execute(delete(Session).where(Session.user_id==u.id))
        audit(db,u.name,'password_changed',u.id)
        return login_response(db,u,response)

@app.post('/api/auth/users/{user_id}/reset')
def create_reset(user_id:str,request:Request):
    with DB.begin() as db:
        actor=current(request,db);manager(user_json(actor))
        u=db.get(User,user_id)
        if not u:fail('not_found',404)
        if actor.role!='admin' and u.role!='student':fail('forbidden',403)
        db.execute(delete(Reset).where(Reset.user_id==user_id))
        token=secrets.token_urlsafe(32)
        db.add(Reset(token_hash=digest(token),user_id=user_id,expires=now()+timedelta(minutes=30)))
        audit(db,actor.name,'password_reset_issued',user_id)
        return {'token':token,'expires_minutes':30}

class ResetInput(Input):
    token:str=Field(min_length=20,max_length=200)
    password:str=Field(min_length=12,max_length=128)

@app.post('/api/auth/reset')
def consume_reset(data:ResetInput,request:Request):
    rate(request,'reset',10)
    with DB.begin() as db:
        item=db.scalar(select(Reset).where(Reset.token_hash==digest(data.token)).with_for_update())
        if not item or item.expires<now():fail('reset_invalid',400)
        u=db.get(User,item.user_id)
        u.password_hash=hasher.hash(data.password)
        db.execute(delete(Session).where(Session.user_id==u.id))
        db.delete(item);audit(db,u.name,'password_reset',u.id)
    return {'ok':True}

@app.get('/api/auth/audit')
def audits(request:Request):
    with DB() as db:
        manager(user_json(current(request,db)))
        return [{'id':a.id,'actor':a.actor,'action':a.action,'target':a.target,'at':a.at.isoformat()+'Z','data':a.data} for a in db.scalars(select(Audit).order_by(Audit.at.desc()).limit(200))]


@app.get('/internal/notification-recipients')
def notification_recipients(request:Request):
    internal(request)
    with DB() as db:
        # No names, emails, passwords or session tokens needed for routing.
        return [{'id':u.id,'role':u.role,'subgroup':u.subgroup}
                for u in db.scalars(select(User).where(User.status=='active'))]

class PushCheck(Input):
    user_id:str=Field(min_length=36,max_length=36)
    session_hash:str=Field(pattern=r'^[0-9a-f]{64}$')

@app.post('/internal/push-check')
def push_check(data:PushCheck,request:Request):
    internal(request)
    with DB() as db:
        session=db.get(Session,data.session_hash)
        user=db.get(User,data.user_id)
        if not session or session.user_id!=data.user_id or session.expires<=now() or not user or user.status!='active':
            return {'active':False}
        return {'active':True,'id':user.id,'role':user.role,'subgroup':user.subgroup}
