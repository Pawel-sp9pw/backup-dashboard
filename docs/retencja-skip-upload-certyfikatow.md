# Retencja wszystkich plików i upload certyfikatów

## Retencja

Nowa retencja przechodzi rekurencyjnie przez `/home/<klient>` i liczy zwykłe pliki, niezależnie od rozszerzenia czy rozmiaru. **Nie przenosi całych katalogów.** Każdy regularny plik jest jednostką retencji.

Wszystkie katalogi o nazwie dokładnie `.skip` (także na niższych poziomach) i cała ich zawartość są pomijane. Dodatkowo chronione są ukryte pliki zaczynające się od kropki (np. `.bashrc`, `.profile`) oraz symlinki i pliki z innych filesystemów. Nie są one ani liczone, ani przenoszone.

UWAGA: zwykłe pliki pomocnicze (np. `backup.log`, `stop.txt`, `Kopia.ok`) też są teraz liczone jako pliki retencyjne. Jeżeli powinny przetrwać, przenieś je do `.skip` lub dostosuj klasyfikację. **Przed aktywacją `EXECUTE=1` koniecznie przejrzyj listę kandydatów.**

Wybrane pliki są przenoszone do kwarantanny z zachowaniem ścieżek względnych. Limit liczby kopii i dni bierze się z panelu klienta. Kwarantanna i trwałe kasowanie działają jak wcześniej, z osobnymi przełącznikami.

## Wykluczenie .skip również w kontrolach n8n

Zmiana skryptu retencji **nie zmienia automatycznie uruchomionych workflowów n8n**. W workflowie świeżości, Linux DB2 i Windows DB2 należy wykluczyć `.skip` podczas wykrywania i testowania.

- Bash `find`: zastąp przejście typu `find "$client_dir" -type f ...` wariantem `find "$client_dir" -type d -name .skip -prune -o -type f ... -print`. Zachowaj istniejące filtry rozmiaru i wzorców, jeśli nadal mają obowiązywać przy testowaniu.
- Python `os.walk`: ustaw `dirs[:] = [d for d in dirs if d != ".skip"]` przed iteracją plików.
- PowerShell: przed weryfikacją `db2ckbkp.exe` odfiltruj wszystkie pliki, których ścieżka zawiera segment `\.skip\` (lub `/.skip/`). Nie wystarczy wyłącznie filtr na nazwę pliku.
- Jeśli chcesz, aby liczba kopii w panelu była identyczna z liczbą plików retencji, trzeba również ujednolicić sposób liczenia w parserach n8n; dotychczasowy licznik `backup_count` może nadal filtrować po wzorcu DB2 / rozmiarze.

## Wgrywanie certyfikatów

Nowe strony: `/certificates/upload?kind=p1` oraz `/certificates/upload?kind=zus`, otwierane z przycisków w odpowiednich zakładkach.

- P1: `.p12` / `.pfx`; opcjonalnie `p.txt` z hasłem.
- ZUS: `.pfx`.
- Wyłącznie aktywni klienci z `clients_config`, do katalogów `/opt/cert/<klient>` i `/opt/lekarze/<klient>`.
- Maksymalnie 20 certyfikatów, łącznie 16 MiB. Brak nadpisywania istniejących plików.
- Pliki z uprawnieniami 0600. Upload nie uruchamia sprawdzenia; n8n sprawdzi przy kolejnym przebiegu.

**Ważne: przed użyciem ustaw hasło do uploadu i używaj wyłącznie HTTPS.** Formularz korzysta z HTTP Basic Auth; nie wystawiaj go publicznie przez zwykły HTTP. Panel nie dostaje domyślnego hasła.

Dodaj do pliku środowiskowego ładowanego przez `backup-panel.service` (np. `/etc/backup-panel.env`):

```ini
BACKUP_PANEL_UPLOAD_USER=pcmed-admin
BACKUP_PANEL_UPLOAD_PASSWORD=USTAW_DLUGIE_LOSOWE_HASLO
BACKUP_PANEL_P1_DIR=/opt/cert
BACKUP_PANEL_ZUS_DIR=/opt/lekarze
BACKUP_PANEL_UPLOAD_MAX_BYTES=16777216
```

Katalogi docelowe muszą istnieć i być zapisywalne przez użytkownika `backup-panel.service`. Nie używaj `chmod 777`. Po zmianach restartuj panel.

## Wdrożenie

```bash
cd /tmp
rm -rf backup-dashboard
git clone https://github.com/Pawel-sp9pw/backup-dashboard.git

systemctl stop backup-panel
cp -a /opt/backup-panel/backup_panel.db /opt/backup-panel/backup_panel.db.bak.$(date +%F_%H%M%S)
install -m 0750 /tmp/backup-dashboard/backup-panel/retention.py /opt/backup-panel/retention.py
cp /tmp/backup-dashboard/backup-panel/app.py /opt/backup-panel/app.py
cp /tmp/backup-dashboard/backup-panel/templates/certificate_upload.html /opt/backup-panel/templates/
cp /tmp/backup-dashboard/backup-panel/templates/certificates.html /opt/backup-panel/templates/
cp /tmp/backup-dashboard/backup-panel/templates/zus_certificates.html /opt/backup-panel/templates/

chown backup-panel:backup-panel /opt/backup-panel/app.py /opt/backup-panel/templates/*.html
python3 -m py_compile /opt/backup-panel/app.py /opt/backup-panel/retention.py
systemctl start backup-panel

sed -i 's/^PCMED_RETENTION_EXECUTE=.*/PCMED_RETENTION_EXECUTE=0/' /etc/pcmed-retention.env
sed -i 's/^PCMED_RETENTION_PURGE=.*/PCMED_RETENTION_PURGE=0/' /etc/pcmed-retention.env
systemctl start pcmed-retention.service
journalctl -u pcmed-retention.service -n 100 --no-pager
```

Bezpieczna konfiguracja wymaga zweryfikowania przypisania certyfikatów do właściwych klientów i uprawnień zapisu do katalogów `/opt/cert` i `/opt/lekarze`.
