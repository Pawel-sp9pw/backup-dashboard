#!/usr/bin/env python3
"""PC MED: conservative, per-client backup retention.

Runs in preview mode unless PCMED_RETENTION_EXECUTE=1.
Old files are moved to a same-filesystem quarantine. Final purge is
disabled unless PCMED_RETENTION_PURGE=1 and quarantine age exceeds grace.
"""
import json
import os
import re
import shutil
import stat
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(os.environ.get("PCMED_RETENTION_ROOT", "/home"))
QUARANTINE = Path(os.environ.get("PCMED_RETENTION_QUARANTINE", "/home/.pcmed-retention-quarantine"))
API_URL = os.environ.get("PCMED_RETENTION_API", "http://127.0.0.1:8080/api/clients/backup")
STATUS_URL = os.environ.get("PCMED_RETENTION_STATUS_API", "http://127.0.0.1:8080/api/status")
TOKEN = os.environ.get("BACKUP_PANEL_TOKEN", "")
EXECUTE = os.environ.get("PCMED_RETENTION_EXECUTE") == "1"
PURGE = os.environ.get("PCMED_RETENTION_PURGE") == "1"
GRACE_DAYS = int(os.environ.get("PCMED_RETENTION_GRACE_DAYS", "14"))
MIN_AGE_HOURS = max(48, int(os.environ.get("PCMED_RETENTION_MIN_AGE_HOURS", "48")))
MAX_MOVES = max(1, int(os.environ.get("PCMED_RETENTION_MAX_MOVES", "20")))
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
DB2_RE = re.compile(r"(db2|mediqus|dbpart\d+)", re.I)
FOLDER_RE = re.compile(r"^(kopie(?:[ _-]|$)|backup(?:[ _-]|$))", re.I)
PART_RE = re.compile(r"^(.*\.(?:dbpart\d+)\.[^.]+)\.(\d{3})$", re.I)
LOG_FILE = os.environ.get("PCMED_RETENTION_LOG", "/var/log/pcmed-retention.jsonl")


def emit(event, **data):
    row = {"at": datetime.now(timezone.utc).isoformat(), "event": event, **data}
    message = json.dumps(row, ensure_ascii=False, sort_keys=True)
    print(message, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(message + "\n")
    except OSError:
        pass


def get_json(url, authenticated=False):
    if authenticated and (not TOKEN or TOKEN == "CHANGE_ME_PANEL_TOKEN"):
        raise RuntimeError("BACKUP_PANEL_TOKEN is missing")
    headers = {"X-Backup-Panel-Token": TOKEN} if authenticated else {}
    req = Request(url, headers=headers)
    with urlopen(req, timeout=15) as response:
        return json.load(response)


def safe_name(name):
    return bool(NAME_RE.fullmatch(str(name))) and str(name) not in {".", ".."}


def checked_int(value):
    if value in (None, ""):
        return None
    num = int(value)
    if not 1 <= num <= 100000:
        raise ValueError("retention out of range")
    return num


def validate_roots():
    if not ROOT.is_dir() or ROOT.is_symlink():
        raise RuntimeError("Invalid backup root")
    if QUARANTINE.is_symlink() or QUARANTINE.parent.resolve() != ROOT.resolve():
        raise RuntimeError("Quarantine must be an immediate child of backup root")
    if ROOT.resolve() == Path("/"):
        raise RuntimeError("Filesystem root cannot be backup root")


def scan_tree(path):
    """Latest mtime and size for all regular files, refusing links and unusual entries."""
    latest = 0.0
    count = 0
    for base, dirs, files in os.walk(path, followlinks=False):
        for name in dirs + files:
            full = Path(base) / name
            mode = full.lstat().st_mode
            if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise ValueError(f"Unsafe entry: {full}")
        for name in files:
            item = Path(base) / name
            info = item.stat(follow_symlinks=False)
            latest = max(latest, info.st_mtime)
            count += 1
    if count == 0:
        raise ValueError("Empty backup directory")
    return latest


def backup_sets(client_dir, db2_type):
    """Only top-level backup directories and direct DB2 files.

    Non-DB2 nested loose files and unknown file structures are never touched.
    Each DB2 multi-part family is treated as a single backup.
    """
    groups = {}
    for entry in client_dir.iterdir():
        if entry.is_symlink():
            emit("skip_link", path=str(entry))
            continue
        if entry.is_dir() and FOLDER_RE.match(entry.name):
            try:
                mtime = scan_tree(entry)
                groups["dir:" + entry.name] = {"paths": [entry], "mtime": mtime}
            except (OSError, ValueError) as exc:
                emit("skip_unsafe_directory", path=str(entry), reason=str(exc))
        elif entry.is_file() and db2_type in ("linux", "windows") and DB2_RE.search(entry.name):
            # DB2 backup images may be split into .001, .002, etc.
            match = PART_RE.match(entry.name)
            family = match.group(1) if match else entry.name
            key = "db2:" + family
            info = entry.stat(follow_symlinks=False)
            group = groups.setdefault(key, {"paths": [], "mtime": 0.0})
            group["paths"].append(entry)
            group["mtime"] = max(group["mtime"], info.st_mtime)
    return sorted(groups.values(), key=lambda x: x["mtime"], reverse=True)


def safe_to_move(status, db2_type):
    if status.get("freshness_status") != "OK":
        return False
    if status.get("overall_status") != "OK":
        return False
    if db2_type == "linux" and status.get("db2_linux_status") not in ("OK", "CACHED_OK"):
        return False
    if db2_type == "windows" and status.get("db2_windows_status") not in ("OK", "CACHED_OK"):
        return False
    return True


def quarantine_group(client, group, policy, now_ts):
    client_q = QUARANTINE / client
    client_q.mkdir(mode=0o700, parents=True, exist_ok=True)
    ident = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:12]
    dest = client_q / ident
    dest.mkdir(mode=0o700)
    moved = []
    try:
        for entry in group["paths"]:
            if entry.is_symlink() or not entry.exists() or entry.parent != ROOT / client:
                raise RuntimeError("Candidate changed before move")
            target = dest / entry.name
            os.rename(entry, target)
            moved.append((entry, target))
        manifest = {
            "client": client,
            "created_at": now_ts,
            "retention_count": policy["count"],
            "retention_days": policy["days"],
            "paths": [name for name in (p.name for p in group["paths"])],
        }
        (dest / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        (dest / ".ready").touch()
        emit("quarantined", client=client, paths=manifest["paths"], quarantine=str(dest))
    except Exception:
        for src, target in reversed(moved):
            if target.exists() and not src.exists():
                os.rename(target, src)
        if not any(dest.iterdir()):
            dest.rmdir()
        raise


def perform_retention(client, cfg, status, now_ts, remaining):
    if not safe_name(client):
        emit("skip_invalid_client", client=str(client))
        return remaining
    client_dir = ROOT / client
    if client_dir.is_symlink() or not client_dir.is_dir():
        emit("skip_missing_or_linked", client=client)
        return remaining
    if client_dir.resolve().parent != ROOT.resolve():
        emit("skip_path_escape", client=client)
        return remaining
    if int(cfg.get("active") or 0) != 1 or int(cfg.get("backup_enabled") or 0) != 1:
        return remaining
    try:
        count = checked_int(cfg.get("retention_count"))
        days = checked_int(cfg.get("retention_days"))
    except (ValueError, TypeError) as exc:
        emit("skip_policy", client=client, error=str(exc))
        return remaining
    if count is None and days is None:
        emit("skip_no_policy", client=client)
        return remaining
    db2_type = str(cfg.get("db2_type") or "none").lower()
    if not safe_to_move(status, db2_type):
        emit("skip_unhealthy", client=client, status=status.get("overall_status"))
        return remaining

    groups = backup_sets(client_dir, db2_type)
    if len(groups) < 3:
        emit("skip_few_backup_sets", client=client, groups=len(groups))
        return remaining
    keep = max(2, count or 2)
    policy = {"count": count, "days": days}
    for idx, group in enumerate(groups):
        if idx < keep or len(groups) - idx <= 0:
            continue
        age_hours = (now_ts - group["mtime"]) / 3600
        if age_hours < MIN_AGE_HOURS:
            continue
        if days is not None and age_hours < days * 24:
            continue
        names = [p.name for p in group["paths"]]
        emit("candidate", client=client, paths=names, age_days=round(age_hours / 24, 1),
             retention_count=count, retention_days=days, action="quarantine" if EXECUTE else "dry_run")
        if EXECUTE and remaining > 0:
            quarantine_group(client, group, policy, now_ts)
            remaining -= 1
    return remaining


def purge_quarantine(now_ts):
    if not QUARANTINE.exists():
        return
    if not EXECUTE or not PURGE:
        emit("purge_disabled")
        return
    for client_dir in QUARANTINE.iterdir():
        if client_dir.is_symlink() or not client_dir.is_dir() or not safe_name(client_dir.name):
            continue
        for entry in client_dir.iterdir():
            if entry.is_symlink() or not entry.is_dir():
                continue
            marker = entry / ".ready"
            manifest = entry / "manifest.json"
            if not marker.is_file() or not manifest.is_file():
                continue
            if now_ts - marker.stat().st_mtime < GRACE_DAYS * 86400:
                continue
            info = json.loads(manifest.read_text(encoding="utf-8"))
            if info.get("client") != client_dir.name:
                emit("skip_bad_manifest", path=str(entry))
                continue
            shutil.rmtree(entry)
            emit("purged", client=client_dir.name, path=str(entry))


def main():
    validate_roots()
    if EXECUTE and os.geteuid() != 0:
        raise RuntimeError("Execution requires root to preserve ownership and permissions")
    if GRACE_DAYS < 7:
        raise RuntimeError("Grace period must be at least 7 days")
    emit("start", execute=EXECUTE, purge=PURGE, root=str(ROOT), grace_days=GRACE_DAYS)
    data = get_json(API_URL, authenticated=True)
    statuses = get_json(STATUS_URL)
    status_by_client = {row["client"]: row for row in statuses.get("clients", [])}
    remaining = MAX_MOVES
    now_ts = datetime.now(timezone.utc).timestamp()
    for cfg in data.get("items", []):
        client = cfg.get("client", "")
        remaining = perform_retention(client, cfg, status_by_client.get(client, {}), now_ts, remaining)
        if remaining == 0:
            emit("move_limit_reached", max_moves=MAX_MOVES)
            break
    purge_quarantine(now_ts)
    emit("finish", moves=MAX_MOVES - remaining)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        emit("fatal", message=str(exc))
        sys.exit(1)
