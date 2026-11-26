# Retencja kopii klientów — PC MED

## Działanie

- Konfiguracja w **Klienci → Edytuj**: \`retention_count\` (liczba kopii) i/lub \`retention_days\` (wiek).
- \`GET /api/clients/backup\` zawiera obie wartości.
- Harmonogram: codziennie około 12:00 (\`pcmed-retention.timer\`).
- **Domyślnie raportuje, niczego nie zmienia**. Włączenie kwarantanny: \`PCMED_RETENTION_EXECUTE=1\`.
- **Trwałe usunięcie jest osobnym przełącznikiem** \`PCMED_RETENTION_PURGE=1\` i następuje najwcześniej po 14 dniach od przeniesienia do kwarantanny.
- Raport JSON Lines: \`/var/log/pcmed-retention.jsonl\` i \`journalctl -u pcmed-retention\`.

## Logika decyzji

Automat bierze wyłącznie klientów aktywnych z włączoną usługą kopii. Aby w ogóle ruszyć kopie, panel musi pokazywać status \`OK\`, w tym \`freshness_status=OK\`, a dla DB2 także pozytywny wynik odpowiedniego \`db2ckbkp\` (\`OK\`/\`CACHED_OK\`). Retencja bez skonfigurowanych wartości jest pomijana.

Jednostką retencji jest **każdy pełny plik kopii**, niezależnie od liczby podkatalogów. Skrypt skanuje rekurencyjnie /home/<klient>:

- Klienci DB2 (Linux/Windows): pliki o nazwie pasującej do wzorca \`*.DBPART000.YYYYMMDDHHMMSS.NNN\` (i analogicznych DBPARTnnn), z sufiksami \`.001\`, \`.002\` itd. **Każdy plik to odrębna pełna kopia w tym wdrożeniu PCMED** — nie są scalane jako części.
- Klienci bez DB2: każdy plik \`.zip\` to oddzielna kopia.
- Pliki poniżej 100 MiB, linki symboliczne, logi i znaczniki stanu nie są kandydatami.
- Retencja obejmuje wszystkie rozpoznane pliki klienta łącznie — również te z katalogów \`PENDRIVE\`, \`DB2_BACKUP\`, \`BACKUP_DB2\`, \`dzienne\`, \`kopie\` itd. Nie są przenoszone całe katalogi.

Przy \`retention_count=N\`: zachowuje co najmniej \`max(2,N)\` najnowszych rozpoznanych plików klienta. Przy \`retention_days=D\`: kwalifikuje kopie starsze niż D dni. Przy obu parametrach oba warunki muszą zostać spełnione. Wiek jest liczony od czasu modyfikacji pliku. Dodatkowo kandydat musi mieć co najmniej 48 godzin; w kolekcji muszą istnieć minimum 3 kopie. Jedno uruchomienie przenosi do kwarantanny najwyżej 20 plików.

Kwarantanna zachowuje ścieżkę relatywną pliku: \`/home/.pcmed-retention-quarantine/<klient>/<id>/data/<oryginalny-podkatalog>/<plik>\`. Metadane oryginalnych lokalizacji znajdują się w \`manifest.json\`.


**Uwaga:** samo znalezienie pliku nie dowodzi możliwości odtworzenia. Status DB2 w panelu dotyczy ostatniego zweryfikowanego obrazu, nie wszystkich starszych zestawów. Zanim włączysz trwałe kasowanie, potwierdź testem odtwarzania politykę backupu klienta i sprawdź dziennik decyzji.

## Instalacja na serwerze backup

\`\`\`bash
cd /tmp
rm -rf backup-dashboard
git clone https://github.com/Pawel-sp9pw/backup-dashboard.git

install -m 0750 /tmp/backup-dashboard/backup-panel/retention.py /opt/backup-panel/retention.py
install -m 0644 /tmp/backup-dashboard/backup-panel/pcmed-retention.service /etc/systemd/system/pcmed-retention.service
install -m 0644 /tmp/backup-dashboard/backup-panel/pcmed-retention.timer /etc/systemd/system/pcmed-retention.timer
install -m 0600 /tmp/backup-dashboard/backup-panel/pcmed-retention.env.example /etc/pcmed-retention.env

python3 -m py_compile /opt/backup-panel/retention.py
systemctl daemon-reload
systemctl start pcmed-retention.service
journalctl -u pcmed-retention -n 100 --no-pager
\`\`\`

Najpierw skontroluj dziennik: \`"event":"candidate"\`, \`"action":"dry_run"\`. Jeśli nie ma kandydatów, sprawdź konfigurację retencji, stan klienta oraz nazwy i lokalizacje plików.

## Aktywacja z kwarantanną

Po zweryfikowaniu raportów, w \`/etc/pcmed-retention.env\` ustaw:
\`\`\`ini
PCMED_RETENTION_EXECUTE=1
PCMED_RETENTION_PURGE=0
\`\`\`
Uruchom jednorazowo \`systemctl start pcmed-retention.service\`, sprawdź manifesty w \`/home/.pcmed-retention-quarantine/\`, przetestuj odzyskanie wybranej kopii, a następnie \`systemctl enable --now pcmed-retention.timer\`.

## Trwałe usuwanie po okresie ochronnym

Gdy proces został przetestowany i zaakceptowany, ustaw dodatkowo:
\`\`\`ini
PCMED_RETENTION_PURGE=1
PCMED_RETENTION_GRACE_DAYS=14
\`\`\`
Zestawy w kwarantannie starsze niż 14 dni, posiadające \`manifest.json\` i \`.ready\`, będą usuwane. **To jest operacja nieodwracalna**. Kwarantanna na tej samej partycji nie zwalnia miejsca do czasu purga.

## Przywrócenie z kwarantanny

Wyłącz timer: \`systemctl stop pcmed-retention.timer\`. Zlokalizuj odpowiedni \`manifest.json\` i pliki w podkatalogu \`/home/.pcmed-retention-quarantine/<klient>/<id>\`. Wykonaj \`mv\` plików/katalogów do \`/home/<klient>/\`, po sprawdzeniu, że nazwy docelowe nie istnieją. Zachowaj uprawnienia użytkownika; \`os.rename\` na tym samym filesystemie zachowuje właściciela.

## Bezpieczeństwo

- Skrypt odrzuca symlinki, brak bezpiecznej ścieżki i katalogi o nieznanych nazwach; nie dotyka \`/opt/cert\`, \`/opt/lekarze\` ani innych usług.
- Brak statusu \`OK\`, brak polityki, błąd API lub niewłaściwy token → brak przenoszenia.
- Kwarantanna musi leżeć bezpośrednio pod katalogiem backupów i być na tym samym filesystemie.
- **Nie zastępuje testu odtwarzania.** Pierwsze przebiegi wykonaj na kopiach testowych, dopiero potem produkcyjnie.
