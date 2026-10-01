#!/usr/bin/env python3
import json, os, sqlite3
from datetime import datetime, timezone
from pathlib import Path
from flask import Flask, jsonify, render_template, request, abort
APP_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get('BACKUP_PANEL_DB', APP_DIR / 'backup_panel.db'))
API_TOKEN = os.environ.get('BACKUP_PANEL_TOKEN', 'CHANGE_ME_PANEL_TOKEN')
STALE_HOURS = int(os.environ.get('BACKUP_PANEL_STALE_HOURS', '30'))
app = Flask(__name__)
OK = {'OK','CACHED_OK'}
INFO = {'SKIPPED_LINUX_DB2','CACHED_SKIPPED_LINUX_DB2','SKIPPED_NOT_DB2','CACHED_SKIPPED_NOT_DB2','SKIPPED_SYSTEM_DIR'}
WARN = {'WARNING','UNKNOWN','SKIPPED_TOO_NEW','SKIPPED_NO_BACKUP','BRAK','OLD','STALE'}
def now(): return datetime.now(timezone.utc).isoformat()
def parse_dt(v):
    if not v: return None
    try: return datetime.fromisoformat(str(v).replace('Z','+00:00'))
    except Exception: return None
def db():
    c=sqlite3.connect(DB_PATH); c.row_factory=sqlite3.Row; return c
def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with db() as c:
        c.executescript('''
CREATE TABLE IF NOT EXISTS check_results (
 id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, source TEXT NOT NULL, client TEXT NOT NULL, status TEXT NOT NULL,
 file_path TEXT, last_backup_time TEXT, backup_age_hours REAL, backup_count INTEGER, checked_at TEXT, message TEXT, raw_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS client_status (
 client TEXT PRIMARY KEY, overall_status TEXT NOT NULL DEFAULT 'UNKNOWN', freshness_status TEXT, freshness_checked_at TEXT,
 db2_linux_status TEXT, db2_linux_checked_at TEXT, db2_windows_status TEXT, db2_windows_checked_at TEXT,
 last_backup_time TEXT, backup_age_hours REAL, backup_count INTEGER, last_file_path TEXT, last_message TEXT, last_error TEXT, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_check_results_client_created ON check_results(client, created_at DESC);
''')
def require_token():
    if request.headers.get('X-Backup-Panel-Token','') != API_TOKEN: abort(401)
def norm(d):
    src=str(d.get('source') or '').strip(); client=str(d.get('client') or '').strip(); status=str(d.get('status') or '').strip().upper()
    if not src or not client or not status: raise ValueError('Wymagane pola: source, client, status')
    return {'source':src,'client':client,'status':status,'file_path':d.get('file_path') or d.get('file') or '',
            'last_backup_time':d.get('last_backup_time') or d.get('lastBackupTime'),
            'backup_age_hours':d.get('backup_age_hours') if d.get('backup_age_hours') is not None else d.get('backupAgeHours'),
            'backup_count':d.get('backup_count') if d.get('backup_count') is not None else d.get('backupCount'),
            'checked_at':d.get('checked_at') or d.get('checkedAt') or now(),'message':d.get('message') or '',
            'raw_json':json.dumps(d,ensure_ascii=False)}
def rank(s):
    if not s: return 1
    s=s.upper()
    if s=='ERROR': return 3
    if s in WARN: return 2
    if s in OK or s in INFO or s.startswith('CACHED_'): return 0
    return 1
def overall(row):
    statuses=[row['freshness_status'],row['db2_linux_status'],row['db2_windows_status']]
    if any(rank(s)==3 for s in statuses) or row['freshness_status'] in ('BRAK','OLD','ERROR'): return 'ERROR'
    checked=[parse_dt(row['freshness_checked_at']),parse_dt(row['db2_linux_checked_at']),parse_dt(row['db2_windows_checked_at'])]
    checked=[x for x in checked if x]
    if checked:
        last=max(checked); age=(datetime.now(last.tzinfo or timezone.utc)-last).total_seconds()/3600
        if age>STALE_HOURS: return 'STALE'
    if any(rank(s)==2 for s in statuses): return 'WARNING'
    if not any(statuses): return 'UNKNOWN'
    return 'OK'
def save_item(item):
    with db() as c:
        c.execute('''INSERT INTO check_results (created_at,source,client,status,file_path,last_backup_time,backup_age_hours,backup_count,checked_at,message,raw_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)''', (now(),item['source'],item['client'],item['status'],item['file_path'],item['last_backup_time'],item['backup_age_hours'],item['backup_count'],item['checked_at'],item['message'],item['raw_json']))
        if not c.execute('SELECT client FROM client_status WHERE client=?',(item['client'],)).fetchone():
            c.execute('INSERT INTO client_status (client, overall_status, updated_at) VALUES (?,"UNKNOWN",?)',(item['client'],now()))
        fields={'last_message':item['message'],'updated_at':now()}
        if item['file_path']: fields['last_file_path']=item['file_path']
        if item['last_backup_time']: fields['last_backup_time']=item['last_backup_time']
        if item['backup_age_hours'] is not None: fields['backup_age_hours']=item['backup_age_hours']
        if item['backup_count'] is not None: fields['backup_count']=item['backup_count']
        if item['status']=='ERROR': fields['last_error']=item['message'] or 'ERROR'
        if item['source']=='freshness': fields.update({'freshness_status':item['status'],'freshness_checked_at':item['checked_at']})
        elif item['source']=='db2_linux': fields.update({'db2_linux_status':item['status'],'db2_linux_checked_at':item['checked_at']})
        elif item['source']=='db2_windows': fields.update({'db2_windows_status':item['status'],'db2_windows_checked_at':item['checked_at']})
        c.execute('UPDATE client_status SET '+','.join(f'{k}=?' for k in fields)+' WHERE client=?', list(fields.values())+[item['client']])
        row=c.execute('SELECT * FROM client_status WHERE client=?',(item['client'],)).fetchone()
        c.execute('UPDATE client_status SET overall_status=?, updated_at=? WHERE client=?',(overall(row),now(),item['client']))
@app.route('/api/check-result', methods=['POST'])
def check_result():
    require_token(); data=request.get_json(force=True)
    rows=data if isinstance(data,list) else [data]
    accepted=[]
    for r in rows:
        item=norm(r); save_item(item); accepted.append({'client':item['client'],'source':item['source'],'status':item['status']})
    return jsonify({'ok':True,'accepted':accepted})
@app.route('/api/status')
def api_status():
    with db() as c: clients=[dict(r) for r in c.execute('SELECT * FROM client_status ORDER BY client COLLATE NOCASE')]
    return jsonify({'generatedAt':now(),'summary':summarize(clients),'clients':clients})
@app.route('/api/history/<client>')
def api_history(client):
    limit=int(request.args.get('limit','50'))
    with db() as c: rows=[dict(r) for r in c.execute('SELECT * FROM check_results WHERE client=? ORDER BY created_at DESC LIMIT ?',(client,limit))]
    return jsonify({'client':client,'results':rows})
def summarize(clients):
    s={'total':len(clients),'OK':0,'WARNING':0,'ERROR':0,'STALE':0,'UNKNOWN':0}
    for c in clients: s[c.get('overall_status') or 'UNKNOWN']=s.get(c.get('overall_status') or 'UNKNOWN',0)+1
    return s
@app.route('/')
def index():
    with db() as c: clients=[dict(r) for r in c.execute('SELECT * FROM client_status ORDER BY client COLLATE NOCASE')]
    return render_template('index.html', clients=clients, summary=summarize(clients), generated_at=now())
if __name__=='__main__': init_db(); app.run(host='0.0.0.0', port=int(os.environ.get('BACKUP_PANEL_PORT','8080')))
