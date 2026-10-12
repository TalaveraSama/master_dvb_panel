"""Tests de regresión de seguridad.

Cubren los hallazgos de la revisión independiente:
  V1 path traversal en `table` de /api/scan/start
  V2 sesiones revocables (logout / cambio de contraseña)
  V3 WebSocket: validación de Origin y código 4401
  V4 X-Forwarded-For sólo con proxy de confianza
  V5 logout exige sesión y CSRF
  V6 sin enumeración de usuarios por tiempo
  V7 sin 500 por ids gigantes
  V9 cabeceras de seguridad
"""

from __future__ import annotations

import time

import pytest


# ---------------------------------------------------------------------------
# V1 — path traversal
# ---------------------------------------------------------------------------
def test_scan_start_rejects_path_traversal(admin_client, tmp_path):
    """`table` no puede leer ficheros fuera del directorio de tablas."""
    secret = tmp_path / "secreto.txt"
    secret.write_text("[CHANNEL]\nDELIVERY_SYSTEM = DVBS2\n", encoding="utf-8")

    for candidate in (
        str(secret),
        "../secreto.txt",
        "/etc/passwd",
        "../../../../etc/shadow",
        "dvb-s/../../../etc/passwd",
    ):
        response = admin_client.post(
            "/api/scan/start",
            json={"adapters": [{"adapter": 0}], "table": candidate},
        )
        # Nunca 200 (no se acepta la tabla) y nunca un error 500
        assert response.status_code in (400, 403, 404), f"{candidate} -> {response.status_code}"
        assert not response.text.startswith("500")


def test_scan_start_rejects_weird_table_names(admin_client):
    for candidate in ("", "   ", "a" * 300, "tabla\x00rara", "con espacios raros"):
        response = admin_client.post(
            "/api/scan/start", json={"adapters": [{"adapter": 0}], "table": candidate}
        )
        assert response.status_code in (400, 403, 404, 422), f"{candidate!r} -> {response.status_code}"


# ---------------------------------------------------------------------------
# V2 — revocación de sesiones
# ---------------------------------------------------------------------------
def test_logout_revokes_session_server_side(admin_client):
    assert admin_client.get("/api/adapters").status_code == 200
    token_cookie = admin_client.cookies.get("tbs_session")
    assert token_cookie

    assert admin_client.post("/api/auth/logout").status_code == 200

    # Aunque el cliente conserve la cookie robada, el servidor la rechaza.
    stolen = admin_client
    stolen.cookies.set("tbs_session", token_cookie)
    assert stolen.get("/api/adapters").status_code == 401
    assert stolen.get("/api/auth/me").json()["authenticated"] is False


def test_password_change_revokes_other_sessions(admin_client):
    old_token = admin_client.cookies.get("tbs_session")
    token = admin_client.get("/api/auth/me").json()["csrf_token"]

    response = admin_client.post(
        "/api/auth/password",
        json={"current_password": "contrasena-segura-123", "new_password": "otra-contrasena-999"},
        headers={"X-CSRF-Token": token},
    )
    assert response.status_code == 200

    # La respuesta emite una sesión nueva (cookie en la propia respuesta)
    new_token = response.cookies.get("tbs_session")
    assert new_token and new_token != old_token

    # El token viejo queda revocado en servidor
    admin_client.cookies.set("tbs_session", old_token)
    admin_client.cookies.set("tbs_csrf", token)
    assert admin_client.get("/api/adapters").status_code == 401

    # Y la sesión nueva de la respuesta sigue siendo válida
    admin_client.cookies.set("tbs_session", new_token)
    admin_client.cookies.set("tbs_csrf", response.cookies.get("tbs_csrf", token))
    assert admin_client.get("/api/auth/me").json()["authenticated"] is True


def test_logout_requires_session_and_csrf(anonymous_client):
    """V5: logout ya no es una mutación pública."""
    assert anonymous_client.post("/api/auth/logout").status_code == 401


def test_logout_requires_csrf_token(admin_client):
    admin_client.headers.pop("X-CSRF-Token", None)
    assert admin_client.post("/api/auth/logout").status_code == 403


# ---------------------------------------------------------------------------
# V3/V8 — WebSocket
# ---------------------------------------------------------------------------
def test_websocket_rejects_foreign_origin(admin_client):
    """Un origen externo no puede abrir el WebSocket (anti-CSWSH)."""
    import pytest as _pytest
    from starlette.websockets import WebSocketDisconnect

    with _pytest.raises(WebSocketDisconnect) as excinfo:
        with admin_client.websocket_connect("/ws/signal", headers={"Origin": "https://evil.example"}) as ws:
            ws.receive_text()
    assert excinfo.value.code == 4403


def test_websocket_rejects_revoked_session(admin_client):
    """V2/N1: el WebSocket no debe aceptar una sesión ya revocada."""
    import pytest as _pytest
    from starlette.websockets import WebSocketDisconnect

    stolen = admin_client.cookies.get("tbs_session")
    assert stolen
    # Logout revoca la época de sesión
    assert admin_client.post("/api/auth/logout").status_code == 200

    # La misma cookie ya no vale por HTTP...
    admin_client.cookies.set("tbs_session", stolen)
    assert admin_client.get("/api/adapters").status_code == 401

    # ...ni por WebSocket
    with _pytest.raises(WebSocketDisconnect) as excinfo:
        with admin_client.websocket_connect("/ws/signal") as ws:
            ws.receive_text()
    assert excinfo.value.code == 4401


def test_websocket_unauthorized_close_code(client):
    """Sin sesión el cierre usa el código de aplicación 4401."""
    import pytest as _pytest
    from starlette.websockets import WebSocketDisconnect

    with _pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect("/ws/signal") as ws:
            ws.receive_text()
    assert excinfo.value.code == 4401


# ---------------------------------------------------------------------------
# V4 — X-Forwarded-For
# ---------------------------------------------------------------------------
def test_forwarded_header_ignored_without_trust(anonymous_client):
    """Sin TBS_TRUST_PROXY_HEADERS la IP del cliente no se puede falsificar."""
    anonymous_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "mal"},
        headers={"X-Forwarded-For": "198.51.100.7"},
    )
    from app.database import get_sessionmaker
    from app.models import LoginAttempt

    session = get_sessionmaker()()
    try:
        last = session.query(LoginAttempt).order_by(LoginAttempt.id.desc()).first()
    finally:
        session.close()
    assert last is not None
    assert last.ip_address != "198.51.100.7"


# ---------------------------------------------------------------------------
# V6 — enumeración de usuarios por tiempo
# ---------------------------------------------------------------------------
def test_no_user_enumeration_by_timing(anonymous_client):
    """El tiempo de respuesta es similar exista o no el usuario."""
    from app.database import get_sessionmaker
    from app.models import LoginAttempt

    def measure(username: str) -> float:
        samples = []
        for _ in range(3):
            start = time.perf_counter()
            anonymous_client.post(
                "/api/auth/login", json={"username": username, "password": "contrasena-mala-xyz"}
            )
            samples.append(time.perf_counter() - start)
        samples.sort()
        return samples[len(samples) // 2]

    existing = measure("admin")
    missing = measure("usuario-que-no-existe-1234")

    # Sin la defensa la diferencia era ~3x; con ella debe quedar por debajo de 3x.
    ratio = max(existing, missing) / max(1e-6, min(existing, missing))
    assert ratio < 3.0, f"diferencia sospechosa: existente={existing:.4f}s inexistente={missing:.4f}s"

    session = get_sessionmaker()()
    try:
        session.query(LoginAttempt).delete()
        session.commit()
    finally:
        session.close()


# ---------------------------------------------------------------------------
# V7 — robustez
# ---------------------------------------------------------------------------
def test_delete_huge_id_does_not_500(admin_client):
    for huge in ("99999999999999999999", "9223372036854775808", "-1", "0"):
        response = admin_client.delete(f"/api/channels/{huge}")
        assert response.status_code in (400, 404, 422), f"{huge} -> {response.status_code}"


def test_patch_huge_id_does_not_500(admin_client):
    """V7: el renombrado con id fuera de int64 debe dar 404, no 500."""
    for huge in (
        "99999999999999999999",
        "9223372036854775808",
        "18446744073709551616",
    ):
        response = admin_client.patch(f"/api/channels/{huge}", json={"name": "X"})
        assert response.status_code == 404, f"PATCH {huge} -> {response.status_code}: {response.text[:120]}"


def test_devices_endpoint_does_not_leak_secrets(admin_client):
    """Errores controlados en escaneo con tabla inexistente."""
    response = admin_client.post(
        "/api/scan/start", json={"adapters": [{"adapter": 0}], "table": "no-existe-esta-tabla"}
    )
    assert response.status_code == 400
    body = response.json()["detail"]
    assert "secret" not in body.lower()
    assert "/home/" not in body


# ---------------------------------------------------------------------------
# Primer arranque: el asistente debe poder usarse si no se define la contraseña
# ---------------------------------------------------------------------------
def test_bootstrap_admin_only_with_env_password(monkeypatch, tmp_path):
    """Sin TBS_ADMIN_PASSWORD no se crea ningún usuario (así aparece el asistente)."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app import models
    from app.config import Settings
    from app.security import bootstrap_admin, user_count

    engine = create_engine(f"sqlite:///{tmp_path / 'boot.db'}", future=True)
    models.Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, future=True)()

    # Caso 1: sin contraseña en el entorno -> no se crea nada
    monkeypatch.delenv("TBS_ADMIN_PASSWORD", raising=False)
    conf = Settings()
    assert conf.admin_password is None
    assert bootstrap_admin(session, conf) is None
    assert user_count(session) == 0

    # Caso 2: con contraseña -> se crea el admin definido
    conf2 = Settings()
    conf2.admin_password = "contrasena-de-entorno-1"
    conf2.admin_user = "jefe"
    user = bootstrap_admin(session, conf2)
    assert user is not None
    assert user.username == "jefe"
    from app.security import verify_password

    assert verify_password("contrasena-de-entorno-1", user.password_hash)

    # Caso 3: ya hay usuarios -> no se toca nada
    assert bootstrap_admin(session, conf2) is None
    session.close()
    engine.dispose()


# ---------------------------------------------------------------------------
# V9 — cabeceras de seguridad
# ---------------------------------------------------------------------------
def test_security_headers(admin_client):
    for path in ("/api/health", "/", "/login"):
        response = admin_client.get(path)
        headers = {key.lower(): value for key, value in response.headers.items()}
        assert headers.get("x-content-type-options") == "nosniff"
        assert headers.get("x-frame-options") == "SAMEORIGIN"
        assert "content-security-policy" in headers
        assert "default-src 'self'" in headers["content-security-policy"]
        assert headers.get("permissions-policy")
        assert headers.get("cross-origin-opener-policy") == "same-origin"


def test_api_responses_are_not_cached(admin_client):
    assert admin_client.get("/api/health").headers.get("cache-control") == "no-store"
