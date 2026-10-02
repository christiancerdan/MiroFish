"""Exercise the real Flask boundary without cloud calls or user data."""
import pytest
import io
from flask import jsonify, send_file

from app import create_app
from app.config import Config

KEY = "test-access-key-" + "x" * 48


@pytest.fixture
def client():
    class TestConfig(Config):
        TESTING = True
        MIROFISH_ACCESS_KEY = KEY
        MIROFISH_ALLOWED_ORIGINS = ["http://localhost:3000"]
        SECRET_KEY = None
        ZEP_API_KEY = "private-zep-credential-sentinel"

    app = create_app(TestConfig)

    @app.route("/api/test/private", methods=["GET", "POST"])
    def private():
        return {"success": True}

    @app.route("/api/test/error")
    def error():
        return jsonify(success=False, error="PRIVATE-PROVIDER-SECRET",
                       traceback="PRIVATE-FILESYSTEM-PATH"), 500

    @app.route("/api/test/raise")
    def crash():
        raise RuntimeError("PRIVATE-PROVIDER-SECRET")

    @app.get("/api/test/download")
    def download():
        return send_file(io.BytesIO(b'{"simulation_id":"sim_ok"}'),
                         mimetype="application/json", as_attachment=True,
                         download_name="simulation_config.json")

    @app.get("/api/test/stored-error")
    def stored_error():
        return {"success": True, "data": {"status": "failed", "error": "PRIVATE-PROVIDER-SECRET"}}

    @app.get("/api/test/echo-secret")
    def echo_secret():
        return {"success": True, "data": {"message": "Provider echoed private-zep-credential-sentinel"}}

    return app.test_client()


def login(client):
    response = client.post("/api/auth/login", json={"access_key": KEY})
    assert response.status_code == 200
    return response


@pytest.mark.parametrize("method,path", [
    ("get", "/api/graph/project/list"),
    ("delete", "/api/graph/project/project_test"),
    ("post", "/api/simulation/prepare"),
    ("get", "/api/report/list"),
])
def test_api_denies_anonymous_clients(client, method, path):
    assert getattr(client, method)(path).status_code == 401


def test_unconfigured_server_fails_closed():
    class MissingConfig(Config):
        MIROFISH_ACCESS_KEY = ""
    client = create_app(MissingConfig).test_client()
    assert client.get("/health").status_code == 200
    assert client.get("/api/graph/project/list").status_code == 503
    assert client.get("/api/auth/session").json["data"] == {
        "configured": False, "authenticated": False,
    }


def test_session_cookie_csrf_and_logout(client):
    assert client.get("/api/auth/session").json["data"]["authenticated"] is False
    response = login(client)
    cookie = response.headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
    assert KEY not in cookie
    token = response.json["data"]["csrf_token"]
    assert token and token != KEY
    assert client.get("/api/test/private").status_code == 200
    assert client.post("/api/test/private", json={}).status_code == 403
    assert client.post("/api/test/private", json={}, headers={"X-CSRF-Token": token}).status_code == 200
    assert client.post("/api/auth/logout", headers={"X-CSRF-Token": token}).status_code == 200
    assert client.get("/api/test/private").status_code == 401


def test_bearer_access_and_constant_denial(client):
    assert client.post("/api/test/private", json={}, headers={"Authorization": "Bearer " + KEY}).status_code == 200
    assert client.get("/api/test/private", headers={"Authorization": "Bearer bad"}).status_code == 401


def test_tampered_cookie_and_rotated_key_reject_old_session(client):
    login(client)
    cookie = client.get_cookie("mirofish_session").value
    client.set_cookie("mirofish_session", cookie + "tampered")
    assert client.get("/api/test/private").status_code == 401
    class RotatedConfig(Config):
        MIROFISH_ACCESS_KEY = "different-key-" + "y" * 48
    new_client = create_app(RotatedConfig).test_client()
    new_client.set_cookie("mirofish_session", cookie)
    assert new_client.get("/api/graph/project/list").status_code == 401


def test_foreign_browser_origin_is_blocked(client):
    for origin in ("https://evil.example", "null", "http://localhost:3000.evil.example"):
        response = client.post("/api/auth/login", json={"access_key": KEY}, headers={"Origin": origin})
        assert response.status_code == 403
        assert "Access-Control-Allow-Origin" not in response.headers
    response = client.post("/api/auth/login", json={"access_key": KEY}, headers={"Origin": "http://localhost:3000"})
    assert response.status_code == 200
    assert response.headers["Access-Control-Allow-Origin"] == "http://localhost:3000"


def test_login_is_rate_limited(client):
    for _ in range(5):
        assert client.post("/api/auth/login", json={"access_key": "bad"}).status_code == 401
    assert client.post("/api/auth/login", json={"access_key": KEY}).status_code == 429


def test_allowed_origin_can_preflight_idempotent_job_retry(client):
    response = client.options('/api/graph/task/task_test/retry', headers={
        'Origin': 'http://localhost:3000',
        'Access-Control-Request-Method': 'POST',
        'Access-Control-Request-Headers': 'Idempotency-Key, X-CSRF-Token',
    })
    assert response.status_code == 204
    assert 'Idempotency-Key' in response.headers['Access-Control-Allow-Headers']


@pytest.mark.parametrize("path", ["/api/test/error", "/api/test/raise"])
def test_server_errors_do_not_expose_internals(client, path):
    response = client.get(path, headers={"Authorization": "Bearer " + KEY})
    assert response.status_code == 500
    assert "PRIVATE-" not in response.get_data(as_text=True)
    assert "traceback" not in response.json
    assert response.json["request_id"] == response.headers["X-Request-ID"]


@pytest.mark.parametrize("body", [{"simulation_id": {}}, {"simulation_id": "/tmp/private"},
                                       {"project_id": "../private"}, ["invalid"]])
def test_invalid_request_identifiers_are_client_errors(client, body):
    response = client.post("/api/simulation/prepare", json=body, headers={"Authorization": "Bearer " + KEY})
    assert response.status_code == 400
    assert "traceback" not in response.get_data(as_text=True)


def test_security_headers(client):
    response = client.get("/api/auth/session")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Frame-Options"] == "DENY"


def test_json_download_remains_a_download(client):
    response = client.get("/api/test/download", headers={"Authorization": "Bearer " + KEY})
    assert response.status_code == 200
    assert response.data == b'{"simulation_id":"sim_ok"}'
    assert 'attachment;' in response.headers['Content-Disposition']


def test_stored_failure_errors_do_not_expose_internals(client):
    response = client.get("/api/test/stored-error", headers={"Authorization": "Bearer " + KEY})
    assert response.status_code == 200
    assert response.json["data"]["status"] == "failed"
    assert "PRIVATE-" not in response.get_data(as_text=True)


def test_configured_credentials_are_redacted_from_api_payloads(client):
    response = client.get("/api/test/echo-secret", headers={"Authorization": "Bearer " + KEY})
    assert response.status_code == 200
    assert "private-zep-credential-sentinel" not in response.get_data(as_text=True)
