# Certyfikaty P1 w panelu backup-dashboard

Panel ma osobną podstronę:

```text
/certificates
```

oraz endpoint dla n8n:

```text
POST /api/cert-result
Header: X-Backup-Panel-Token: <token z /etc/backup-panel.env>
Content-Type: application/json
```

Akceptowane pola JSON:

```json
{
  "client": "nazwa-klienta",
  "cert_file": "plik.p12",
  "cert_path": "/opt/cert/klient/plik.p12",
  "status": "ok",
  "error": "",
  "valid_from": "2026-01-01 00:00:00",
  "valid_to": "2027-01-01 00:00:00",
  "days_left": 80,
  "should_alert": false,
  "serial_number": "...",
  "issuer": "...",
  "subject": "..."
}
```

Dla zgodności z obecnym workflowem endpoint przyjmie też polskie nazwy pól:

```text
klient, certyfikat, sciezka, blad, wazny_od, wazny_do, dni_do_wygasniecia, alert_30_dni, numer_seryjny
```

## Statusy certyfikatów

Panel przelicza status P1 niezależnie od n8n:

```text
ERROR   - błąd odczytu, błąd parsowania albo certyfikat wygasł
WARNING - certyfikat wygasa w ciągu BACKUP_PANEL_CERT_WARN_DAYS dni
OK      - certyfikat ważny dłużej niż próg ostrzegania
UNKNOWN - brak daty lub danych do oceny
```

Domyślny próg ostrzegania:

```text
BACKUP_PANEL_CERT_WARN_DAYS=30
```

## Zmiana w workflow n8n

Po node `Lista wszystkich certyfikatów` dodaj równolegle do obecnej ścieżki mailowej:

1. Code node `Przygotuj do panelu`
2. HTTP Request `HTTP - wyślij do panelu`

### Code node `Przygotuj do panelu`

```javascript
return items.map(i => ({ json: {
  client: i.json.klient || '',
  cert_file: i.json.certyfikat || '',
  cert_path: i.json.sciezka || '',
  status: i.json.status || 'unknown',
  error: i.json.blad || '',
  valid_from: i.json.wazny_od || '',
  valid_to: i.json.wazny_do || '',
  days_left: i.json.dni_do_wygasniecia,
  should_alert: i.json.alert_30_dni === true,
  serial_number: i.json.numer_seryjny || '',
  issuer: i.json.issuer || '',
  subject: i.json.subject || ''
}}));
```

### HTTP Request `HTTP - wyślij do panelu`

```text
Method: POST
URL: http://172.20.0.2:8080/api/cert-result
Header: Content-Type = application/json
Header: X-Backup-Panel-Token = token z /etc/backup-panel.env
Body: JSON
JSON Body: ={{ $json }}
Continue On Fail: true
```

Obecny workflow może dalej wysyłać mail zbiorczy dla certyfikatów <= 30 dni. Panel będzie jednocześnie przechowywał historię i pokazywał stan na podstronie `/certificates`.
