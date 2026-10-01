# PC MED Backup Dashboard

Lekki dashboard do agregacji i wizualizacji statusu kopii zapasowych klientów PC MED.

Architektura:

- n8n uruchamia workflowy kontrolne,
- workflowy wysyłają wyniki przez HTTP POST,
- dashboard działa niezależnie od n8n,
- dane są zapisywane w SQLite,
- panel WWW pokazuje status per klient.

## Struktura repo

```text
backup-panel/   Aplikacja Flask + SQLite
docs/           Instrukcje wdrożenia i zmiany w workflowach n8n
n8n/            Workflow agregatora do importu w n8n
```

## Szybki start

Na serwerze backupów:

```bash
apt update
apt install -y python3 python3-venv python3-pip unzip
useradd --system --home /opt/backup-panel --shell /usr/sbin/nologin backup-panel
mkdir -p /opt/backup-panel
cp -r backup-panel/* /opt/backup-panel/
chown -R backup-panel:backup-panel /opt/backup-panel
cd /opt/backup-panel
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

Konfiguracja tokenu:

```bash
nano /etc/backup-panel.env
```

```text
BACKUP_PANEL_TOKEN=zmien_na_losowy_dlugi_token
BACKUP_PANEL_PORT=8080
BACKUP_PANEL_STALE_HOURS=30
```

Uruchomienie:

```bash
cp /opt/backup-panel/backup-panel.service /etc/systemd/system/backup-panel.service
systemctl daemon-reload
systemctl enable --now backup-panel
```

Panel:

```text
http://IP_SERWERA:8080/
```

Szczegóły są w `docs/wdrozenie-dashboardu-krok-po-kroku.md`.
