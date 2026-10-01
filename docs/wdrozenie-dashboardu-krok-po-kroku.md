# Wdrożenie dashboardu backupów PC MED krok po kroku

Zakładany host: maszyna z backupami `172.20.0.2`. Dashboard działa tylko w sieci wewnętrznej na porcie `8080`.

## 1. Instalacja pakietów
```bash
apt update
apt install -y python3 python3-venv python3-pip unzip
```

Opcjonalnie nginx na porcie 80:
```bash
apt install -y nginx
```

## 2. Użytkownik systemowy
```bash
useradd --system --home /opt/backup-panel --shell /usr/sbin/nologin backup-panel
```

## 3. Wgranie aplikacji
```bash
mkdir -p /opt/backup-panel
cp -r backup-panel/* /opt/backup-panel/
chown -R backup-panel:backup-panel /opt/backup-panel
```

## 4. Python venv
```bash
cd /opt/backup-panel
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
chown -R backup-panel:backup-panel /opt/backup-panel
```

## 5. Token API
```bash
nano /etc/backup-panel.env
```
Treść:
```text
BACKUP_PANEL_TOKEN=zmien_na_losowy_dlugi_token
BACKUP_PANEL_PORT=8080
BACKUP_PANEL_STALE_HOURS=30
```
```bash
chmod 600 /etc/backup-panel.env
```

## 6. Systemd
```bash
cp /opt/backup-panel/backup-panel.service /etc/systemd/system/backup-panel.service
systemctl daemon-reload
systemctl enable --now backup-panel
systemctl status backup-panel
```

## 7. Test
```bash
curl http://127.0.0.1:8080/api/status
```

Test POST:
```bash
curl -X POST http://127.0.0.1:8080/api/check-result \
  -H "Content-Type: application/json" \
  -H "X-Backup-Panel-Token: zmien_na_losowy_dlugi_token" \
  -d '{"source":"freshness","client":"test","status":"OK","backup_age_hours":5,"backup_count":10,"message":"test"}'
```

Otwórz: `http://172.20.0.2:8080/`

## 8. Firewall
```bash
ufw allow from 172.20.0.0/24 to any port 8080 proto tcp
```

## 9. Opcjonalnie nginx
```bash
cp /opt/backup-panel/backup-panel.nginx.conf /etc/nginx/sites-available/backup-panel
ln -s /etc/nginx/sites-available/backup-panel /etc/nginx/sites-enabled/backup-panel
nginx -t
systemctl reload nginx
```
