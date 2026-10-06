#!/usr/bin/env python3
import json
import os
import smtplib
import sqlite3
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, render_template, request, url_for

APP_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("BACKUP_PANEL_DB", APP_DIR / "backup_panel.db"))
API_TOKEN = os.environ.get("BACKUP_PANEL_TOKEN", "CHANGE_ME_PANEL_TOKEN")
STALE_HOURS = int(os.environ.get("BACKUP_PANEL_STALE_HOURS", "30"))
HISTORY_DAYS = int(os.environ.get("BACKUP_PANEL_HISTORY_DAYS", "90"))
CERT_WARN_DAYS = int(os.environ.get("BACKUP_PANEL_CERT_WARN_DAYS", "30"))

SMTP_HOST = os.environ.get("BACKUP_PANEL_SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("BACKUP_PANEL_SMTP_PORT", "587"))
SMTP_USER = os.environ.get("BACKUP_PANEL_SMTP_USER", "")
SMTP_PASS = os.environ.get("BACKUP_PANEL_SMTP_PASS", "")
SMTP_FROM = os.environ.get("BACKUP_PANEL_SMTP_FROM", SMTP_USER or "backup-panel@localhost")
SMTP_TLS = os.environ.get("BACKUP_PANEL_SMTP_TLS", "1") not in ("0", "false", "False", "no")

app = Flask(__name__)

OK = {"OK", "CACHED_OK"}
INFO = {"SKIPPED_LINUX_DB2", "CACHED_SKIPPED_LINUX_DB2", "SKIPPED_WINDOWS_DB2", "CACHED_SKIPPED_WINDOWS_DB2", "SKIPPED_NOT_DB2", "CACHED_SKIPPED_NOT_DB2", "SKIPPED_SYSTEM_DIR", "NOT_APPLICABLE", "CACHED_NOT_APPLICABLE"}
WARN = {"WARNING", "UNKNOWN", "SKIPPED_TOO_NEW", "NO_DB2_BACKUP", "CACHED_NO_DB2_BACKUP"}
ERRORISH = {"ERROR", "BRAK", "OLD", "SKIPPED_NO_BACKUP"}
SYSTEM_CLIENTS = {"db2inst1", "__system__"}
SYSTEM_STATUSES = {"SKIPPED_SYSTEM_DIR"}
NOT_DB2_STATUSES = {"SKIPPED_NOT_DB2", "CACHED_SKIPPED_NOT_DB2", "NOT_APPLICABLE", "CACHED_NOT_APPLICABLE"}

DEFAULT_CLIENTS = [
    {"client": "vena", "db2_type": "linux"},
    {"client": "bes", "db2_type": "windows"},
    {"client": "etos", "db2_type": "windows"},
    {"client": "galena", "db2_type": "windows"},
    {"client": "iwaniuk", "db2_type": "windows"},
    {"client": "novo-med-klobuck", "db2_type": "windows"},
    {"client": "novo-med-miedzno", "db2_type": "windows"},
    {"client": "novo-med-panki", "db2_type": "windows"},
    {"client": "novo-med-popow", "db2_type": "windows"},
    {"client": "nowinski", "db2_type": "windows"},
    {"client": "pulsmed", "db2_type": "windows"},
    {"client": "salomon", "db2_type": "windows"},
    {"client": "kulej", "db2_type": "none"},
    {"client": "jagielska", "db2_type": "none"},
]


def now():
    return datetime.now(timezone.utc).isoformat()


def parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def existing_columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def add_column_if_missing(conn, table, column, ddl):
    if column not in existing_columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with db() as conn:
        conn.executescript('''
CREATE TABLE IF NOT EXISTS check_results (
 id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, source TEXT NOT NULL, client TEXT NOT NULL, status TEXT NOT NULL,
 file_path TEXT, last_backup_time TEXT, backup_age_hours REAL, backup_count INTEGER, checked_at TEXT, message TEXT, raw_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS client_status (
 client TEXT PRIMARY KEY, overall_status TEXT NOT NULL DEFAULT 'UNKNOWN', freshness_status TEXT, freshness_checked_at TEXT,
 db2_linux_status TEXT, db2_linux_checked_at TEXT, db2_windows_status TEXT, db2_windows_checked_at TEXT,
 last_backup_time TEXT, backup_age_hours REAL, backup_count INTEGER, last_file_path TEXT, last_message TEXT, last_error TEXT, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS client_notifications (
 client TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0, email TEXT, notify_error INTEGER NOT NULL DEFAULT 1,
 notify_warning INTEGER NOT NULL DEFAULT 0, notify_stale INTEGER NOT NULL DEFAULT 0, last_notified_key TEXT, last_notified_at TEXT, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS notification_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, client TEXT NOT NULL, email TEXT NOT NULL, status TEXT NOT NULL,
 problem_key TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL, send_status TEXT NOT NULL, error TEXT);
CREATE TABLE IF NOT EXISTS cert_results (
 id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, client TEXT NOT NULL, cert_file TEXT, cert_path TEXT, status TEXT NOT NULL,
 cert_status TEXT NOT NULL, valid_from TEXT, valid_to TEXT, days_left INTEGER, should_alert INTEGER, serial_number TEXT, issuer TEXT, subject TEXT, message TEXT, raw_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cert_status (
 client TEXT NOT NULL, cert_file TEXT NOT NULL, cert_path TEXT, status TEXT NOT NULL, cert_status TEXT NOT NULL,
 valid_from TEXT, valid_to TEXT, days_left INTEGER, should_alert INTEGER, serial_number TEXT, issuer TEXT, subject TEXT, message TEXT, checked_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 PRIMARY KEY (client, cert_file));
CREATE TABLE IF NOT EXISTS clients_config (
 client TEXT PRIMARY KEY,
 display_name TEXT,
 active INTEGER NOT NULL DEFAULT 1,
 backup_enabled INTEGER NOT NULL DEFAULT 1,
 db2_type TEXT NOT NULL DEFAULT 'none',
 cert_p1_enabled INTEGER NOT NULL DEFAULT 0,
 notification_email TEXT,
 notes TEXT,
 updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_check_results_client_created ON check_results(client, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_check_results_created ON check_results(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notification_events_client_created ON notification_events(client, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_cert_results_client_created ON cert_results(client, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_cert_results_created ON cert_results(created_at DESC);
''')
        for col in ("freshness_message", "db2_linux_message", "db2_windows_message", "freshness_file_path", "db2_linux_file_path", "db2_windows_file_path"):
            add_column_if_missing(conn, "client_status", col, "TEXT")
        for item in DEFAULT_CLIENTS:
            conn.execute(
                "INSERT OR IGNORE INTO clients_config (client, display_name, active, backup_enabled, db2_type, cert_p1_enabled, updated_at) VALUES (?,?,?,?,?,?,?)",
                (item["client"], item["client"], 1, 1, item["db2_type"], 0, now()),
            )
        sync_missing_client_configs(conn)
        prune_history(conn)


def prune_history(conn=None):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=HISTORY_DAYS)).isoformat()
    own = conn is None
    conn = conn or db()
    try:
        conn.execute("DELETE FROM check_results WHERE created_at < ?", (cutoff,))
        conn.execute("DELETE FROM notification_events WHERE created_at < ?", (cutoff,))
        conn.execute("DELETE FROM cert_results WHERE created_at < ?", (cutoff,))
        if own:
            conn.commit()
    finally:
        if own:
            conn.close()


def require_token():
    if request.headers.get("X-Backup-Panel-Token", "") != API_TOKEN:
        abort(401)


def sync_missing_client_configs(conn):
    known = {r["client"] for r in conn.execute("SELECT client FROM clients_config")}
    names = set()
    names.update(r["client"] for r in conn.execute("SELECT client FROM client_status WHERE client NOT IN ('db2inst1','__system__')"))
    names.update(r["client"] for r in conn.execute("SELECT DISTINCT client FROM cert_status WHERE client NOT IN ('db2inst1','__system__')"))
    for client in sorted(names - known):
        cs = conn.execute("SELECT db2_linux_status, db2_windows_status FROM client_status WHERE client=?", (client,)).fetchone()
        cert = conn.execute("SELECT 1 FROM cert_status WHERE client=? LIMIT 1", (client,)).fetchone()
        db2_type = "none"
        backup_enabled = 1 if cs else 0
        cert_enabled = 1 if cert else 0
        if cs:
            linux = str(cs["db2_linux_status"] or "").upper()
            windows = str(cs["db2_windows_status"] or "").upper()
            if linux in {"OK", "CACHED_OK"}:
                db2_type = "linux"
            elif windows in {"OK", "CACHED_OK"} or linux in {"SKIPPED_WINDOWS_DB2", "CACHED_SKIPPED_WINDOWS_DB2"}:
                db2_type = "windows"
        conn.execute(
            "INSERT INTO clients_config (client, display_name, active, backup_enabled, db2_type, cert_p1_enabled, updated_at) VALUES (?,?,?,?,?,?,?)",
            (client, client, 1, backup_enabled, db2_type, cert_enabled, now()),
        )


def active_clients_clause(alias="c"):
    return f"EXISTS (SELECT 1 FROM clients_config cfg WHERE cfg.client={alias}.client AND cfg.active=1)"


def norm(payload):
    source = str(payload.get("source") or "").strip()
    client = str(payload.get("client") or "").strip()
    status = str(payload.get("status") or "").strip().upper()
    if not source or not client or not status:
        raise ValueError("Wymagane pola: source, client, status")
    return {"source": source, "client": client, "status": status, "file_path": payload.get("file_path") or payload.get("file") or "", "last_backup_time": payload.get("last_backup_time") or payload.get("lastBackupTime"), "backup_age_hours": payload.get("backup_age_hours") if payload.get("backup_age_hours") is not None else payload.get("backupAgeHours"), "backup_count": payload.get("backup_count") if payload.get("backup_count") is not None else payload.get("backupCount"), "checked_at": payload.get("checked_at") or payload.get("checkedAt") or now(), "message": payload.get("message") or "", "raw_json": json.dumps(payload, ensure_ascii=False)}


def norm_cert(payload):
    client = str(payload.get("client") or payload.get("klient") or "").strip()
    cert_file = str(payload.get("cert_file") or payload.get("certyfikat") or "").strip()
    status = str(payload.get("status") or "unknown").strip().lower()
    days_left = payload.get("days_left") if payload.get("days_left") is not None else payload.get("dni_do_wygasniecia")
    try:
        days_left = int(days_left) if days_left is not None else None
    except Exception:
        days_left = None
    return {"client": client or "__system__", "cert_file": cert_file or "-", "cert_path": payload.get("cert_path") or payload.get("sciezka") or "", "status": status, "valid_from": payload.get("valid_from") or payload.get("wazny_od") or "", "valid_to": payload.get("valid_to") or payload.get("wazny_do") or "", "days_left": days_left, "should_alert": bool(payload.get("should_alert") if payload.get("should_alert") is not None else payload.get("alert_30_dni")), "serial_number": payload.get("serial_number") or payload.get("numer_seryjny") or "", "issuer": payload.get("issuer") or "", "subject": payload.get("subject") or "", "message": payload.get("error") or payload.get("blad") or payload.get("message") or "", "raw_json": json.dumps(payload, ensure_ascii=False)}


def cert_overall(item):
    if item["status"] in ("error", "parse_error"):
        return "ERROR"
    if item["days_left"] is None:
        return "UNKNOWN"
    if item["days_left"] < 0:
        return "ERROR"
    if item["days_left"] <= CERT_WARN_DAYS:
        return "WARNING"
    return "OK"


def should_ignore(item):
    return item["client"] in SYSTEM_CLIENTS or item["status"] in SYSTEM_STATUSES


def rank(status):
    if not status:
        return 1
    status = str(status).upper()
    if status in ERRORISH:
        return 3
    if status in WARN:
        return 2
    if status in OK or status in INFO or status.startswith("CACHED_"):
        return 0
    return 1


def combined_db2_status(row):
    linux = row["db2_linux_status"] if hasattr(row, "keys") else row.get("db2_linux_status")
    windows = row["db2_windows_status"] if hasattr(row, "keys") else row.get("db2_windows_status")
    if rank(linux) == 3: return linux
    if rank(windows) == 3: return windows
    if linux in ("OK", "CACHED_OK"): return linux
    if windows in ("OK", "CACHED_OK"): return windows
    if linux and linux not in ("NOT_APPLICABLE", "CACHED_NOT_APPLICABLE"): return linux
    if windows and windows not in ("NOT_APPLICABLE", "CACHED_NOT_APPLICABLE"): return windows
    return linux or windows or "-"


def backup_system(row):
    configured = str(row.get("configured_db2_type") or row.get("db2_type") or "").lower()
    if configured == "linux": return "🐧", "Linux DB2"
    if configured == "windows": return "🪟", "Windows DB2"
    linux = str(row.get("db2_linux_status") or "").upper()
    windows = str(row.get("db2_windows_status") or "").upper()
    if linux in {"OK", "CACHED_OK"}: return "🐧", "Linux DB2"
    if windows in {"OK", "CACHED_OK"} or linux in {"SKIPPED_WINDOWS_DB2", "CACHED_SKIPPED_WINDOWS_DB2"}: return "🪟", "Windows DB2"
    if configured == "none": return "📦", "Inny system / bez DB2"
    if linux in NOT_DB2_STATUSES and windows in NOT_DB2_STATUSES: return "📦", "Inny system / nie DB2"
    return "–", "Nieustalony"


def overall(row):
    freshness = row["freshness_status"]
    statuses = [freshness, row["db2_linux_status"], row["db2_windows_status"]]
    if freshness in ("BRAK", "OLD", "ERROR", "NO_DB2_BACKUP", "SKIPPED_NO_BACKUP"):
        return "ERROR"
    if any(rank(s) == 3 for s in statuses): return "ERROR"
    checked = [parse_dt(row["freshness_checked_at"]), parse_dt(row["db2_linux_checked_at"]), parse_dt(row["db2_windows_checked_at"])]
    checked = [x for x in checked if x]
    if checked:
        last = max(checked)
        if (datetime.now(last.tzinfo or timezone.utc) - last).total_seconds() / 3600 > STALE_HOURS:
            return "STALE"
    if any(rank(s) == 2 for s in statuses): return "WARNING"
    if not any(statuses): return "UNKNOWN"
    return "OK"


def clean_message(message, limit=500):
    message = " ".join(str(message or "").split())
    return message[: limit - 3] + "..." if len(message) > limit else message


def display_message(row):
    if row["overall_status"] == "ERROR":
        if row["freshness_status"] in ("BRAK", "OLD", "ERROR", "NO_DB2_BACKUP", "SKIPPED_NO_BACKUP") and row["freshness_message"]:
            return clean_message(row["freshness_message"])
        if rank(row["db2_linux_status"]) == 3 and row["db2_linux_message"]: return clean_message(row["db2_linux_message"])
        if rank(row["db2_windows_status"]) == 3 and row["db2_windows_message"]: return clean_message(row["db2_windows_message"])
        return clean_message(row["last_error"] or "ERROR")
    if row["overall_status"] == "WARNING":
        for s, m in ((row["freshness_status"], row["freshness_message"]), (row["db2_linux_status"], row["db2_linux_message"]), (row["db2_windows_status"], row["db2_windows_message"])):
            if rank(s) == 2 and m: return clean_message(m)
    return ""


def decorate_client(row):
    icon, label = backup_system(row)
    row["backup_system_icon"] = icon
    row["backup_system_label"] = label
    row["db2_status"] = combined_db2_status(row)
    row["display_message"] = display_message(row)
    age_h = row.get("backup_age_hours")
    row["backup_age_days"] = round(float(age_h) / 24, 1) if age_h is not None else None
    row["display_name"] = row.get("display_name") or row.get("client")
    return row


def send_mail(to_email, subject, body):
    if not SMTP_HOST:
        raise RuntimeError("BACKUP_PANEL_SMTP_HOST nie jest ustawiony")
    msg = EmailMessage(); msg["From"] = SMTP_FROM; msg["To"] = to_email; msg["Subject"] = subject; msg.set_content(body)
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as smtp:
        if SMTP_TLS: smtp.starttls()
        if SMTP_USER: smtp.login(SMTP_USER, SMTP_PASS)
        smtp.send_message(msg)


def notification_config(conn, client):
    cfg = conn.execute("SELECT * FROM client_notifications WHERE client=?", (client,)).fetchone()
    if not cfg:
        email = conn.execute("SELECT notification_email FROM clients_config WHERE client=?", (client,)).fetchone()
        conn.execute("INSERT INTO client_notifications (client, enabled, email, updated_at) VALUES (?,?,?,?)", (client, 0, email["notification_email"] if email else "", now()))
        cfg = conn.execute("SELECT * FROM client_notifications WHERE client=?", (client,)).fetchone()
    return cfg


def maybe_notify(conn, client):
    cfg_client = conn.execute("SELECT active FROM clients_config WHERE client=?", (client,)).fetchone()
    if cfg_client and not cfg_client["active"]:
        return
    row = conn.execute("SELECT * FROM client_status WHERE client=?", (client,)).fetchone()
    if not row: return
    status = overall(row)
    cfg = notification_config(conn, client)
    if not cfg["enabled"] or not cfg["email"]: return
    if status == "ERROR" and not cfg["notify_error"]: return
    if status == "WARNING" and not cfg["notify_warning"]: return
    if status == "STALE" and not cfg["notify_stale"]: return
    if status not in {"ERROR", "WARNING", "STALE"}: return
    tmp = dict(row); tmp["overall_status"] = status
    msg = display_message(tmp) or status
    problem_key = "|".join([client, status, str(row["freshness_status"]), str(row["db2_linux_status"]), str(row["db2_windows_status"]), str(row["last_file_path"]), datetime.now(timezone.utc).date().isoformat()])
    if cfg["last_notified_key"] == problem_key: return
    subject = f"PC MED backup: {client} - {status}"
    body = f"Wykryto problem z kopią zapasową klienta: {client}\n\nStatus: {status}\nŚwieżość: {row['freshness_status'] or '-'}\nDB2: {combined_db2_status(row)}\nOstatnia kopia: {row['last_backup_time'] or '-'}\nPlik: {row['last_file_path'] or '-'}\nKomunikat: {msg}\n"
    send_status, error = "sent", None
    try: send_mail(cfg["email"], subject, body)
    except Exception as exc: send_status, error = "error", str(exc)
    conn.execute("INSERT INTO notification_events (created_at, client, email, status, problem_key, subject, body, send_status, error) VALUES (?,?,?,?,?,?,?,?,?)", (now(), client, cfg["email"], status, problem_key, subject, body, send_status, error))
    if send_status == "sent": conn.execute("UPDATE client_notifications SET last_notified_key=?, last_notified_at=?, updated_at=? WHERE client=?", (problem_key, now(), now(), client))


def save_item(item):
    if should_ignore(item): return "ignored"
    with db() as conn:
        prune_history(conn)
        if not conn.execute("SELECT client FROM clients_config WHERE client=?", (item["client"],)).fetchone():
            conn.execute("INSERT INTO clients_config (client, display_name, active, backup_enabled, db2_type, cert_p1_enabled, updated_at) VALUES (?,?,?,?,?,?,?)", (item["client"], item["client"], 1, 1, "none", 0, now()))
        conn.execute("INSERT INTO check_results (created_at,source,client,status,file_path,last_backup_time,backup_age_hours,backup_count,checked_at,message,raw_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (now(), item["source"], item["client"], item["status"], item["file_path"], item["last_backup_time"], item["backup_age_hours"], item["backup_count"], item["checked_at"], item["message"], item["raw_json"]))
        if not conn.execute("SELECT client FROM client_status WHERE client=?", (item["client"],)).fetchone(): conn.execute("INSERT INTO client_status (client, overall_status, updated_at) VALUES (?,'UNKNOWN',?)", (item["client"], now()))
        fields = {"updated_at": now()}
        if item["file_path"]: fields["last_file_path"] = item["file_path"]
        if item["last_backup_time"]: fields["last_backup_time"] = item["last_backup_time"]
        if item["backup_age_hours"] is not None: fields["backup_age_hours"] = item["backup_age_hours"]
        if item["backup_count"] is not None: fields["backup_count"] = item["backup_count"]
        if item["status"] == "ERROR": fields["last_error"] = item["message"] or "ERROR"
        if item["source"] == "freshness": fields.update({"freshness_status": item["status"], "freshness_checked_at": item["checked_at"], "freshness_message": item["message"], "freshness_file_path": item["file_path"]})
        elif item["source"] == "db2_linux": fields.update({"db2_linux_status": item["status"], "db2_linux_checked_at": item["checked_at"], "db2_linux_message": item["message"], "db2_linux_file_path": item["file_path"]})
        elif item["source"] == "db2_windows": fields.update({"db2_windows_status": item["status"], "db2_windows_checked_at": item["checked_at"], "db2_windows_message": item["message"], "db2_windows_file_path": item["file_path"]})
        conn.execute("UPDATE client_status SET " + ", ".join(f"{k}=?" for k in fields) + " WHERE client=?", list(fields.values()) + [item["client"]])
        row = conn.execute("SELECT * FROM client_status WHERE client=?", (item["client"],)).fetchone()
        new_overall = overall(row); tmp = dict(row); tmp["overall_status"] = new_overall
        if new_overall == "OK": conn.execute("UPDATE client_status SET overall_status=?, last_message=?, last_error=NULL, updated_at=? WHERE client=?", (new_overall, display_message(tmp), now(), item["client"]))
        else: conn.execute("UPDATE client_status SET overall_status=?, last_message=?, updated_at=? WHERE client=?", (new_overall, display_message(tmp), now(), item["client"]))
        maybe_notify(conn, item["client"])
    return "accepted"


def save_cert_item(item):
    cert_status = cert_overall(item)
    with db() as conn:
        prune_history(conn)
        if not conn.execute("SELECT client FROM clients_config WHERE client=?", (item["client"],)).fetchone():
            conn.execute("INSERT INTO clients_config (client, display_name, active, backup_enabled, db2_type, cert_p1_enabled, updated_at) VALUES (?,?,?,?,?,?,?)", (item["client"], item["client"], 1, 0, "none", 1, now()))
        conn.execute("INSERT INTO cert_results (created_at,client,cert_file,cert_path,status,cert_status,valid_from,valid_to,days_left,should_alert,serial_number,issuer,subject,message,raw_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (now(), item["client"], item["cert_file"], item["cert_path"], item["status"], cert_status, item["valid_from"], item["valid_to"], item["days_left"], 1 if item["should_alert"] else 0, item["serial_number"], item["issuer"], item["subject"], item["message"], item["raw_json"]))
        conn.execute("INSERT INTO cert_status (client,cert_file,cert_path,status,cert_status,valid_from,valid_to,days_left,should_alert,serial_number,issuer,subject,message,checked_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(client, cert_file) DO UPDATE SET cert_path=excluded.cert_path,status=excluded.status,cert_status=excluded.cert_status,valid_from=excluded.valid_from,valid_to=excluded.valid_to,days_left=excluded.days_left,should_alert=excluded.should_alert,serial_number=excluded.serial_number,issuer=excluded.issuer,subject=excluded.subject,message=excluded.message,checked_at=excluded.checked_at,updated_at=excluded.updated_at", (item["client"], item["cert_file"], item["cert_path"], item["status"], cert_status, item["valid_from"], item["valid_to"], item["days_left"], 1 if item["should_alert"] else 0, item["serial_number"], item["issuer"], item["subject"], item["message"], now(), now()))
    return {"client": item["client"], "cert_file": item["cert_file"], "cert_status": cert_status}


def refresh_display_messages():
    with db() as conn:
        for row in conn.execute("SELECT * FROM client_status").fetchall():
            new_overall = overall(row); tmp = dict(row); tmp["overall_status"] = new_overall
            conn.execute("UPDATE client_status SET overall_status=?, last_message=?, last_error=CASE WHEN ?='OK' THEN NULL ELSE last_error END WHERE client=?", (new_overall, display_message(tmp), new_overall, row["client"]))


def fetch_clients():
    refresh_display_messages()
    with db() as conn:
        sync_missing_client_configs(conn)
        rows = [decorate_client(dict(r)) for r in conn.execute("""
SELECT s.*, cfg.display_name, cfg.active, cfg.backup_enabled, cfg.db2_type AS configured_db2_type, cfg.cert_p1_enabled
FROM client_status s
JOIN clients_config cfg ON cfg.client=s.client
WHERE s.client NOT IN ('db2inst1','__system__') AND cfg.active=1 AND cfg.backup_enabled=1
ORDER BY COALESCE(cfg.display_name, s.client) COLLATE NOCASE
""")]
    return rows


def summarize(clients):
    summary = {"total": len(clients), "OK": 0, "WARNING": 0, "ERROR": 0, "STALE": 0, "UNKNOWN": 0}
    for c in clients:
        summary[c.get("overall_status") or "UNKNOWN"] = summary.get(c.get("overall_status") or "UNKNOWN", 0) + 1
    return summary


def cert_summary(rows):
    s = {"total": len(rows), "OK": 0, "WARNING": 0, "ERROR": 0, "UNKNOWN": 0}
    for r in rows:
        s[r.get("cert_status") or "UNKNOWN"] = s.get(r.get("cert_status") or "UNKNOWN", 0) + 1
    return s


def service_clients(service):
    with db() as conn:
        sync_missing_client_configs(conn)
        if service == "backup":
            where = "active=1 AND backup_enabled=1"
        elif service == "db2-linux":
            where = "active=1 AND backup_enabled=1 AND db2_type='linux'"
        elif service == "db2-windows":
            where = "active=1 AND backup_enabled=1 AND db2_type='windows'"
        elif service == "cert-p1":
            where = "active=1 AND cert_p1_enabled=1"
        else:
            where = "active=1"
        rows = [dict(r) for r in conn.execute(f"SELECT * FROM clients_config WHERE {where} ORDER BY COALESCE(display_name, client) COLLATE NOCASE")]
    return rows


def client_list_payload(service="all"):
    rows = service_clients(service)
    return {
        "service": service,
        "count": len(rows),
        "clients": [r["client"] for r in rows],
        "items": rows,
        "generatedAt": now(),
    }


@app.route("/api/check-result", methods=["POST"])
def check_result():
    require_token(); data = request.get_json(force=True); rows = data if isinstance(data, list) else [data]
    accepted, ignored = [], []
    for payload in rows:
        item = norm(payload); result = save_item(item)
        (ignored if result == "ignored" else accepted).append({"client": item["client"], "source": item["source"], "status": item["status"]})
    return jsonify({"ok": True, "accepted": accepted, "ignored": ignored})

@app.route("/api/cert-result", methods=["POST"])
def cert_result():
    require_token(); data = request.get_json(force=True); rows = data if isinstance(data, list) else [data]
    accepted = [save_cert_item(norm_cert(r)) for r in rows]
    return jsonify({"ok": True, "accepted": accepted})

@app.route("/api/status")
def api_status():
    clients = fetch_clients(); return jsonify({"generatedAt": now(), "historyDays": HISTORY_DAYS, "summary": summarize(clients), "clients": clients})

@app.route("/api/clients")
def api_clients():
    require_token(); return jsonify(client_list_payload("all"))

@app.route("/api/clients/<service>")
def api_clients_service(service):
    require_token()
    if service not in {"backup", "db2-linux", "db2-windows", "cert-p1"}:
        abort(404)
    return jsonify(client_list_payload(service))

@app.route("/api/certificates")
def api_certificates():
    with db() as conn:
        rows = [dict(r) for r in conn.execute("""
SELECT cs.* FROM cert_status cs
JOIN clients_config cfg ON cfg.client=cs.client
WHERE cfg.active=1 AND cfg.cert_p1_enabled=1
ORDER BY CASE cs.cert_status WHEN 'ERROR' THEN 0 WHEN 'WARNING' THEN 1 WHEN 'UNKNOWN' THEN 2 ELSE 3 END, cs.days_left ASC, cs.client COLLATE NOCASE
""")]
    return jsonify({"generatedAt": now(), "warningDays": CERT_WARN_DAYS, "summary": cert_summary(rows), "certificates": rows})

@app.route("/api/history/<client>")
def api_history(client):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=HISTORY_DAYS)).isoformat()
    with db() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM check_results WHERE client=? AND created_at>=? ORDER BY created_at DESC", (client, cutoff))]
    return jsonify({"client": client, "historyDays": HISTORY_DAYS, "results": rows})

@app.route("/")
def index():
    clients = fetch_clients(); return render_template("index.html", clients=clients, summary=summarize(clients), generated_at=now())

@app.route("/certificates")
def certificates():
    with db() as conn:
        sync_missing_client_configs(conn)
        rows = [dict(r) for r in conn.execute("""
SELECT cs.* FROM cert_status cs
JOIN clients_config cfg ON cfg.client=cs.client
WHERE cfg.active=1 AND cfg.cert_p1_enabled=1
ORDER BY CASE cs.cert_status WHEN 'ERROR' THEN 0 WHEN 'WARNING' THEN 1 WHEN 'UNKNOWN' THEN 2 ELSE 3 END, cs.days_left ASC, cs.client COLLATE NOCASE
""")]
    return render_template("certificates.html", certificates=rows, summary=cert_summary(rows), generated_at=now(), warning_days=CERT_WARN_DAYS)

@app.route("/clients")
def clients_config_page():
    with db() as conn:
        sync_missing_client_configs(conn)
        rows = [dict(r) for r in conn.execute("SELECT * FROM clients_config ORDER BY active DESC, COALESCE(display_name, client) COLLATE NOCASE")]
    return render_template("clients.html", clients=rows, generated_at=now())

@app.route("/clients/new", methods=["GET", "POST"])
def client_config_new():
    if request.method == "POST":
        client = request.form.get("client", "").strip()
        if not client:
            abort(400)
        with db() as conn:
            conn.execute("INSERT OR REPLACE INTO clients_config (client, display_name, active, backup_enabled, db2_type, cert_p1_enabled, notification_email, notes, updated_at) VALUES (?,?,?,?,?,?,?,?,?)", (client, request.form.get("display_name") or client, 1 if request.form.get("active") == "on" else 0, 1 if request.form.get("backup_enabled") == "on" else 0, request.form.get("db2_type") or "none", 1 if request.form.get("cert_p1_enabled") == "on" else 0, request.form.get("notification_email", "").strip(), request.form.get("notes", "").strip(), now()))
        return redirect(url_for("clients_config_page"))
    return render_template("client_config_form.html", item=None)

@app.route("/clients/<client>/edit", methods=["GET", "POST"])
def client_config_edit(client):
    with db() as conn:
        if request.method == "POST":
            conn.execute("UPDATE clients_config SET display_name=?, active=?, backup_enabled=?, db2_type=?, cert_p1_enabled=?, notification_email=?, notes=?, updated_at=? WHERE client=?", (request.form.get("display_name") or client, 1 if request.form.get("active") == "on" else 0, 1 if request.form.get("backup_enabled") == "on" else 0, request.form.get("db2_type") or "none", 1 if request.form.get("cert_p1_enabled") == "on" else 0, request.form.get("notification_email", "").strip(), request.form.get("notes", "").strip(), now(), client))
            if request.form.get("notification_email"):
                notification_config(conn, client)
                conn.execute("UPDATE client_notifications SET email=?, updated_at=? WHERE client=?", (request.form.get("notification_email", "").strip(), now(), client))
            return redirect(url_for("clients_config_page"))
        item = conn.execute("SELECT * FROM clients_config WHERE client=?", (client,)).fetchone()
        if not item: abort(404)
    return render_template("client_config_form.html", item=dict(item))

@app.route("/client/<client>")
def client_report(client):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=HISTORY_DAYS)).isoformat()
    with db() as conn:
        status = conn.execute("SELECT s.*, cfg.display_name, cfg.active, cfg.backup_enabled, cfg.db2_type AS configured_db2_type, cfg.cert_p1_enabled FROM client_status s LEFT JOIN clients_config cfg ON cfg.client=s.client WHERE s.client=?", (client,)).fetchone()
        if not status: abort(404)
        results = [dict(r) for r in conn.execute("SELECT * FROM check_results WHERE client=? AND created_at>=? ORDER BY created_at DESC", (client, cutoff))]
        certs = [dict(r) for r in conn.execute("SELECT * FROM cert_status WHERE client=? ORDER BY days_left ASC", (client,))]
        notifications = [dict(r) for r in conn.execute("SELECT * FROM notification_events WHERE client=? AND created_at>=? ORDER BY created_at DESC LIMIT 50", (client, cutoff))]
        cfg = dict(notification_config(conn, client))
    return render_template("client.html", client=client, status=decorate_client(dict(status)), results=results, certs=certs, notifications=notifications, cfg=cfg, history_days=HISTORY_DAYS, generated_at=now())

@app.route("/client/<client>/notifications", methods=["GET", "POST"])
def client_notifications(client):
    with db() as conn:
        if request.method == "POST":
            notification_config(conn, client)
            conn.execute("UPDATE client_notifications SET enabled=?, email=?, notify_error=?, notify_warning=?, notify_stale=?, updated_at=? WHERE client=?", (1 if request.form.get("enabled") == "on" else 0, request.form.get("email", "").strip(), 1 if request.form.get("notify_error") == "on" else 0, 1 if request.form.get("notify_warning") == "on" else 0, 1 if request.form.get("notify_stale") == "on" else 0, now(), client))
            return redirect(url_for("client_report", client=client))
        cfg = dict(notification_config(conn, client))
    return render_template("notifications.html", client=client, cfg=cfg)

if __name__ == "__main__":
    init_db(); app.run(host="0.0.0.0", port=int(os.environ.get("BACKUP_PANEL_PORT", "8080")))
