import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.errors import AppError, app_error_handler


def test_error_shape():
    app = FastAPI()
    app.add_exception_handler(AppError, app_error_handler)

    @app.get("/x")
    def x():
        raise AppError("QUESTION_REGEN_LIMIT", "하루 1회", {"limit": 1})

    r = TestClient(app).get("/x")
    assert r.status_code == 429
    assert r.json() == {"error": {"code": "QUESTION_REGEN_LIMIT", "message": "하루 1회", "details": {"limit": 1}}}


def test_unknown_code_rejected():
    with pytest.raises(AssertionError):
        AppError("NOPE")
