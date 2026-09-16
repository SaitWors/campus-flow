"""TOTP enrollment, single-use login challenges, recovery and device sessions."""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from datetime import timedelta
from urllib.parse import quote
from argon2.exceptions import VerificationError
from cryptography.fernet import Fernet
from fastapi import Request, Response
from pydantic import Field
from sqlalchemy import select, delete, inspect, text
from services.common.core import Input, now, uid, digest, fail, service_secret
from services.auth.models import TwoFactor, LoginChallenge, Session, User


def migrate_security(conn):
    TwoFactor.__table__.create(conn, checkfirst=True)
    LoginChallenge.__table__.create(conn, checkfirst=True)
    Session.__table__.create(conn, checkfirst=True)
    columns={c['name'] for c in inspect(conn).get_columns('sessions')}
    for name, definition in [('public_id','VARCHAR(36)'),('created_at','TIMESTAMP'),('device',"VARCHAR(200) NOT NULL DEFAULT ''")]:
        if name not in columns:
            conn.execute(text(f'ALTER TABLE sessions ADD COLUMN {name} {definition}'))
    for token in conn.execute(text('SELECT token_hash FROM sessions WHERE public_id IS NULL')).scalars():
        conn.execute(text('UPDATE sessions SET public_id=:id, created_at=:at WHERE token_hash=:token'),
                     {'id':uid(),'at':now(),'token':token})
    conn.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS ix_sessions_public_id ON sessions (public_id)'))


def cipher():
    key=hashlib.sha256(('campus-flow:totp:v1:'+service_secret()).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def totp(secret, step, digits=6):
    key=base64.b32decode(secret+'='*((-len(secret))%8))
    value=hmac.new(key,struct.pack('>Q',step),hashlib.sha1).digest()
    offset=value[-1]&15
    number=(struct.unpack('>I',value[offset:offset+4])[0]&0x7fffffff)%(10**digits)
    return str(number).zfill(digits)


def consume_code(factor, code):
    value=code.replace(' ','').replace('-','').upper()
    if len(value)==6 and value.isascii() and value.isdigit():
        step=int(time.time())//30
        secret=cipher().decrypt(factor.secret.encode()).decode()
        for candidate in (step,step-1,step+1):
            if candidate>factor.last_step and hmac.compare_digest(totp(secret,candidate),value):
                factor.last_step=candidate
                return True
    hashed=digest(factor.user_id+':'+value)
    for saved in factor.recovery_hashes:
        if hmac.compare_digest(saved,hashed):
            factor.recovery_hashes=[h for h in factor.recovery_hashes if h!=saved]
            return True
    return False


def recovery_codes(factor):
    codes=[secrets.token_hex(8).upper() for _ in range(10)]
    factor.recovery_hashes=[digest(factor.user_id+':'+code) for code in codes]
    return [code[:8]+'-'+code[8:] for code in codes]


def make_challenge(db, user):
    factor=db.get(TwoFactor,user.id)
    if not factor or not factor.enabled:
        return None
    db.execute(delete(LoginChallenge).where(LoginChallenge.expires<now()))
    # Password holders may restart login, but outstanding challenges stay bounded.
    active=db.scalars(select(LoginChallenge).where(LoginChallenge.user_id==user.id).order_by(LoginChallenge.expires.desc())).all()
    for old in active[4:]:db.delete(old)
    token=secrets.token_urlsafe(32)
    db.add(LoginChallenge(token_hash=digest(token),user_id=user.id,expires=now()+timedelta(minutes=5)))
    return {'mfa_required':True,'challenge':token}


class Proof(Input):
    password:str=Field(min_length=1,max_length=128)
    code:str=Field(default='',max_length=40)


class Code(Input):
    code:str=Field(min_length=6,max_length=40)


class Challenge(Code):
    challenge:str=Field(min_length=30,max_length=100)


def install(app,DB,current,hasher,rate,audit,login_response):
    def password_proof(user,password):
        try:hasher.verify(user.password_hash,password)
        except VerificationError:fail('invalid_credentials',401)

    def lock_user(db,user):
        return db.scalar(select(User).where(User.id==user.id).with_for_update().execution_options(populate_existing=True))

    def factor_row(db,user):
        return db.scalar(select(TwoFactor).where(TwoFactor.user_id==user.id).with_for_update())

    def revoke_others(db,user,request):
        db.execute(delete(Session).where(Session.user_id==user.id,Session.token_hash!=digest(request.cookies.get('cf_session',''))))
        db.execute(delete(LoginChallenge).where(LoginChallenge.user_id==user.id))

    @app.post('/api/auth/login/verify')
    def verify_login(data:Challenge,request:Request,response:Response):
        rate(request,'mfa-login',60)
        invalid=False
        with DB.begin() as db:
            # Same lock order as password login and factor management.
            candidate=db.get(LoginChallenge,digest(data.challenge))
            if not candidate:fail('mfa_challenge_expired',401)
            user=db.scalar(select(User).where(User.id==candidate.user_id).with_for_update())
            challenge=db.scalar(select(LoginChallenge).where(LoginChallenge.token_hash==digest(data.challenge)).with_for_update().execution_options(populate_existing=True))
            if not challenge or challenge.expires<now() or challenge.attempts>=5 or not user or user.status!='active':
                fail('mfa_challenge_expired',401)
            factor=factor_row(db,user)
            if not factor or not factor.enabled:fail('mfa_challenge_expired',401)
            challenge.attempts+=1
            if not consume_code(factor,data.code):
                invalid=True
                if challenge.attempts>=5:db.delete(challenge)
            else:
                db.delete(challenge)
                result=login_response(db,user,response,request)
        # Commit failed-attempt counters before returning an error.
        if invalid:fail('mfa_invalid',401)
        return result

    @app.get('/api/auth/security')
    def security_status(request:Request):
        with DB() as db:
            user=current(request,db);factor=db.get(TwoFactor,user.id)
            return {'enabled':bool(factor and factor.enabled),
                    'recovery_remaining':len(factor.recovery_hashes) if factor and factor.enabled else 0}

    @app.post('/api/auth/security/setup')
    def setup(data:Proof,request:Request):
        rate(request,'mfa-management',30)
        with DB.begin() as db:
            user=lock_user(db,current(request,db));password_proof(user,data.password)
            factor=factor_row(db,user)
            if factor and factor.enabled:fail('mfa_already_enabled',409)
            if not factor:factor=TwoFactor(user_id=user.id);db.add(factor)
            secret=base64.b32encode(secrets.token_bytes(20)).decode().rstrip('=')
            factor.secret=cipher().encrypt(secret.encode()).decode()
            factor.pending_until=now()+timedelta(minutes=10)
            factor.last_step=-1;factor.recovery_hashes=[]
            uri='otpauth://totp/'+quote('Campus Flow:'+user.email,safe='')+'?secret='+secret+'&issuer=Campus%20Flow&algorithm=SHA1&digits=6&period=30'
            return {'secret':secret,'uri':uri,'expires_minutes':10}

    @app.post('/api/auth/security/enable')
    def enable(data:Code,request:Request):
        rate(request,'mfa-management',30)
        with DB.begin() as db:
            user=lock_user(db,current(request,db));factor=factor_row(db,user)
            if not factor or factor.enabled or factor.pending_until<now():fail('mfa_setup_expired',409)
            if not consume_code(factor,data.code):fail('mfa_invalid',400)
            factor.enabled=True;codes=recovery_codes(factor)
            revoke_others(db,user,request);audit(db,user.name,'mfa_enabled',user.id)
            return {'recovery_codes':codes}

    @app.post('/api/auth/security/disable')
    def disable(data:Proof,request:Request):
        rate(request,'mfa-management',30)
        with DB.begin() as db:
            user=lock_user(db,current(request,db));password_proof(user,data.password)
            factor=factor_row(db,user)
            if not factor or not factor.enabled:fail('mfa_not_enabled',409)
            if not consume_code(factor,data.code):fail('mfa_invalid',400)
            db.delete(factor);revoke_others(db,user,request)
            audit(db,user.name,'mfa_disabled',user.id)
        return {'ok':True}

    @app.post('/api/auth/security/recovery')
    def regenerate(data:Proof,request:Request):
        rate(request,'mfa-management',30)
        with DB.begin() as db:
            user=lock_user(db,current(request,db));password_proof(user,data.password)
            factor=factor_row(db,user)
            if not factor or not factor.enabled:fail('mfa_not_enabled',409)
            if not consume_code(factor,data.code):fail('mfa_invalid',400)
            codes=recovery_codes(factor);revoke_others(db,user,request)
            audit(db,user.name,'mfa_recovery_regenerated',user.id)
            return {'recovery_codes':codes}

    @app.get('/api/auth/sessions')
    def sessions(request:Request):
        with DB() as db:
            user=current(request,db);token=digest(request.cookies.get('cf_session',''))
            return [{'id':s.public_id,'current':s.token_hash==token,'device':s.device,
                     'created_at':s.created_at.isoformat()+'Z','expires':s.expires.isoformat()+'Z'}
                    for s in db.scalars(select(Session).where(Session.user_id==user.id,Session.expires>now()).order_by(Session.created_at.desc()))]

    @app.delete('/api/auth/sessions/others')
    def end_others(request:Request):
        with DB.begin() as db:
            user=lock_user(db,current(request,db));revoke_others(db,user,request)
            audit(db,user.name,'sessions_revoked',user.id)
        return {'ok':True}

    @app.delete('/api/auth/sessions/{session_id}')
    def end_session(session_id:str,request:Request,response:Response):
        with DB.begin() as db:
            user=lock_user(db,current(request,db))
            session=db.scalar(select(Session).where(Session.public_id==session_id,Session.user_id==user.id))
            if not session:fail('not_found',404)
            if session.token_hash==digest(request.cookies.get('cf_session','')):
                response.delete_cookie('cf_session',path='/')
            db.delete(session);audit(db,user.name,'session_revoked',user.id)
        return {'ok':True}
