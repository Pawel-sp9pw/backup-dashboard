#!/usr/bin/env python3
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request

APP_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("BACKUP_PANEL_DB", APP_DIR / "backup_panel.db"))
API_TOKEN = os.environ.get("BACKUP_PANEL_TOKEN", "CHANGE_ME_PANEL_TOKEN")
STALE_HOURS = int(os.environ.get("BACKUP_PANEL_STALE_HOURS", "30"))

app = Flask(__name__)

OK = {"OK", "CACHED_OK"}

INFO = {
    "SKIPPED_LINUX_DB2",
    "CACHED_SKIPPED_LINUX_DB2",
    "SKIPPED_WINDOWS_DB2",
    "CACHED_SKIPPED_WINDOWS_DB2",
    "SKIPPED_NOT_DB2",
    "CACHED_SKIPPED_NOT_DB2",
    "SKIPPED_SYSTEM_DIR",
    "NOT_APPLICABLE",
    "CACHED_NOT_APPLICABLE",
    "NO_DB2_BACKUP",
    "CACHED_NO_DB2_BACKUP",
}

WARN = {
    "WARNING",
    "UNKNOWN",
    "SKIPPED_TOO_NEW",
    "SKIPPED_NO_BACKUP",
    "BRAK",
    "OLD",
    "STALE",
}

SYSTEM_CLIENTS = {"db2inst1", "__system__"}
SYSTEM_STATUSES = {"SKIPPED_SYSTEM_DIR"}
NOT_DB2_STATUSES = {
    "SKIPPED_NOT_DB2",
    "CACHED_SKIPPED_NOT_DB2",
    "NOT_APPLICABLE",
    "CACHED_NOT_APPLICABLE",
    "NO_DB2_BACKUP",
    "CACHED_NO_DB2_BACKUP",
}


def now() -> str:
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


def existing_columns(conn, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def add_column_if_missing(conn, table: str, column: str, ddl: str):
    if column not in existing_columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with db() as conn:
        conn.executescript(
            """
CREATE TABLE IF NOT EXISTS check_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    source TEXT NOT NULL,
    client TEXT NOT NULL,
    status TEXT NOT NULL,
    file_path TEXT,
    last_backup_time TEXT,
    backup_age_hours REAL,
    backup_count INTEGER,
    checked_at TEXT,
    message TEXT,
    raw_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS client_status (
    client TEXT PRIMARY KEY,
    overall_status TEXT NOT NULL DEFAULT 'UNKNOWN',
    freshness_status TEXT,
    freshness_checked_at TEXT,
    db2_linux_status TEXT,
    db2_linux_checked_at TEXT,
    db2_windows_status TEXT,
    db2_windows_checked_at TEXT,
    last_backup_time TEXT,
    backup_age_hours REAL,
    backup_count INTEGER,
    last_file_path TEXT,
    last_message TEXT,
    last_error TEXT,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_check_results_client_created
ON check_results(client, created_at DESC);
"""
        )

        add_column_if_missing(conn, "client_status", "freshness_message", "TEXT")
        add_column_if_missing(conn, "client_status", "db2_linux_message", "TEXT")
        add_column_if_missing(conn, "client_status", "db2_windows_message", "TEXT")
        add_column_if_missing(conn, "client_status", "freshness_file_path", "TEXT")
        add_column_if_missing(conn, "client_status", "db2_linux_file_path", "TEXT")
        add_column_if_missing(conn, "client_status", "db2_windows_file_path", "TEXT")


def require_token():
    if request.headers.get("X-Backup-Panel-Token", "") != API_TOKEN:
        abort(401)


def norm(payload: dict) -> dict:
    source = str(payload.get("source") or "").strip()
    client = str(payload.get("client") or "").strip()
    status = str(payload.get("status") or "").strip().upper()

    if not source or not client or not status:
        raise ValueError("Wymagane pola: source, client, status")

    return {
        "source": source,
        "client": client,
        "status": status,
        "file_path": payload.get("file_path") or payload.get("file") or "",
        "last_backup_time": payload.get("last_backup_time") or payload.get("lastBackupTime"),
        "backup_age_hours": payload.get("backup_age_hours")
        if payload.get("backup_age_hours") is not None
        else payload.get("backupAgeHours"),
        "backup_count": payload.get("backup_count")
        if payload.get("backup_count") is not None
        else payload.get("backupCount"),
        "checked_at": payload.get("checked_at") or payload.get("checkedAt") or now(),
        "message": payload.get("message") or "",
        "raw_json": json.dumps(payload, ensure_ascii=False),
    }


def should_ignore(item: dict) -> bool:
    return item["client"] in SYSTEM_CLIENTS or item["status"] in SYSTEM_STATUSES


def rank(status) -> int:
    if not status:
        return 1
    status = str(status).upper()
    if status == "ERROR":
        return 3
    if status in WARN:
        return 2
    if status in OK or status in INFO or status.startswith("CACHED_"):
        return 0
    return 1


def overall(row) -> str:
    freshness = row["freshness_status"]
    db2_linux = row["db2_linux_status"]
    db2_windows = row["db2_windows_status"]
    statuses = [freshness, db2_linux, db2_windows]

    if freshness in ("BRAK", "OLD", "ERROR"):
        return "ERROR"
    if any(rank(status) == 3 for status in statuses):
        return "ERROR"

    checked_dates = [
        parse_dt(row["freshness_checked_at"]),
        parse_dt(row["db2_linux_checked_at"]),
        parse_dt(row["db2_windows_checked_at"]),
    ]
    checked_dates = [value for value in checked_dates if value]
    if checked_dates:
        last_checked = max(checked_dates)
        age_hours = (
            datetime.now(last_checked.tzinfo or timezone.utc) - last_checked
        ).total_seconds() / 3600
        if age_hours > STALE_HOURS:
            return "STALE"

    if any(rank(status) == 2 for status in statuses):
        return "WARNING"
    if not any(statuses):
        return "UNKNOWN"
    return "OK"


def clean_message(message: str, limit: int = 500) -> str:
    message = " ".join(str(message or "").split())
    if len(message) > limit:
        return message[: limit - 3] + "..."
    return message


def display_message(row) -> str:
    overall_status = row["overall_status"]
    freshness_status = row["freshness_status"]
    db2_linux_status = row["db2_linux_status"]
    db2_windows_status = row["db2_windows_status"]
    freshness_message = row["freshness_message"]
    db2_linux_message = row["db2_linux_message"]
    db2_windows_message = row["db2_windows_message"]

    if overall_status == "ERROR":
        if freshness_status in ("BRAK", "OLD", "ERROR") and freshness_message:
            return clean_message(freshness_message)
        if rank(db2_linux_status) == 3 and db2_linux_message:
            return clean_message(db2_linux_message)
        if rank(db2_windows_status) == 3 and db2_windows_message:
            return clean_message(db2_windows_message)
        return clean_message(row["last_error"] or "ERROR")

    if overall_status == "WARNING":
        if rank(freshness_status) == 2 and freshness_message:
            return clean_message(freshness_message)
        if rank(db2_linux_status) == 2 and db2_linux_message:
            return clean_message(db2_linux_message)
        if rank(db2_windows_status) == 2 and db2_windows_message:
            return clean_message(db2_windows_message)

    return ""


def backup_system(row: dict) -> tuple[str, str]:
    linux_status = str(row.get("db2_linux_status") or "").upper()
    windows_status = str(row.get("db2_windows_status") or "").upper()

    if linux_status in {"OK", "CACHED_OK"}:
        return "🐧", "Linux DB2"

    if windows_status in {"OK", "CACHED_OK"} or linux_status in {
        "SKIPPED_WINDOWS_DB2",
        "CACHED_SKIPPED_WINDOWS_DB2",
    }:
        return "🪟", "Windows DB2"

    if linux_status in NOT_DB2_STATUSES and windows_status in NOT_DB2_STATUSES:
        return "📦", "Inny system / nie DB2"
    if linux_status in NOT_DB2_STATUSES:
        return "📦", "Inny system / nie DB2"

    return "–", "Nieustalony"


def unified_db2_status(row: dict) -> tuple[str, str]:
    icon, label = backup_system(row)
    linux_status = str(row.get("db2_linux_status") or "").upper()
    windows_status = str(row.get("db2_windows_status") or "").upper()

    if label == "Linux DB2":
        return linux_status or "-", "linux"
    if label == "Windows DB2":
        if windows_status and windows_status not in {"NOT_APPLICABLE", "CACHED_NOT_APPLICABLE"}:
            return windows_status, "windows"
        return linux_status or windows_status or "-", "windows"
    if label == "Inny system / nie DB2":
        return "NOT_APPLICABLE", "other"

    if rank(linux_status) == 3:
        return linux_status, "linux"
    if rank(windows_status) == 3:
        return windows_status, "windows"

    return linux_status or windows_status or "-", "unknown"


def decorate_client(row: dict) -> dict:
    icon, label = backup_system(row)
    db2_status, db2_class = unified_db2_status(row)
    row["backup_system_icon"] = icon
    row["backup_system_label"] = label
    row["db2_status"] = db2_status
    row["db2_status_class"] = db2_class
    row["backup_age_days"] = None
    if row.get("backup_age_hours") is not None:
        try:
            row["backup_age_days"] = float(row["backup_age_hours"]) / 24
        except Exception:
            row["backup_age_days"] = None
    row["display_message"] = display_message(row)
    return row


def save_item(item: dict):
    if should_ignore(item):
        return "ignored"

    with db() as conn:
        conn.execute(
            """
INSERT INTO check_results (
    created_at, source, client, status, file_path, last_backup_time,
    backup_age_hours, backup_count, checked_at, message, raw_json
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
""",
            (
                now(),
                item["source"],
                item["client"],
                item["status"],
                item["file_path"],
                item["last_backup_time"],
                item["backup_age_hours"],
                item["backup_count"],
                item["checked_at"],
                item["message"],
                item["raw_json"],
            ),
        )

        exists = conn.execute(
            "SELECT client FROM client_status WHERE client = ?", (item["client"],)
        ).fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO client_status (client, overall_status, updated_at) VALUES (?, 'UNKNOWN', ?)",
                (item["client"], now()),
            )

        fields = {"updated_at": now()}
        if item["file_path"]:
            fields["last_file_path"] = item["file_path"]
        if item["last_backup_time"]:
            fields["last_backup_time"] = item["last_backup_time"]
        if item["backup_age_hours"] is not None:
            fields["backup_age_hours"] = item["backup_age_hours"]
        if item["backup_count"] is not None:
            fields["backup_count"] = item["backup_count"]
        if item["status"] == "ERROR":
            fields["last_error"] = item["message"] or "ERROR"

        if item["source"] == "freshness":
            fields.update(
                {
                    "freshness_status": item["status"],
                    "freshness_checked_at": item["checked_at"],
                    "freshness_message": item["message"],
                    "freshness_file_path": item["file_path"],
                }
            )
        elif item["source"] == "db2_linux":
            fields.update(
                {
                    "db2_linux_status": item["status"],
                    "db2_linux_checked_at": item["checked_at"],
                    "db2_linux_message": item["message"],
                    "db2_linux_file_path": item["file_path"],
                }
            )
        elif item["source"] == "db2_windows":
            fields.update(
                {
                    "db2_windows_status": item["status"],
                    "db2_windows_checked_at": item["checked_at"],
                    "db2_windows_message": item["message"],
                    "db2_windows_file_path": item["file_path"],
                }
            )

        conn.execute(
            "UPDATE client_status SET "
            + ", ".join(f"{field} = ?" for field in fields)
            + " WHERE client = ?",
            list(fields.values()) + [item["client"]],
        )

        row = conn.execute(
            "SELECT * FROM client_status WHERE client = ?", (item["client"],)
        ).fetchone()
        new_overall = overall(row)
        tmp = dict(row)
        tmp["overall_status"] = new_overall
        new_message = display_message(tmp)
        new_error = tmp.get("last_error") if new_overall == "ERROR" else None

        conn.execute(
            "UPDATE client_status SET overall_status = ?, last_message = ?, last_error = ?, updated_at = ? WHERE client = ?",
            (new_overall, new_message, new_error, now(), item["client"]),
        )

    return "accepted"


def refresh_display_messages():
    with db() as conn:
        rows = conn.execute("SELECT * FROM client_status").fetchall()
        for row in rows:
            new_overall = overall(row)
            tmp = dict(row)
            tmp["overall_status"] = new_overall
            new_message = display_message(tmp)
            new_error = row["last_error"] if new_overall == "ERROR" else None
            conn.execute(
                "UPDATE client_status SET overall_status = ?, last_message = ?, last_error = ? WHERE client = ?",
                (new_overall, new_message, new_error, row["client"]),
            )


def fetch_clients():
    refresh_display_messages()
    with db() as conn:
        rows = [
            decorate_client(dict(row))
            for row in conn.execute(
                "SELECT * FROM client_status WHERE client NOT IN ('db2inst1', '__system__') ORDER BY client COLLATE NOCASE"
            )
        ]
    return rows


def summarize(clients):
    summary = {"total": len(clients), "OK": 0, "WARNING": 0, "ERROR": 0, "STALE": 0, "UNKNOWN": 0}
    for client in clients:
        status = client.get("overall_status") or "UNKNOWN"
        summary[status] = summary.get(status, 0) + 1
    return summary


@app.route("/api/check-result", methods=["POST"])
def check_result():
    require_token()
    data = request.get_json(force=True)
    rows = data if isinstance(data, list) else [data]
    accepted = []
    ignored = []

    for payload in rows:
        item = norm(payload)
        result = save_item(item)
        target = ignored if result == "ignored" else accepted
        target.append({"client": item["client"], "source": item["source"], "status": item["status"]})

    return jsonify({"ok": True, "accepted": accepted, "ignored": ignored})


@app.route("/api/status")
def api_status():
    clients = fetch_clients()
    return jsonify({"generatedAt": now(), "summary": summarize(clients), "clients": clients})


@app.route("/api/history/<client>")
def api_history(client):
    limit = int(request.args.get("limit", "50"))
    with db() as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM check_results WHERE client = ? ORDER BY created_at DESC LIMIT ?",
                (client, limit),
            )
        ]
    return jsonify({"client": client, "results": rows})


@app.route("/")
def index():
    clients = fetch_clients()
    return render_template(
        "index.html",
        clients=clients,
        summary=summarize(clients),
        generated_at=now(),
    )


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=int(os.environ.get("BACKUP_PANEL_PORT", "8080")))
