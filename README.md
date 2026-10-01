# PC MED Backup Dashboard

Lekki dashboard do agregacji i wizualizacji statusu kopii zapasowych klientów PC MED.

## Architektura

```text
n8n / workflowy kontrolne
        ↓ HTTP POST
backup-dashboard na osobnym hoście lub maszynie backupów
        ↓
SQLite + panel WWW
```

Dashboard działa niezależnie od n8n. Workflowy n8n tylko wysyłają wyniki do API dashboardu.

## Struktura repo

```text
backup-panel/   Aplikacja Flask + SQLite
docs/           Instrukcje wdrożenia i zmiany w workflowach n8n
n8n/            Workflow agregatora do importu w n8n
```

Ważne: plik `requirements.txt` znajduje się w katalogu:

```text
backup-panel/requirements.txt
```

Po instalacji powinien trafić tutaj:

```text
/opt/backup-panel/requirements.txt
```

## Szybki start od zera

Poniższe komendy wykonuj na serwerze, na którym ma działać dashboard, np. na maszynie z backupami.

### 1. Instalacja pakietów

```bash
apt update
apt install -y git python3 python3-venv python3-pip unzip
```

Opcjonalnie, jeżeli chcesz używać nginx na porcie 80:

```bash
apt install -y nginx
```

### 2. Pobranie repozytorium

Przejdź do katalogu tymczasowego i sklonuj repo:

```bash
cd /tmp
rm -rf backup-dashboard
git clone https://github.com/Pawel-sp9pw/backup-dashboard.git
cd /tmp/backup-dashboard
```

Sprawdź, czy pliki są na miejscu:

```bash
ls -la
ls -la backup-panel
```

Powinieneś zobaczyć między innymi:

```text
backup-panel/app.py
backup-panel/requirements.txt
backup-panel/templates/
backup-panel/static/
backup-panel/backup-panel.service
```

### 3. Utworzenie użytkownika usługi

Jeżeli użytkownik jeszcze nie istnieje:

```bash
id backup-panel >/dev/null 2>&1 || useradd --system --home /opt/backup-panel --shell /usr/sbin/nologin backup-panel
```

### 4. Instalacja aplikacji do `/opt/backup-panel`

Ważne: tę komendę wykonuj będąc w katalogu głównym repo, czyli:

```bash
pwd
```

powinno pokazać:

```text
/tmp/backup-dashboard
```

Dopiero wtedy kopiuj pliki:

```bash
mkdir -p /opt/backup-panel
cp -r /tmp/backup-dashboard/backup-panel/* /opt/backup-panel/
chown -R backup-panel:backup-panel /opt/backup-panel
```

Sprawdź wynik:

```bash
ls -la /opt/backup-panel
```

W katalogu `/opt/backup-panel` muszą być widoczne:

```text
app.py
requirements.txt
templates
static
backup-panel.service
backup-panel.nginx.conf
```

Jeżeli `requirements.txt` nie istnieje, to znaczy, że skopiowano zły katalog.

### 5. Python venv i zależności

```bash
cd /opt/backup-panel
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt
chown -R backup-panel:backup-panel /opt/backup-panel
```

### 6. Konfiguracja tokenu API

Utwórz plik:

```bash
nano /etc/backup-panel.env
```

Przykładowa zawartość:

```text
BACKUP_PANEL_TOKEN=zmien_na_losowy_dlugi_token
BACKUP_PANEL_PORT=8080
BACKUP_PANEL_STALE_HOURS=30
```

Ustaw uprawnienia:

```bash
chmod 600 /etc/backup-panel.env
```

Token z `BACKUP_PANEL_TOKEN` będzie potrzebny w n8n w nagłówku:

```text
X-Backup-Panel-Token: zmien_na_losowy_dlugi_token
```

### 7. Uruchomienie systemd

```bash
cp /opt/backup-panel/backup-panel.service /etc/systemd/system/backup-panel.service
systemctl daemon-reload
systemctl enable --now backup-panel
systemctl status backup-panel
```

Logi usługi:

```bash
journalctl -u backup-panel -n 100 --no-pager
```

### 8. Test lokalny

```bash
curl http://127.0.0.1:8080/api/status
```

Powinieneś dostać JSON, nawet jeśli nie ma jeszcze klientów.

Testowy wpis do dashboardu:

```bash
curl -X POST http://127.0.0.1:8080/api/check-result \
  -H "Content-Type: application/json" \
  -H "X-Backup-Panel-Token: zmien_na_losowy_dlugi_token" \
  -d '{
    "source": "freshness",
    "client": "test",
    "status": "OK",
    "backup_age_hours": 5,
    "backup_count": 10,
    "checked_at": "2026-10-01T10:00:00+02:00",
    "message": "test"
  }'
```

Panel WWW:

```text
http://IP_SERWERA:8080/
```

Przykładowo, jeśli dashboard działa na serwerze backupów:

```text
http://172.20.0.2:8080/
```

### 9. Firewall

Jeżeli używasz UFW i dashboard ma być dostępny tylko z sieci wewnętrznej:

```bash
ufw allow from 172.20.0.0/24 to any port 8080 proto tcp
```

### 10. Opcjonalnie nginx na porcie 80

```bash
cp /opt/backup-panel/backup-panel.nginx.conf /etc/nginx/sites-available/backup-panel
ln -sf /etc/nginx/sites-available/backup-panel /etc/nginx/sites-enabled/backup-panel
nginx -t
systemctl reload nginx
```

Po nginx panel może być dostępny pod:

```text
http://IP_SERWERA/
```

## Podłączenie n8n

Każdy workflow n8n powinien wysyłać wynik do:

```text
http://IP_SERWERA:8080/api/check-result
```

Nagłówki HTTP:

```text
Content-Type: application/json
X-Backup-Panel-Token: token_z_/etc/backup-panel.env
```

Przykładowe body JSON:

```json
{
  "source": "db2_windows",
  "client": "etos",
  "status": "CACHED_OK",
  "file_path": "\\\\172.20.0.2\\home\\etos\\plik",
  "checked_at": "2026-10-01T10:10:00+02:00",
  "message": "Pominięto db2ckbkp: ten sam plik był już sprawdzony jako OK"
}
```

Szczegółowe zmiany w workflowach są w:

```text
docs/n8n-zmiany-workflowow.md
```

Instrukcja wdrożenia krok po kroku jest również w:

```text
docs/wdrozenie-dashboardu-krok-po-kroku.md
```

## Diagnostyka typowych problemów

### `Could not open requirements file`

Błąd:

```text
ERROR: Could not open requirements file: [Errno 2] No such file or directory: 'requirements.txt'
```

Sprawdź:

```bash
pwd
ls -la
find /opt/backup-panel -maxdepth 2 -type f
```

W `/opt/backup-panel` musi istnieć:

```text
/opt/backup-panel/requirements.txt
```

Jeżeli go nie ma, skopiuj ponownie właściwy katalog:

```bash
cd /tmp
rm -rf backup-dashboard
git clone https://github.com/Pawel-sp9pw/backup-dashboard.git
mkdir -p /opt/backup-panel
cp -r /tmp/backup-dashboard/backup-panel/* /opt/backup-panel/
chown -R backup-panel:backup-panel /opt/backup-panel
cd /opt/backup-panel
ls -la
```

Potem ponów:

```bash
./venv/bin/pip install -r requirements.txt
```

### Usługa nie startuje

Sprawdź:

```bash
systemctl status backup-panel
journalctl -u backup-panel -n 100 --no-pager
```

### API zwraca `401 Unauthorized`

Token w n8n lub w `curl` nie zgadza się z wartością w:

```text
/etc/backup-panel.env
```

Sprawdź:

```bash
cat /etc/backup-panel.env
```

### Panel działa lokalnie, ale nie z innego hosta

Sprawdź port:

```bash
ss -tulpn | grep 8080
```

Sprawdź firewall:

```bash
ufw status
```

Dla sieci `172.20.0.0/24`:

```bash
ufw allow from 172.20.0.0/24 to any port 8080 proto tcp
```
