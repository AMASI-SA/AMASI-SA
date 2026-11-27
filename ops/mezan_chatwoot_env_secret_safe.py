#!/usr/bin/env python3
"""Safely append the existing Chatwoot agentbot token to local backend/.env.
Standard-library only; no secrets in stdout, args, Git, history, or logs.
No restart, deployment, or modification to existing keys.
"""
from __future__ import annotations

import getpass
import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile
import uuid

APP_ENV = Path("/app/backend/.env")
BACKUP_DIR = Path("/root/mezan-secret-backups")
SECRET_NAME = "AMASI_CHATWOOT_BOT_TOKEN"
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{24,256}\Z", flags=re.ASCII)
ASSIGNMENT_PATTERN = re.compile(
    r"^[ \t]*(?:export[ \t]+)?([A-Za-z_][A-Za-z_0-9]*)[ \t]*=", re.ASCII
)
CONTROL_FLAGS = ("AMASI_WHATSAPP_CATALOG_ENABLED", "AMASI_WHATSAPP_ORDER_STAGE_ENABLED")


def refuse(message: str) -> None:
    raise RuntimeError("CHATWOOT_ENV_STOP: " + message)


def secure_env_file(path: Path) -> os.stat_result:
    try:
        info = path.lstat()
    except FileNotFoundError:
        refuse("backend/.env is missing; refusing to replace it")
    if not stat.S_ISREG(info.st_mode) or path.is_symlink():
        refuse("backend/.env is not a regular, non-symlink file")
    if stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
        refuse("backend/.env must be mode 0600 with one hardlink")
    if info.st_size > 8 * 1024 * 1024:
        refuse("backend/.env is unexpectedly large")
    return info


def check_existing(raw: bytes) -> None:
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeError:
        refuse("backend/.env is not UTF-8")
    for line in lines:
        found = ASSIGNMENT_PATTERN.match(line)
        if not found:
            continue
        name = found.group(1)
        if name == SECRET_NAME:
            refuse("token already configured; refusing to overwrite or duplicate")
        if name in CONTROL_FLAGS:
            value = line[found.end():].strip()
            if value not in ("0", "'0'", '"0"'):
                refuse("WhatsApp feature flag is not OFF: " + name)
    for flag in CONTROL_FLAGS:
        if os.environ.get(flag) not in (None, "", "0"):
            refuse("live WhatsApp feature flag is not OFF: " + flag)


def backup_location() -> Path:
    if not BACKUP_DIR.exists():
        BACKUP_DIR.mkdir(mode=0o700, parents=False)
    info = BACKUP_DIR.lstat()
    if (not stat.S_ISDIR(info.st_mode) or BACKUP_DIR.is_symlink()
            or stat.S_IMODE(info.st_mode) & 0o077 or info.st_uid != os.geteuid()):
        refuse("backup directory not privately owned")
    return BACKUP_DIR / ("backend-env-" + uuid.uuid4().hex + ".bak")


def write_private_backup(destination: Path, data: bytes) -> None:
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(destination, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    if destination.read_bytes() != data:
        refuse("backup failed verification")


def perform() -> None:
    if os.geteuid() != 0:
        refuse("run from the authorized root Emergent terminal")
    if APP_ENV.parent.is_symlink() or not APP_ENV.parent.is_dir():
        refuse("backend directory missing or unsafe")
    before = secure_env_file(APP_ENV)
    original = APP_ENV.read_bytes()
    digest = hashlib.sha256(original).digest()
    check_existing(original)
    try:
        with open("/dev/tty", "rb", buffering=0):
            pass
    except OSError:
        refuse("no interactive terminal for hidden input")
    token = getpass.getpass("Paste Chatwoot token (hidden, then Enter): ")
    if TOKEN_PATTERN.fullmatch(token) is None:
        refuse("token format invalid; 24-256 ASCII letters/digits/_/- required")
    now = secure_env_file(APP_ENV)
    if (now.st_ino != before.st_ino or now.st_dev != before.st_dev
            or hashlib.sha256(APP_ENV.read_bytes()).digest() != digest):
        refuse("backend/.env changed while entering token; no write performed")
    backup = backup_location()
    write_private_backup(backup, original)
    separator = b"" if not original or original.endswith(b"\n") else b"\n"
    content = original + separator + SECRET_NAME.encode() + b"=" + token.encode("ascii") + b"\n"
    fd, temporary = tempfile.mkstemp(prefix=".env.chatwoot-pending-", dir=APP_ENV.parent)
    try:
        os.fchmod(fd, 0o600)
        os.fchown(fd, before.st_uid, before.st_gid)
        with os.fdopen(fd, "wb") as out:
            out.write(content)
            out.flush()
            os.fsync(out.fileno())
        now = secure_env_file(APP_ENV)
        if (now.st_ino != before.st_ino or now.st_dev != before.st_dev
                or hashlib.sha256(APP_ENV.read_bytes()).digest() != digest):
            refuse("backend/.env changed before atomic replace")
        os.replace(temporary, APP_ENV)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    if APP_ENV.read_bytes() != content or stat.S_IMODE(APP_ENV.stat().st_mode) != 0o600:
        refuse("post-write verification failed; recover using private backup")
    print("CHATWOOT_TOKEN_SAVED_SUCCESS")
    print("BACKUP_SAVED_PRIVATE_ONLY")
    print("NO_SECRET_PRINTED")
    print("NO_SERVICE_RESTART_NO_DEPLOY")
    print("NOTE_LOCAL_DOTENV_MAY_NOT_PERSIST_AFTER_REPUBLISH")


if __name__ == "__main__":
    try:
        perform()
    except (RuntimeError, OSError) as error:
        print(str(error))
        raise SystemExit(2) from None
