# Zmiany w workflowach n8n

Dashboard przyjmuje `POST http://172.20.0.2:8080/api/check-result` z nagłówkiem `X-Backup-Panel-Token`.

## DB2 Windows
Po node SSH, który uruchamia `C:\PCMED\db2-windows-check.ps1`, dodaj Code node:

```javascript
const stdout = $json.stdout || '';
const checkedAt = new Date().toISOString();
return stdout.split(/\r?\n/)
  .filter(line => line.startsWith('RESULT|'))
  .map(line => {
    const p = line.split('|');
    return { json: { source: 'db2_windows', client: p[2] || '__system__', status: p[1] || 'UNKNOWN', file_path: p[3] || '', checked_at: checkedAt, message: p.slice(4).join('|') || '' } };
  });
```

Następnie HTTP Request: Method `POST`, URL `http://172.20.0.2:8080/api/check-result`, header `X-Backup-Panel-Token`, JSON body `={{ $json }}`.

## DB2 Linux
Użyj tego samego parsera, zmień tylko `source` na `db2_linux`.

## Świeżość kopii /home
Na końcu parser powinien zwrócić itemy w formacie:

```javascript
return items.map(item => ({ json: {
  source: 'freshness',
  client: item.json.client,
  status: item.json.status,
  file_path: item.json.latestFile || '',
  last_backup_time: item.json.lastBackupTime || null,
  backup_age_hours: item.json.backupAgeHours ?? null,
  backup_count: item.json.backupCount ?? null,
  checked_at: new Date().toISOString(),
  message: item.json.message || ''
}}));
```

HTTP Request jak wyżej. Status ogólny liczy dashboard, nie n8n.
