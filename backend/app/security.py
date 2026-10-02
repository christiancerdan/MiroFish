"""Single-owner API access, browser sessions, and a safe HTTP error boundary."""
import hashlib
import hmac
import secrets
import threading
import time
from collections import OrderedDict, deque
from datetime import timedelta

from flask import g, jsonify, request, session
from werkzeug.exceptions import HTTPException

from .utils.storage import validate_storage_id

_IDS = {"project_id", "simulation_id", "report_id", "task_id", "graph_id", "entity_uuid"}
_PUBLIC = {"/api/auth/session", "/api/auth/login"}
_READ_METHODS = {"GET", "HEAD", "OPTIONS"}


def public_error(payload, status):
    """Only use for intentionally sanitized errors, never raw exception text."""
    response = jsonify(payload)
    response.status_code = status
    response._mirofish_public_error = True
    return response


def install_security(app):
    access_key = app.config.get("MIROFISH_ACCESS_KEY") or ""
    configured = isinstance(access_key, str) and len(access_key) >= 32
    private_values = sorted({
        value for name, value in app.config.items()
        if name in {"MIROFISH_ACCESS_KEY", "SECRET_KEY", "LLM_API_KEY", "LLM_BOOST_API_KEY", "OLLAMA_API_KEY", "ZEP_API_KEY"}
        and isinstance(value, str) and len(value) >= 8
    }, key=len, reverse=True)
    # Rotating the access key also invalidates existing sessions. Never use a
    # published default signing secret, even if SECRET_KEY was set separately.
    app.secret_key = hashlib.sha256(
        ("mirofish-session-v1:" + access_key + ":" + (app.config.get("SECRET_KEY") or "")).encode()
    ).digest() if configured else secrets.token_bytes(32)
    app.config.update(
        SESSION_COOKIE_NAME="mirofish_session", SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_SECURE=app.config.get("MIROFISH_COOKIE_SECURE", False),
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        SESSION_REFRESH_EACH_REQUEST=False,
    )
    origins = app.config.get("MIROFISH_ALLOWED_ORIGINS", [])
    if isinstance(origins, str):
        origins = origins.split(",")
    origins = {origin.strip().rstrip("/") for origin in origins if origin.strip()}
    if "*" in origins or "null" in origins:
        raise ValueError("MIROFISH_ALLOWED_ORIGINS must contain explicit origins")
    failures = OrderedDict()
    failure_lock = threading.Lock()

    def error(message, status):
        return public_error({"success": False, "error": message, "request_id": g.request_id}, status)

    def authenticated():
        authorization = request.headers.get("Authorization", "")
        if authorization:
            g.bearer_auth = True
            return authorization.startswith("Bearer ") and hmac.compare_digest(
                authorization[7:].encode(), access_key.encode()
            )
        g.bearer_auth = False
        return session.get("authenticated") is True and bool(session.get("csrf_token"))

    def session_data():
        valid = configured and authenticated()
        data = {"configured": configured, "authenticated": bool(valid)}
        if valid:
            # Programmatic bearer clients do not need CSRF, but preserve the
            # session response contract without disclosing the owner key.
            data["csrf_token"] = session.get("csrf_token") or secrets.token_urlsafe(32)
        return {"success": True, "data": data}

    @app.before_request
    def protect_api():
        g.request_id = secrets.token_hex(12)
        if not request.path.startswith("/api/"):
            return None
        origin = request.headers.get("Origin")
        same_origin = request.host_url.rstrip("/")
        if origin and (origin == "null" or (origin not in origins and origin != same_origin)):
            return error("Origin is not allowed", 403)
        if request.method == "OPTIONS":
            return "", 204
        if request.path not in _PUBLIC:
            if not configured:
                return error("Server access key is not configured", 503)
            if not authenticated():
                return error("Authentication required", 401)
            if request.method not in _READ_METHODS and not g.bearer_auth:
                supplied = request.headers.get("X-CSRF-Token", "")
                expected = session.get("csrf_token", "")
                if not supplied or not hmac.compare_digest(supplied.encode(), expected.encode()):
                    return error("Invalid CSRF token", 403)
        body = request.get_json() if request.is_json else None
        if body is not None and not isinstance(body, dict):
            return error("JSON request body must be an object", 400)
        for source in (request.view_args or {}, request.args, request.form, body or {}):
            for field in _IDS.intersection(source):
                value = source[field]
                if value is None or value == "":
                    continue  # Route-specific required-field checks handle these.
                try:
                    validate_storage_id(value, field)
                except ValueError:
                    return error(f"Invalid {field}", 400)

    @app.get("/api/auth/session")
    def auth_session():
        return session_data()

    @app.post("/api/auth/login")
    def auth_login():
        if not configured:
            return error("Server access key is not configured", 503)
        data = request.get_json(silent=True) or {}
        supplied = data.get("access_key", "")
        ip = request.remote_addr or "unknown"
        now = time.monotonic()
        with failure_lock:
            attempts = failures.setdefault(ip, deque())
            failures.move_to_end(ip)
            while attempts and attempts[0] <= now - 60:
                attempts.popleft()
            while len(failures) > 4096:
                failures.popitem(last=False)
            if len(attempts) >= 5:
                response = error("Too many login attempts; retry in one minute", 429)
                response.headers["Retry-After"] = "60"
                return response
            if not isinstance(supplied, str) or not hmac.compare_digest(supplied.encode(), access_key.encode()):
                attempts.append(now)
                return error("Invalid access key", 401)
            failures.pop(ip, None)
        session.clear()
        session.permanent = True
        session["authenticated"] = True
        session["csrf_token"] = secrets.token_urlsafe(32)
        return session_data()

    @app.post("/api/auth/logout")
    def auth_logout():
        session.clear()
        return {"success": True, "data": {"configured": configured, "authenticated": False}}

    @app.errorhandler(Exception)
    def handle_exception(exc):
        if isinstance(exc, HTTPException):
            # HTTPException descriptions may contain attacker-controlled input.
            return error(exc.name, exc.code or 500)
        app.logger.error("Request %s failed (%s)", g.request_id, type(exc).__name__)
        return error("Internal server error; consult server logs using the request ID", 500)

    @app.after_request
    def safe_response(response):
        request_id = getattr(g, "request_id", secrets.token_hex(12))
        if request.path.startswith("/api/"):
            if response.status_code >= 500 and not getattr(response, "_mirofish_public_error", False):
                status = response.status_code
                response = jsonify(success=False, error="Request failed; consult server logs using the request ID",
                                   request_id=request_id)
                response.status_code = status
            elif response.is_json and not response.direct_passthrough and not response.is_streamed:
                hide_stored_errors = response.status_code < 400
                def strip_tracebacks(value):
                    if isinstance(value, dict):
                        return {
                            key: ("Operation failed; consult server logs" if hide_stored_errors
                                  and key in {"error", "error_message"} and item else strip_tracebacks(item))
                            for key, item in value.items() if key != "traceback"
                        }
                    if isinstance(value, list):
                        return [strip_tracebacks(item) for item in value]
                    if isinstance(value, str):
                        for secret in private_values:
                            value = value.replace(secret, "[redacted]")
                    return value
                response.set_data(app.json.dumps(strip_tracebacks(response.get_json())))
            response.headers["Cache-Control"] = "no-store"
            origin = request.headers.get("Origin")
            if origin and origin != "null" and (origin in origins or origin == request.host_url.rstrip("/")):
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers["Vary"] = "Origin, Cookie"
                response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-CSRF-Token, Accept-Language, Idempotency-Key"
                response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
                response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; font-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        return response
