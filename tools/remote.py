#!/usr/bin/env python3
"""Cliente SSH mínimo para despliegue y verificación en el host Ubuntu real.

Uso:
    python tools/remote.py exec "uname -a"
    python tools/remote.py exec --sudo "apt-get update"
    python tools/remote.py put  <local> <remoto>
    python tools/remote.py push <local-dir> <remoto-dir>     # sincroniza (tar)
    python tools/remote.py get  <remoto> <local>

Credenciales: se leen de .env.remote (raíz del repo, ignorado por git) o de
las variables de entorno TBS_HOST/TBS_USER/TBS_PASS.
Este fichero NUNCA contiene credenciales.
"""

from __future__ import annotations

import argparse
import os
import posixpath
import stat
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# La salida remota puede traer UTF-8 (tildes, símbolos de systemctl): forzar
# UTF-8 en stdout/stderr para que la consola de Windows no reviente.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # pragma: no cover - consolas sin reconfigure
        pass


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def config() -> dict:
    _load_env_file(REPO_ROOT / ".env.remote")
    host = os.environ.get("TBS_HOST")
    user = os.environ.get("TBS_USER")
    password = os.environ.get("TBS_PASS")
    if not (host and user and password):
        sys.exit(
            "Faltan credenciales. Crea .env.remote con TBS_HOST/TBS_USER/TBS_PASS "
            "(ver .env.remote.example)."
        )
    return {
        "host": host,
        "user": user,
        "password": password,
        "port": int(os.environ.get("TBS_PORT", "22")),
    }


def connect():
    import paramiko

    cfg = config()
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=cfg["host"],
        port=cfg["port"],
        username=cfg["user"],
        password=cfg["password"],
        timeout=20,
        banner_timeout=20,
        auth_timeout=20,
        look_for_keys=False,
        allow_agent=False,
    )
    return client


def _sudo_wrap(command: str, password: str) -> str:
    # -S lee la contraseña de stdin; -p '' silencia el prompt.
    escaped = command.replace("'", "'\"'\"'")
    return f"sudo -S -p '' bash -lc '{escaped}'"


def run(client, command: str, use_sudo: bool = False, timeout: int = 900):
    cfg = config()
    if use_sudo:
        command = _sudo_wrap(command, cfg["password"])
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout, get_pty=False)
    if use_sudo:
        stdin.write(cfg["password"] + "\n")
        stdin.flush()
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    code = stdout.channel.recv_exit_status()
    if use_sudo:
        # sudo -S a veces deja el eco del prompt en stderr
        err = "\n".join(l for l in err.splitlines() if "[sudo]" not in l)
    return code, out, err


def sftp_put(client, local: Path, remote: str) -> None:
    sftp = client.open_sftp()
    remote_dir = posixpath.dirname(remote)
    _mkdirs(sftp, remote_dir)
    sftp.put(str(local), remote)
    sftp.close()


def _mkdirs(sftp, path: str) -> None:
    if not path or path == "/":
        return
    parts = path.strip("/").split("/")
    cur = "" if path.startswith("/") else None
    for part in parts:
        cur = f"{cur}/{part}" if cur is not None else part
        try:
            sftp.stat(cur)
        except OSError:
            try:
                sftp.mkdir(cur)
            except OSError:
                pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_exec = sub.add_parser("exec")
    p_exec.add_argument("command")
    p_exec.add_argument("--sudo", action="store_true")
    p_exec.add_argument("--timeout", type=int, default=900)

    p_put = sub.add_parser("put")
    p_put.add_argument("local")
    p_put.add_argument("remote")

    p_get = sub.add_parser("get")
    p_get.add_argument("remote")
    p_get.add_argument("local")

    p_push = sub.add_parser("push")
    p_push.add_argument("local_dir")
    p_push.add_argument("remote_dir")
    p_push.add_argument("--exclude", action="append", default=[])

    args = parser.parse_args()
    client = connect()
    try:
        if args.cmd == "exec":
            code, out, err = run(client, args.command, args.sudo, args.timeout)
            if out:
                sys.stdout.write(out)
            if err:
                sys.stderr.write(err)
            return code
        if args.cmd == "put":
            sftp_put(client, Path(args.local), args.remote)
            print(f"put {args.local} -> {args.remote}")
            return 0
        if args.cmd == "get":
            sftp = client.open_sftp()
            sftp.get(args.remote, args.local)
            sftp.close()
            print(f"get {args.remote} -> {args.local}")
            return 0
        if args.cmd == "push":
            # Sincroniza un directorio completo vía tar+stdin (rápido y sin rsync)
            import io
            import tarfile

            local_dir = Path(args.local_dir)
            excludes = set(args.exclude)
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w:gz") as tar:
                for path in sorted(local_dir.rglob("*")):
                    rel = path.relative_to(local_dir)
                    rel_str = rel.as_posix()
                    if any(rel_str == e or rel_str.startswith(e + "/") for e in excludes):
                        continue
                    if any(part in {".git", "__pycache__", ".venv", "node_modules", ".pytest_cache"} for part in rel.parts):
                        continue
                    tar.add(path, arcname=rel_str, recursive=False)
            buf.seek(0)
            run(client, f"mkdir -p {args.remote_dir}")
            stdin, stdout, stderr = client.exec_command(
                f"tar -xzf - -C {args.remote_dir}", timeout=600
            )
            stdin.write(buf.getvalue())
            stdin.flush()
            stdin.channel.shutdown_write()
            code = stdout.channel.recv_exit_status()
            err = stderr.read().decode("utf-8", "replace")
            if code != 0:
                sys.stderr.write(err)
            print(f"push {args.local_dir} -> {args.remote_dir} (exit {code})")
            return code
    finally:
        client.close()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
