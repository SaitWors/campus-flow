"""Regression checks for bot manager authorization and input constraints."""
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as DBSession

from services.auth.bots import valid_image, is_owner
from services.auth.models import Base, Audit, User

GOOD = 'ghcr.io/saitwors/test-telegram-bot@sha256:' + 'a' * 64


def test_registry_image_must_be_owned_and_digest_pinned():
    assert valid_image(GOOD) == GOOD
    for bad in [
        'ghcr.io/saitwors/test-telegram-bot:latest',
        'ghcr.io/anybody/test-telegram-bot@sha256:' + 'a' * 64,
        'docker.io/saitwors/test-telegram-bot@sha256:' + 'a' * 64,
        'ghcr.io/saitwors/test@sha256:' + 'g' * 64,
        'ghcr.io/saitwors/test@sha256:' + 'a' * 64 + ' extra',
    ]:
        with pytest.raises(HTTPException) as error:
            valid_image(bad)
        assert error.value.status_code == 422


def test_only_original_setup_admin_is_bot_owner():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with DBSession(engine) as db:
        first = User(email='original@example.com', name='Original',
                     password_hash='hash', role='admin', status='active')
        second = User(email='other@example.com', name='Other',
                      password_hash='hash', role='admin', status='active')
        db.add_all([first, second])
        db.flush()
        db.add(Audit(actor=first.name, action='setup', target=first.id))
        db.commit()
        assert is_owner(db, first)
        assert not is_owner(db, second)
        first.role = 'student'
        db.flush()
        assert not is_owner(db, first)
