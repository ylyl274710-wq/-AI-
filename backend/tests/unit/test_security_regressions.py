"""Security boundaries without live infrastructure."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import jwt
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.core.config import Settings, settings
from app.core.security import decode_token, hash_password, verify_password
from app.schemas import RegisterIn


def test_password_byte_boundary():
    password = "密" * 24
    hashed = hash_password(password)
    assert verify_password(password, hashed)
    assert not verify_password(password + "x", hashed)
    with pytest.raises(ValueError):
        hash_password(password + "x")
    with pytest.raises(ValidationError):
        RegisterIn(username="farmer", password=password + "x")


def test_token_requires_expiration():
    token = jwt.encode({"sub": "1", "iat": 1}, settings.jwt_secret,
                       algorithm=settings.jwt_algorithm)
    with pytest.raises(HTTPException) as exc:
        decode_token(token)
    assert exc.value.status_code == 401


def test_production_rejects_insecure_defaults():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, env="production", debug=False)
    config = Settings(_env_file=None, env="production", debug=False,
                      jwt_secret="a" * 32, cors_origins="https://example.com")
    assert config.env == "production"


def test_foreign_field_rejected_before_agent():
    from app.api.v1.diagnosis import _validate_field
    db = Mock()
    db.scalar.return_value = None
    with pytest.raises(HTTPException) as exc:
        _validate_field(db, SimpleNamespace(id=1), 99)
    assert exc.value.status_code == 404
    statement = str(db.scalar.call_args.args[0])
    assert "farms.owner_id" in statement
    assert "farms.deleted_at IS NULL" in statement


def test_upload_read_is_bounded():
    from app.api.v1.diagnosis import _read_images, MAX_IMAGE_BYTES
    upload = SimpleNamespace(filename="photo.jpg", read=AsyncMock(return_value=b"x"))
    assert asyncio.run(_read_images([upload])) == [b"x"]
    upload.read.assert_awaited_once_with(MAX_IMAGE_BYTES + 1)


def test_tool_checks_field_owner():
    from app.agent.tool_impl import load_farm_context
    db = Mock()
    db.scalar.return_value = None
    ctx = SimpleNamespace(user_id=1, field_id=99)
    with pytest.raises(ValueError):
        asyncio.run(load_farm_context(ctx=ctx, db=db))
    db.get.assert_not_called()
    assert "farms.owner_id" in str(db.scalar.call_args.args[0])
