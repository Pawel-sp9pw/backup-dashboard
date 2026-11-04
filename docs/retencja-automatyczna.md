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

Jednostka retencyjna jest **całym zestawem kopii**, nie dowolnym plikiem:

- Bezpośrednie katalogi klienta o nazwie rozpoczynającej się \`kopie \`, \`kopie_\`, \`backup \`, \`backup_\` są traktowane jako zestawy. Dla katalogu używany jest najnowszy czas modyfikacji pliku znajdującego się wewnątrz.
- Pliki DB2 bezpośrednio w \`/home/<klient>\`, których nazwa zawiera \`DB2\`, \`mediqus\`, \`DBPART000\` itp., mogą być traktowane jako zestawy. Części \`.001\`, \`.002\` itd. w rozpoznanej rodzinie \`DBPART\` są przesuwane razem.
- Pozostałe, nieznane struktury (np. pliki umieszczone wewnątrz katalogu \`mediqus\`, luźne ZIP-y bez jednoznacznej jednostki retencyjnej) **nie są usuwane**. Trzeba je najpierw jawnie obsłużyć i przetestować.

Przy \`retention_count=N\`: zachowuje co najmniej \`max(2,N)\` **najnowszych zestawów**. Przy \`retention_days=D\`: kwalifikuje zestawy starsze niż D dni. Jeśli oba parametry podano, obydwa warunki muszą być spełnione. Dodatkowo zestaw musi mieć minimum 48 godzin i łącznie muszą istnieć co najmniej 3 zestawy. Jedno uruchomienie przenosi najwyżej 20 zestawów (limit regulowany).

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
