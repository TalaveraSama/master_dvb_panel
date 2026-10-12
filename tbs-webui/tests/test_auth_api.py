"""Tests de autenticación, sesión, CSRF y límite de intentos."""

from __future__ import annotations


def test_health_is_public(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["mock"] is True
    assert body["adapters"] >= 1


def test_me_without_session(client):
    response = client.get("/api/auth/me")
    assert response.status_code == 200
    body = response.json()
    assert body["authenticated"] is False
    assert body["csrf_token"] is None
    # El asistente inicial sólo se ofrece cuando no hay ningún usuario
    assert body["must_setup"] is False


def test_me_with_session(admin_client):
    body = admin_client.get("/api/auth/me").json()
    assert body["authenticated"] is True
    assert body["username"] == "admin"
    assert body["csrf_token"]
    assert body["must_setup"] is False


def test_api_requires_session(client):
    """Sin login no se puede acceder a NADA de la API protegida."""
    for method, path in [
        ("get", "/api/adapters"),
        ("get", "/api/status"),
        ("get", "/api/channels"),
        ("get", "/api/transponders"),
        ("get", "/api/settings"),
        ("get", "/api/logs"),
        ("get", "/api/export/m3u"),
        ("get", "/api/scan/status"),
        ("get", "/api/scan/tables"),
        ("post", "/api/tune"),
        ("post", "/api/scan/start"),
        ("post", "/api/scan/stop"),
        ("post", "/api/tune/release"),
    ]:
        if method == "post":
            response = client.post(path, json={})
        else:
            response = client.get(path)
        assert response.status_code == 401, f"{method.upper()} {path} devolvió {response.status_code}"


def test_setup_refuses_when_admin_exists(client):
    """El asistente inicial no puede reutilizarse si ya hay usuarios."""
    response = client.post("/api/auth/setup", json={"username": "otro", "password": "otra-contrasena-123"})
    assert response.status_code == 409
    assert "administrador" in response.json()["detail"].lower()


def test_setup_creates_admin_and_protects_again(anonymous_client):
    """Con la tabla de usuarios vacía, el asistente crea el admin y abre sesión."""
    from sqlalchemy import delete

    from app.database import get_sessionmaker
    from app.models import User

    session = get_sessionmaker()()
    try:
        session.execute(delete(User))
        session.commit()
    finally:
        session.close()

    first = anonymous_client.post(
        "/api/auth/setup", json={"username": "admin", "password": "contrasena-segura-123"}
    )
    assert first.status_code == 200, first.text
    assert first.json()["username"] == "admin"
    assert first.cookies.get("tbs_session")

    me = anonymous_client.get("/api/auth/me").json()
    assert me["authenticated"] is True
    assert me["csrf_token"]

    # Y no se puede ejecutar dos veces
    second = anonymous_client.post(
        "/api/auth/setup", json={"username": "otro", "password": "otra-contrasena-123"}
    )
    assert second.status_code == 409


def test_setup_rejects_short_password(client):
    response = client.post("/api/auth/setup", json={"username": "admin", "password": "corta"})
    assert response.status_code == 422


def test_login_logout_flow(admin_client):
    logout = admin_client.post("/api/auth/logout")
    assert logout.status_code == 200
    assert admin_client.get("/api/auth/me").json()["authenticated"] is False
    assert admin_client.get("/api/adapters").status_code == 401

    login = admin_client.post(
        "/api/auth/login", json={"username": "admin", "password": "contrasena-segura-123"}
    )
    assert login.status_code == 200
    assert admin_client.get("/api/adapters").status_code == 200


def test_login_wrong_password_then_lockout(client):
    client.post("/api/auth/setup", json={"username": "admin", "password": "contrasena-segura-123"})
    client.post("/api/auth/logout")

    for _ in range(4):
        response = client.post("/api/auth/login", json={"username": "admin", "password": "mal"})
        assert response.status_code == 401

    # El 5º fallo (límite por defecto) bloquea
    fifth = client.post("/api/auth/login", json={"username": "admin", "password": "mal"})
    assert fifth.status_code in (401, 423)

    blocked = client.post("/api/auth/login", json={"username": "admin", "password": "mal"})
    assert blocked.status_code == 423
    assert "Retry-After" in blocked.headers

    # Y la contraseña correcta también queda bloqueada mientras dura el bloqueo
    blocked_ok = client.post(
        "/api/auth/login", json={"username": "admin", "password": "contrasena-segura-123"}
    )
    assert blocked_ok.status_code == 423


def test_csrf_required_for_mutations(admin_client):
    # Sin cabecera CSRF -> 403 (se envía explícitamente vacía)
    del admin_client.headers["X-CSRF-Token"]
    response = admin_client.put(
        "/api/settings", json={"default_adapter": 1}, headers={"X-CSRF-Token": ""}
    )
    assert response.status_code == 403
    assert "CSRF" in response.json()["detail"]

    # Con token -> 200
    token = admin_client.get("/api/auth/me").json()["csrf_token"]
    admin_client.headers.update({"X-CSRF-Token": token})
    ok = admin_client.put("/api/settings", json={"default_adapter": 1})
    assert ok.status_code == 200
    assert ok.json()["default_adapter"] == 1


def test_change_password(admin_client):
    wrong = admin_client.post(
        "/api/auth/password",
        json={"current_password": "no-es", "new_password": "nueva-contrasena-456"},
    )
    assert wrong.status_code == 401

    ok = admin_client.post(
        "/api/auth/password",
        json={"current_password": "contrasena-segura-123", "new_password": "nueva-contrasena-456"},
    )
    assert ok.status_code == 200

    admin_client.post("/api/auth/logout")
    assert (
        admin_client.post("/api/auth/login", json={"username": "admin", "password": "contrasena-segura-123"}).status_code
        == 401
    )
    assert (
        admin_client.post("/api/auth/login", json={"username": "admin", "password": "nueva-contrasena-456"}).status_code
        == 200
    )


def test_websocket_requires_session(client):
    import pytest
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect("/ws/signal") as ws:
            ws.receive_text()
    assert excinfo.value.code == 4401


def test_websocket_streams_stats(admin_client):
    with admin_client.websocket_connect("/ws/signal") as ws:
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"

        ws.send_json(
            {
                "type": "tune",
                "adapter": 0,
                "frontend": 0,
                "delivery_system": "DVB-S2",
                "frequency_khz": 11096000,
                "symbol_rate_ksps": 27500,
                "polarization": "V",
            }
        )
        # Primero llega la confirmación de sintonización y luego stats
        types = []
        for _ in range(12):
            message = ws.receive_json()
            types.append(message["type"])
            if message["type"] == "stats":
                stats = message
                break
        assert "tuned" in types
        assert "stats" in types
        assert stats["locked"] is True
        assert stats["signal_percent"] is not None
        assert stats["flags"]["lock"] is True
        assert isinstance(stats["history"], list)

        ws.send_json({"type": "release"})
        # Pueden llegar mensajes de stats ya encolados antes del acuse
        for _ in range(10):
            message = ws.receive_json()
            if message["type"] == "released":
                break
        else:
            raise AssertionError("No se recibió el acuse de liberación")


def test_websocket_rejects_invalid_tune(admin_client):
    with admin_client.websocket_connect("/ws/signal") as ws:
        ws.send_json({"type": "tune", "frequency_khz": 0})
        for _ in range(10):
            message = ws.receive_json()
            if message["type"] == "error":
                assert "inválidos" in message["detail"] or "inválid" in message["detail"]
                return
        raise AssertionError("Se esperaba un mensaje de error")


def test_websocket_rejects_unknown_message(admin_client):
    with admin_client.websocket_connect("/ws/signal") as ws:
        ws.send_json({"type": "no-existe"})
        for _ in range(10):
            message = ws.receive_json()
            if message["type"] == "error":
                assert "no soportado" in message["detail"]
                return
        raise AssertionError("Se esperaba un mensaje de error")


def test_security_headers_and_no_public_docs(client):
    response = client.get("/api/health")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "SAMEORIGIN"
    # No hay documentación pública en un panel privado
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404
