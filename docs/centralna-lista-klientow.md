# Centralna lista klientów

Panel jest źródłem prawdy dla listy klientów i usług. Workflowy n8n powinny pobierać listy klientów z API panelu zamiast mieć listy wpisane ręcznie w skryptach.

## Panel

Adres:

```text
/clients
```

Pola konfiguracji:

```text
client                identyfikator klienta, zwykle nazwa katalogu
_display_name_        nazwa wyświetlana
active                czy klient jest aktywny
backup_enabled        czy klient ma usługę kontroli kopii
db2_type              none / linux / windows
cert_p1_enabled       czy klient ma kontrolę certyfikatów P1
notification_email    email powiadomień
notes                 notatka
```

Klient z `active=0` nie jest zwracany do n8n i nie jest pokazywany na dashboardach bieżących. Historia pozostaje w bazie.

## Endpointy dla n8n

Wszystkie endpointy wymagają nagłówka:

```text
X-Backup-Panel-Token: <BACKUP_PANEL_TOKEN>
```

Endpointy:

```text
GET /api/clients
GET /api/clients/backup
GET /api/clients/db2-linux
GET /api/clients/db2-windows
GET /api/clients/cert-p1
```

Przykład odpowiedzi:

```json
{
  "service": "db2-windows",
  "count": 2,
  "clients": ["bes", "etos"],
  "items": [
    {
      "client": "bes",
      "display_name": "bes",
      "active": 1,
      "backup_enabled": 1,
      "db2_type": "windows",
      "cert_p1_enabled": 0
    }
  ],
  "generatedAt": "2026-10-06T12:00:00+00:00"
}
```

## Mapowanie usług

```text
/api/clients/backup       → workflow świeżości kopii
/api/clients/db2-linux    → workflow DB2 Linux
/api/clients/db2-windows  → workflow DB2 Windows
/api/clients/cert-p1      → workflow Certyfikaty P1
```

## Statusy

Brak kopii u klienta z aktywną usługą kopii jest błędem. Klient bez usługi DB2 ma `db2_type=none`, więc workflowy DB2 nie powinny go sprawdzać.
