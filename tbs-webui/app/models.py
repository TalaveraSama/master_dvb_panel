"""Modelos de datos (SQLAlchemy 2.0).

Tablas:
    users            usuarios del panel
    login_attempts   registro de intentos fallidos (límite de intentos)
    transponders     mux/transponders sintonizados o escaneados
    services         canales encontrados dentro de un transponder
    settings         ajustes persistentes clave/valor
    scan_jobs        histórico de escaneos
    app_logs         log de la aplicación consultable desde la UI
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    """Fecha/hora UTC sin zona (SQLite no guarda tz)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    """Base declarativa común."""


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Época de sesión: se incrementa al cerrar sesión o cambiar la contraseña.
    # Los tokens de sesión llevan la época con la que se emitieron, de modo que
    # al subirla todas las sesiones anteriores quedan revocadas (server-side).
    session_epoch: Mapped[int] = mapped_column(Integer, default=0, nullable=False, server_default="0")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User {self.username}>"


class LoginAttempt(Base):
    """Intentos de login (se guardan también los correctos para auditar)."""

    __tablename__ = "login_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    attempted_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)


class Transponder(Base):
    """Un mux/transponder: identidad física de la sintonización."""

    __tablename__ = "transponders"
    __table_args__ = (
        UniqueConstraint(
            "delivery_system",
            "frequency_khz",
            "polarization",
            "symbol_rate_ksps",
            "stream_id",
            "plp_id",
            name="uq_transponder_natural_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    delivery_system: Mapped[str] = mapped_column(String(16), nullable=False)
    frequency_khz: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    polarization: Mapped[str | None] = mapped_column(String(4), nullable=True)
    symbol_rate_ksps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fec: Mapped[str | None] = mapped_column(String(16), nullable=True)
    modulation: Mapped[str | None] = mapped_column(String(16), nullable=True)
    bandwidth_mhz: Mapped[int | None] = mapped_column(Integer, nullable=True)
    inversion: Mapped[str] = mapped_column(String(16), default="AUTO", nullable=False)
    rolloff: Mapped[str | None] = mapped_column(String(8), nullable=True)
    pilot: Mapped[str | None] = mapped_column(String(16), nullable=True)
    stream_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    plp_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    guard_interval: Mapped[str | None] = mapped_column(String(16), nullable=True)
    transmission_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    code_rate_hp: Mapped[str | None] = mapped_column(String(16), nullable=True)
    code_rate_lp: Mapped[str | None] = mapped_column(String(16), nullable=True)
    hierarchy: Mapped[str | None] = mapped_column(String(16), nullable=True)
    sat_position: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    lnb_low_khz: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lnb_high_khz: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lnb_tone: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    lnb_voltage: Mapped[str | None] = mapped_column(String(4), nullable=True)
    diseqc: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_signal_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_snr_db: Mapped[float | None] = mapped_column(Float, nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    services: Mapped[list["Service"]] = relationship(
        back_populates="transponder",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    @property
    def label(self) -> str:
        """Etiqueta legible: 11096 H 27500 DVB-S2."""
        parts = [f"{self.frequency_khz / 1000:g} MHz"]
        if self.polarization:
            parts.append(self.polarization)
        if self.symbol_rate_ksps:
            parts.append(f"{self.symbol_rate_ksps} ksps")
        parts.append(self.delivery_system)
        return " ".join(parts)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Transponder {self.delivery_system} {self.frequency_khz}kHz>"


class Service(Base):
    """Un canal/servicio dentro de un transponder.

    La clave anti-duplicados es (transponder_id, service_id).
    """

    __tablename__ = "services"
    __table_args__ = (
        UniqueConstraint("transponder_id", "service_id", name="uq_service_per_transponder"),
        Index("ix_services_name", "name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    transponder_id: Mapped[int] = mapped_column(
        ForeignKey("transponders.id", ondelete="CASCADE"), index=True, nullable=False
    )
    service_id: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    custom_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    service_type: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    service_type_label: Mapped[str] = mapped_column(String(16), default="TV", nullable=False)
    is_fta: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    scrambled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    video_pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    audio_pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pcr_pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pmt_pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    video_codec: Mapped[str | None] = mapped_column(String(48), nullable=True)
    audio_codec: Mapped[str | None] = mapped_column(String(48), nullable=True)
    audio_pids: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    video_pids: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    ca_system_ids: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="scan", nullable=False)  # scan | manual
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    transponder: Mapped[Transponder] = relationship(back_populates="services")

    @property
    def display_name(self) -> str:
        """Nombre personalizado si existe, si no el del flujo."""
        return self.custom_name or self.name or f"Service {self.service_id}"

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Service {self.service_id} {self.display_name!r}>"


class AppSetting(Base):
    """Ajustes persistentes clave/valor editables desde la UI."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class ScanJob(Base):
    """Histórico de trabajos de escaneo."""

    __tablename__ = "scan_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str] = mapped_column(String(36), unique=True, index=True, nullable=False)
    state: Mapped[str] = mapped_column(String(16), default="running", nullable=False)
    adapter: Mapped[int | None] = mapped_column(Integer, nullable=True)
    frontend: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    current: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    services_found: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    transponders_locked: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    tables: Mapped[str | None] = mapped_column(String(128), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AppLog(Base):
    """Log de aplicación consultable desde la UI (nivel >= WARNING por defecto)."""

    __tablename__ = "app_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)
    level: Mapped[str] = mapped_column(String(16), default="INFO", nullable=False)
    source: Mapped[str] = mapped_column(String(32), default="app", nullable=False)
    message: Mapped[str] = mapped_column(Text, default="", nullable=False)


class NewcamdAccount(Base):
    """Cuenta de un servidor Newcamd (card sharing propio o autorizado).

    La contraseña se guarda **cifrada** con una clave derivada del secreto de la
    aplicación; la API nunca la devuelve.
    """

    __tablename__ = "newcamd_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, default=50001, nullable=False)
    username: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    password_enc: Mapped[str] = mapped_column(Text, default="", nullable=False)
    deskey: Mapped[str] = mapped_column(
        String(32), default="0102030405060708091011121314", nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    auto_connect: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<NewcamdAccount {self.name} {self.host}:{self.port}>"
