# Historia 90 dni i powiadomienia klientów

Dashboard przechowuje historię wyników kontroli w tabeli `check_results` oraz historię wysłanych powiadomień w tabeli `notification_events`.

Domyślnie przechowywane są dane z ostatnich 90 dni.

Można to zmienić w `/etc/backup-panel.env`:

```text
BACKUP_PANEL_HISTORY_DAYS=90
```

## Raport klienta

Kliknięcie nazwy klienta w dashboardzie otwiera:

```text
/client/<klient>
```

Raport pokazuje:

- aktualny status klienta,
- typ kopii,
- wiek ostatniej kopii w dniach,
- liczbę kopii DB2,
- historię kontroli z ostatnich 90 dni,
- historię powiadomień.

## Panel powiadomień klienta

Dla każdego klienta dostępny jest adres:

```text
/client/<klient>/notifications
```

Można tam ustawić:

- włączenie/wyłączenie powiadomień,
- adres e-mail klienta,
- wysyłkę przy `ERROR`,
- wysyłkę przy `WARNING`,
- wysyłkę przy `STALE`.

Panel nie wysyła ponownie tego samego powiadomienia więcej niż raz dziennie dla identycznego problemu.

## Konfiguracja SMTP

W `/etc/backup-panel.env` dodaj:

```text
BACKUP_PANEL_SMTP_HOST=smtp.example.pl
BACKUP_PANEL_SMTP_PORT=587
BACKUP_PANEL_SMTP_USER=backup@example.pl
BACKUP_PANEL_SMTP_PASS=haslo
BACKUP_PANEL_SMTP_FROM=backup@example.pl
BACKUP_PANEL_SMTP_TLS=1
```

Po zmianie konfiguracji:

```bash
systemctl restart backup-panel
```

## Migracje SQLite

Aplikacja sama tworzy nowe tabele przy starcie:

- `client_notifications`,
- `notification_events`.

Nie trzeba usuwać istniejącej bazy.
