#!/usr/bin/env python3
import json, os, sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from flask import Flask, abort, jsonify, redirect, render_template, request, url_for

APP_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get('BACKUP_PANEL_DB', APP_DIR / 'backup_panel.db'))
API_TOKEN = os.environ.get('BACKUP_PANEL_TOKEN', 'CHANGE_ME_PANEL_TOKEN')
HISTORY_DAYS = int(os.environ.get('BACKUP_PANEL_HISTORY_DAYS', '90'))
CERT_WARN_DAYS = int(os.environ.get('BACKUP_PANEL_CERT_WARN_DAYS', '30'))
ZUS_WARN_DAYS = int(os.environ.get('BACKUP_PANEL_ZUS_WARN_DAYS', str(CERT_WARN_DAYS)))
STALE_HOURS = int(os.environ.get('BACKUP_PANEL_STALE_HOURS', '30'))
app = Flask(__name__)

DEFAULT_CLIENTS = [('vena','linux'),('bes','windows'),('etos','windows'),('galena','windows'),('iwaniuk','windows'),('novo-med-klobuck','windows'),('novo-med-miedzno','windows'),('novo-med-panki','windows'),('novo-med-popow','windows'),('nowinski','windows'),('pulsmed','windows'),('salomon','windows'),('kulej','none'),('jagielska','none')]
OK = {'OK','CACHED_OK'}
INFO = {'SKIPPED_LINUX_DB2','CACHED_SKIPPED_LINUX_DB2','SKIPPED_WINDOWS_DB2','CACHED_SKIPPED_WINDOWS_DB2','SKIPPED_NOT_DB2','CACHED_SKIPPED_NOT_DB2','NOT_APPLICABLE','CACHED_NOT_APPLICABLE','SKIPPED_SYSTEM_DIR'}
WARN = {'WARNING','UNKNOWN','SKIPPED_TOO_NEW','NO_DB2_BACKUP','CACHED_NO_DB2_BACKUP'}
ERR = {'ERROR','BRAK','OLD','SKIPPED_NO_BACKUP'}
SYSTEM_CLIENTS = {'db2inst1','__system__'}

def now():
    return datetime.now(timezone.utc).isoformat()

def db():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c

def cols(conn, table):
    try:
        return {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}
    except Exception:
        return set()

def addcol(conn, table, column, ddl):
    if column not in cols(conn, table):
        conn.execute(f'ALTER TABLE {table} ADD COLUMN {column} {ddl}')

def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with db() as conn:
        conn.executescript('''
CREATE TABLE IF NOT EXISTS check_results(id INTEGER PRIMARY KEY AUTOINCREMENT,created_at TEXT NOT NULL,source TEXT NOT NULL,client TEXT NOT NULL,status TEXT NOT NULL,file_path TEXT,last_backup_time TEXT,backup_age_hours REAL,backup_count INTEGER,checked_at TEXT,message TEXT,raw_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS client_status(client TEXT PRIMARY KEY,overall_status TEXT NOT NULL DEFAULT 'UNKNOWN',freshness_status TEXT,freshness_checked_at TEXT,db2_linux_status TEXT,db2_linux_checked_at TEXT,db2_windows_status TEXT,db2_windows_checked_at TEXT,last_backup_time TEXT,backup_age_hours REAL,backup_count INTEGER,last_file_path TEXT,last_message TEXT,last_error TEXT,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS client_notifications(client TEXT PRIMARY KEY,enabled INTEGER NOT NULL DEFAULT 0,email TEXT,notify_error INTEGER NOT NULL DEFAULT 1,notify_warning INTEGER NOT NULL DEFAULT 0,notify_stale INTEGER NOT NULL DEFAULT 0,last_notified_key TEXT,last_notified_at TEXT,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS notification_events(id INTEGER PRIMARY KEY AUTOINCREMENT,created_at TEXT NOT NULL,client TEXT NOT NULL,email TEXT NOT NULL,status TEXT NOT NULL,problem_key TEXT NOT NULL,subject TEXT NOT NULL,body TEXT NOT NULL,send_status TEXT NOT NULL,error TEXT);
CREATE TABLE IF NOT EXISTS cert_results(id INTEGER PRIMARY KEY AUTOINCREMENT,created_at TEXT NOT NULL,client TEXT NOT NULL,cert_file TEXT,cert_path TEXT,status TEXT NOT NULL,cert_status TEXT NOT NULL,valid_from TEXT,valid_to TEXT,days_left INTEGER,should_alert INTEGER,serial_number TEXT,issuer TEXT,subject TEXT,message TEXT,raw_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cert_status(client TEXT NOT NULL,cert_file TEXT NOT NULL,cert_path TEXT,status TEXT NOT NULL,cert_status TEXT NOT NULL,valid_from TEXT,valid_to TEXT,days_left INTEGER,should_alert INTEGER,serial_number TEXT,issuer TEXT,subject TEXT,message TEXT,checked_at TEXT NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(client,cert_file));
CREATE TABLE IF NOT EXISTS zus_cert_results(id INTEGER PRIMARY KEY AUTOINCREMENT,created_at TEXT NOT NULL,client TEXT NOT NULL,cert_file TEXT,cert_path TEXT,doctor_name TEXT,status TEXT NOT NULL,cert_status TEXT NOT NULL,generated_at TEXT,valid_to TEXT,days_left INTEGER,should_alert INTEGER,message TEXT,raw_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS zus_cert_status(client TEXT NOT NULL,cert_file TEXT NOT NULL,cert_path TEXT,doctor_name TEXT,status TEXT NOT NULL,cert_status TEXT NOT NULL,generated_at TEXT,valid_to TEXT,days_left INTEGER,should_alert INTEGER,message TEXT,checked_at TEXT NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(client,cert_file));
CREATE TABLE IF NOT EXISTS clients_config(client TEXT PRIMARY KEY,display_name TEXT,active INTEGER NOT NULL DEFAULT 1,backup_enabled INTEGER NOT NULL DEFAULT 1,db2_type TEXT NOT NULL DEFAULT 'none',cert_p1_enabled INTEGER NOT NULL DEFAULT 0,notification_email TEXT,notes TEXT,updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_check_results_client_created ON check_results(client,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_cert_results_client_created ON cert_results(client,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_zus_cert_results_client_created ON zus_cert_results(client,created_at DESC);
''')
        for c in ('freshness_message','db2_linux_message','db2_windows_message','freshness_file_path','db2_linux_file_path','db2_windows_file_path'):
            addcol(conn, 'client_status', c, 'TEXT')
        ensure_clients(conn)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=HISTORY_DAYS)).isoformat()
        for t in ('check_results','notification_events','cert_results','zus_cert_results'):
            try: conn.execute(f'DELETE FROM {t} WHERE created_at < ?', (cutoff,))
            except Exception: pass

def ensure_clients(conn):
    for client, db2_type in DEFAULT_CLIENTS:
        conn.execute("INSERT INTO clients_config(client,display_name,active,backup_enabled,db2_type,cert_p1_enabled,notification_email,notes,updated_at) VALUES(?,?,1,1,?,0,'','',?) ON CONFLICT(client) DO NOTHING", (client, client, db2_type, now()))
    for table in ('client_status','cert_status','zus_cert_status'):
        try: rows = conn.execute(f"SELECT DISTINCT client FROM {table} WHERE client NOT IN ('db2inst1','__system__')").fetchall()
        except Exception: rows = []
        for r in rows:
            if r['client']:
                conn.execute("INSERT INTO clients_config(client,display_name,active,backup_enabled,db2_type,cert_p1_enabled,notification_email,notes,updated_at) VALUES(?,?,1,1,'none',0,'','',?) ON CONFLICT(client) DO NOTHING", (r['client'], r['client'], now()))

def require_token():
    if request.headers.get('X-Backup-Panel-Token','') != API_TOKEN:
        abort(401)

def as_int(v):
    try: return int(v) if v not in (None, '') else None
    except Exception: return None

def cert_overall(item, warn_days):
    if item['status'] in ('error','parse_error'):
        return 'ERROR'
    if item['days_left'] is None:
        return 'UNKNOWN'
    if item['days_left'] < 0:
        return 'ERROR'
    if item['days_left'] <= warn_days:
        return 'WARNING'
    return 'OK'

def rank(s):
    s = str(s or '').upper()
    if s in ERR: return 3
    if s in WARN: return 2
    if s in OK or s in INFO or s.startswith('CACHED_'): return 0
    return 1 if s else 0

def parse_dt(v):
    try: return datetime.fromisoformat(str(v).replace('Z','+00:00')) if v else None
    except Exception: return None

def combined_db2(row):
    l, w = row.get('db2_linux_status'), row.get('db2_windows_status')
    if rank(l) == 3: return l
    if rank(w) == 3: return w
    if l in ('OK','CACHED_OK'): return l
    if w in ('OK','CACHED_OK'): return w
    return l or w or '-'

def calc_overall(row):
    f = row.get('freshness_status')
    sts = [f, row.get('db2_linux_status'), row.get('db2_windows_status')]
    if f in ('BRAK','OLD','ERROR','NO_DB2_BACKUP','SKIPPED_NO_BACKUP') or any(rank(s)==3 for s in sts): return 'ERROR'
    checked = [parse_dt(row.get('freshness_checked_at')), parse_dt(row.get('db2_linux_checked_at')), parse_dt(row.get('db2_windows_checked_at'))]
    checked = [x for x in checked if x]
    if checked and (datetime.now(checked[0].tzinfo or timezone.utc) - max(checked)).total_seconds()/3600 > STALE_HOURS: return 'STALE'
    if any(rank(s)==2 for s in sts): return 'WARNING'
    return 'OK' if any(sts) else 'UNKNOWN'

def short(m, n=500):
    m = ' '.join(str(m or '').split())
    return m[:n-3]+'...' if len(m) > n else m

def display_message(row):
    if row.get('overall_status') == 'ERROR':
        if row.get('freshness_status') in ('BRAK','OLD','ERROR','NO_DB2_BACKUP','SKIPPED_NO_BACKUP') and row.get('freshness_message'): return short(row['freshness_message'])
        if rank(row.get('db2_linux_status')) == 3 and row.get('db2_linux_message'): return short(row['db2_linux_message'])
        if rank(row.get('db2_windows_status')) == 3 and row.get('db2_windows_message'): return short(row['db2_windows_message'])
        return short(row.get('last_error') or 'ERROR')
    return ''

def decorate(row):
    db2 = str(row.get('db2_type') or '').lower()
    l, w = str(row.get('db2_linux_status') or '').upper(), str(row.get('db2_windows_status') or '').upper()
    if db2 == 'linux' or l in ('OK','CACHED_OK'): icon, label = '🐧', 'Linux DB2'
    elif db2 == 'windows' or w in ('OK','CACHED_OK') or l in ('SKIPPED_WINDOWS_DB2','CACHED_SKIPPED_WINDOWS_DB2'): icon, label = '🪟', 'Windows DB2'
    elif db2 == 'none': icon, label = '📦', 'Inny system / bez DB2'
    else: icon, label = '–', 'Nieustalony'
    row['backup_system_icon'] = icon; row['backup_system_label'] = label; row['db2_status'] = combined_db2(row); row['display_message'] = display_message(row)
    row['backup_age_days'] = round(float(row['backup_age_hours'])/24, 1) if row.get('backup_age_hours') is not None else None
    return row

def notification_config(conn, client):
    cfg = conn.execute('SELECT * FROM client_notifications WHERE client=?', (client,)).fetchone()
    if not cfg:
        conn.execute('INSERT INTO client_notifications(client,enabled,updated_at) VALUES(?,0,?)', (client, now()))
        cfg = conn.execute('SELECT * FROM client_notifications WHERE client=?', (client,)).fetchone()
    return cfg

def norm(payload):
    return {'source':str(payload.get('source') or '').strip(),'client':str(payload.get('client') or '').strip(),'status':str(payload.get('status') or '').strip().upper(),'file_path':payload.get('file_path') or payload.get('file') or '','last_backup_time':payload.get('last_backup_time') or payload.get('lastBackupTime'),'backup_age_hours':payload.get('backup_age_hours') if payload.get('backup_age_hours') is not None else payload.get('backupAgeHours'),'backup_count':payload.get('backup_count') if payload.get('backup_count') is not None else payload.get('backupCount'),'checked_at':payload.get('checked_at') or payload.get('checkedAt') or now(),'message':payload.get('message') or '','raw_json':json.dumps(payload,ensure_ascii=False)}

def norm_cert(p):
    return {'client':str(p.get('client') or p.get('klient') or '').strip() or '__system__','cert_file':str(p.get('cert_file') or p.get('certyfikat') or '').strip() or '-','cert_path':p.get('cert_path') or p.get('sciezka') or '','status':str(p.get('status') or 'unknown').lower(),'valid_from':p.get('valid_from') or p.get('wazny_od') or '','valid_to':p.get('valid_to') or p.get('wazny_do') or '','days_left':as_int(p.get('days_left') if p.get('days_left') is not None else p.get('dni_do_wygasniecia')),'should_alert':bool(p.get('should_alert') if p.get('should_alert') is not None else p.get('alert_30_dni')),'serial_number':p.get('serial_number') or p.get('numer_seryjny') or '','issuer':p.get('issuer') or '','subject':p.get('subject') or '','message':p.get('error') or p.get('blad') or p.get('message') or '','raw_json':json.dumps(p,ensure_ascii=False)}

def norm_zus(p):
    return {'client':str(p.get('client') or p.get('klient') or '').strip() or '__system__','cert_file':str(p.get('cert_file') or p.get('certyfikat') or '').strip() or '-','cert_path':p.get('cert_path') or p.get('sciezka') or '','doctor_name':p.get('doctor_name') or p.get('lekarz') or '','status':str(p.get('status') or 'unknown').lower(),'generated_at':p.get('generated_at') or p.get('wygenerowany') or '','valid_to':p.get('valid_to') or p.get('wazny_do') or '','days_left':as_int(p.get('days_left') if p.get('days_left') is not None else p.get('dni_do_wygasniecia')),'should_alert':bool(p.get('should_alert') if p.get('should_alert') is not None else p.get('alert_30_dni')),'message':p.get('error') or p.get('blad') or p.get('message') or '','raw_json':json.dumps(p,ensure_ascii=False)}

def save_item(item):
    if item['client'] in SYSTEM_CLIENTS or item['status'] == 'SKIPPED_SYSTEM_DIR': return 'ignored'
    with db() as conn:
        conn.execute('INSERT INTO check_results(created_at,source,client,status,file_path,last_backup_time,backup_age_hours,backup_count,checked_at,message,raw_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(now(),item['source'],item['client'],item['status'],item['file_path'],item['last_backup_time'],item['backup_age_hours'],item['backup_count'],item['checked_at'],item['message'],item['raw_json']))
        conn.execute("INSERT INTO client_status(client,overall_status,updated_at) VALUES(?,'UNKNOWN',?) ON CONFLICT(client) DO NOTHING", (item['client'], now()))
        conn.execute("INSERT INTO clients_config(client,display_name,active,backup_enabled,db2_type,cert_p1_enabled,notification_email,notes,updated_at) VALUES(?,?,1,1,'none',0,'','',?) ON CONFLICT(client) DO NOTHING", (item['client'], item['client'], now()))
        fields = {'updated_at':now()}
        if item['file_path']: fields['last_file_path'] = item['file_path']
        if item['last_backup_time']: fields['last_backup_time'] = item['last_backup_time']
        if item['backup_age_hours'] is not None: fields['backup_age_hours'] = item['backup_age_hours']
        if item['backup_count'] is not None: fields['backup_count'] = item['backup_count']
        if item['status'] == 'ERROR': fields['last_error'] = item['message'] or 'ERROR'
        if item['source'] == 'freshness': fields.update({'freshness_status':item['status'],'freshness_checked_at':item['checked_at'],'freshness_message':item['message'],'freshness_file_path':item['file_path']})
        if item['source'] == 'db2_linux': fields.update({'db2_linux_status':item['status'],'db2_linux_checked_at':item['checked_at'],'db2_linux_message':item['message'],'db2_linux_file_path':item['file_path']})
        if item['source'] == 'db2_windows': fields.update({'db2_windows_status':item['status'],'db2_windows_checked_at':item['checked_at'],'db2_windows_message':item['message'],'db2_windows_file_path':item['file_path']})
        conn.execute('UPDATE client_status SET '+','.join(f'{k}=?' for k in fields)+' WHERE client=?', list(fields.values())+[item['client']])
        row = dict(conn.execute('SELECT * FROM client_status WHERE client=?',(item['client'],)).fetchone()); row['overall_status'] = calc_overall(row)
        conn.execute('UPDATE client_status SET overall_status=?,last_message=?,last_error=CASE WHEN ?="OK" THEN NULL ELSE last_error END,updated_at=? WHERE client=?',(row['overall_status'],display_message(row),row['overall_status'],now(),item['client']))
    return 'accepted'

def save_cert(item):
    cs = cert_overall(item, CERT_WARN_DAYS)
    with db() as conn:
        conn.execute('INSERT INTO cert_results(created_at,client,cert_file,cert_path,status,cert_status,valid_from,valid_to,days_left,should_alert,serial_number,issuer,subject,message,raw_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(now(),item['client'],item['cert_file'],item['cert_path'],item['status'],cs,item['valid_from'],item['valid_to'],item['days_left'],1 if item['should_alert'] else 0,item['serial_number'],item['issuer'],item['subject'],item['message'],item['raw_json']))
        conn.execute('INSERT INTO cert_status(client,cert_file,cert_path,status,cert_status,valid_from,valid_to,days_left,should_alert,serial_number,issuer,subject,message,checked_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(client,cert_file) DO UPDATE SET cert_path=excluded.cert_path,status=excluded.status,cert_status=excluded.cert_status,valid_from=excluded.valid_from,valid_to=excluded.valid_to,days_left=excluded.days_left,should_alert=excluded.should_alert,serial_number=excluded.serial_number,issuer=excluded.issuer,subject=excluded.subject,message=excluded.message,checked_at=excluded.checked_at,updated_at=excluded.updated_at',(item['client'],item['cert_file'],item['cert_path'],item['status'],cs,item['valid_from'],item['valid_to'],item['days_left'],1 if item['should_alert'] else 0,item['serial_number'],item['issuer'],item['subject'],item['message'],now(),now()))
        conn.execute("INSERT INTO clients_config(client,display_name,active,backup_enabled,db2_type,cert_p1_enabled,notification_email,notes,updated_at) VALUES(?,?,1,1,'none',1,'','',?) ON CONFLICT(client) DO NOTHING", (item['client'], item['client'], now()))
    return {'client':item['client'],'cert_file':item['cert_file'],'cert_status':cs}

def save_zus(item):
    cs = cert_overall(item, ZUS_WARN_DAYS)
    with db() as conn:
        conn.execute('INSERT INTO zus_cert_results(created_at,client,cert_file,cert_path,doctor_name,status,cert_status,generated_at,valid_to,days_left,should_alert,message,raw_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(now(),item['client'],item['cert_file'],item['cert_path'],item['doctor_name'],item['status'],cs,item['generated_at'],item['valid_to'],item['days_left'],1 if item['should_alert'] else 0,item['message'],item['raw_json']))
        conn.execute('INSERT INTO zus_cert_status(client,cert_file,cert_path,doctor_name,status,cert_status,generated_at,valid_to,days_left,should_alert,message,checked_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(client,cert_file) DO UPDATE SET cert_path=excluded.cert_path,doctor_name=excluded.doctor_name,status=excluded.status,cert_status=excluded.cert_status,generated_at=excluded.generated_at,valid_to=excluded.valid_to,days_left=excluded.days_left,should_alert=excluded.should_alert,message=excluded.message,checked_at=excluded.checked_at,updated_at=excluded.updated_at',(item['client'],item['cert_file'],item['cert_path'],item['doctor_name'],item['status'],cs,item['generated_at'],item['valid_to'],item['days_left'],1 if item['should_alert'] else 0,item['message'],now(),now()))
        conn.execute("INSERT INTO clients_config(client,display_name,active,backup_enabled,db2_type,cert_p1_enabled,notification_email,notes,updated_at) VALUES(?,?,1,1,'none',0,'','',?) ON CONFLICT(client) DO NOTHING", (item['client'], item['client'], now()))
    return {'client':item['client'],'cert_file':item['cert_file'],'cert_status':cs}

def refresh_statuses():
    with db() as conn:
        for r in conn.execute('SELECT * FROM client_status').fetchall():
            row = dict(r); row['overall_status'] = calc_overall(row)
            conn.execute('UPDATE client_status SET overall_status=?,last_message=?,last_error=CASE WHEN ?="OK" THEN NULL ELSE last_error END WHERE client=?',(row['overall_status'],display_message(row),row['overall_status'],row['client']))

def empty_status(client):
    keys = ['client','overall_status','freshness_status','freshness_checked_at','db2_linux_status','db2_linux_checked_at','db2_windows_status','db2_windows_checked_at','last_backup_time','backup_age_hours','backup_count','last_file_path','last_message','last_error','updated_at','freshness_message','db2_linux_message','db2_windows_message','freshness_file_path','db2_linux_file_path','db2_windows_file_path']
    d = {k:None for k in keys}; d.update({'client':client,'overall_status':'UNKNOWN','updated_at':'-'})
    return d

def fetch_clients(include_inactive=False):
    refresh_statuses()
    with db() as conn:
        ensure_clients(conn)
        cfgs = [dict(r) for r in conn.execute('SELECT * FROM clients_config '+('' if include_inactive else 'WHERE active=1')+' ORDER BY client COLLATE NOCASE')]
        sts = {r['client']:dict(r) for r in conn.execute('SELECT * FROM client_status')}
    out = []
    for cfg in cfgs:
        row = empty_status(cfg['client']); row.update(sts.get(cfg['client'], {})); row.update(cfg); out.append(decorate(row))
    return [r for r in out if r['client'] not in SYSTEM_CLIENTS]

def summary(rows):
    s = {'total':len(rows),'OK':0,'WARNING':0,'ERROR':0,'STALE':0,'UNKNOWN':0}
    for r in rows: s[r.get('overall_status') or 'UNKNOWN'] = s.get(r.get('overall_status') or 'UNKNOWN', 0) + 1
    return s

def cert_summary(rows):
    s = {'total':len(rows),'OK':0,'WARNING':0,'ERROR':0,'UNKNOWN':0}
    for r in rows: s[r.get('cert_status') or 'UNKNOWN'] = s.get(r.get('cert_status') or 'UNKNOWN', 0) + 1
    return s

def service_clients(service):
    where = {'backup':'active=1 AND backup_enabled=1','db2-linux':"active=1 AND backup_enabled=1 AND db2_type='linux'",'db2-windows':"active=1 AND backup_enabled=1 AND db2_type='windows'",'cert-p1':'active=1 AND cert_p1_enabled=1'}.get(service, 'active=1')
    with db() as conn:
        ensure_clients(conn)
        return [dict(r) for r in conn.execute(f'SELECT * FROM clients_config WHERE {where} ORDER BY client COLLATE NOCASE')]

@app.route('/api/check-result', methods=['POST'])
def check_result():
    require_token(); data = request.get_json(force=True); rows = data if isinstance(data, list) else [data]
    acc, ign = [], []
    for p in rows:
        it = norm(p); res = save_item(it); (ign if res == 'ignored' else acc).append({'client':it['client'],'source':it['source'],'status':it['status']})
    return jsonify({'ok':True,'accepted':acc,'ignored':ign})
@app.route('/api/cert-result', methods=['POST'])
def cert_result():
    require_token(); data = request.get_json(force=True); rows = data if isinstance(data, list) else [data]
    return jsonify({'ok':True,'accepted':[save_cert(norm_cert(r)) for r in rows]})
@app.route('/api/zus-cert-result', methods=['POST'])
def zus_cert_result():
    require_token(); data = request.get_json(force=True); rows = data if isinstance(data, list) else [data]
    return jsonify({'ok':True,'accepted':[save_zus(norm_zus(r)) for r in rows]})
@app.route('/api/status')
def api_status():
    c = fetch_clients(); return jsonify({'generatedAt':now(),'historyDays':HISTORY_DAYS,'summary':summary(c),'clients':c})
@app.route('/api/clients')
def api_clients_all():
    require_token(); items = service_clients('all'); return jsonify({'service':'all','count':len(items),'clients':[i['client'] for i in items],'items':items})
@app.route('/api/clients/<service>')
def api_clients_service(service):
    require_token(); items = service_clients(service); return jsonify({'service':service,'count':len(items),'clients':[i['client'] for i in items],'items':items})
@app.route('/api/certificates')
def api_certificates():
    with db() as conn: rows = [dict(r) for r in conn.execute('SELECT cs.* FROM cert_status cs LEFT JOIN clients_config cc ON cc.client=cs.client WHERE COALESCE(cc.active,1)=1 ORDER BY cert_status DESC, days_left ASC, cs.client COLLATE NOCASE')]
    return jsonify({'generatedAt':now(),'warningDays':CERT_WARN_DAYS,'summary':cert_summary(rows),'certificates':rows})
@app.route('/api/zus-certificates')
def api_zus_certificates():
    with db() as conn: rows = [dict(r) for r in conn.execute('SELECT zs.* FROM zus_cert_status zs LEFT JOIN clients_config cc ON cc.client=zs.client WHERE COALESCE(cc.active,1)=1 ORDER BY cert_status DESC, days_left ASC, zs.client COLLATE NOCASE')]
    return jsonify({'generatedAt':now(),'warningDays':ZUS_WARN_DAYS,'summary':cert_summary(rows),'certificates':rows})
@app.route('/api/history/<client>')
def api_history(client):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=HISTORY_DAYS)).isoformat()
    with db() as conn: rows = [dict(r) for r in conn.execute('SELECT * FROM check_results WHERE client=? AND created_at>=? ORDER BY created_at DESC',(client, cutoff))]
    return jsonify({'client':client,'historyDays':HISTORY_DAYS,'results':rows})
@app.route('/')
def index():
    c = fetch_clients(); return render_template('index.html', clients=c, summary=summary(c), generated_at=now())
@app.route('/certificates')
def certificates():
    with db() as conn: rows = [dict(r) for r in conn.execute("SELECT cs.* FROM cert_status cs LEFT JOIN clients_config cc ON cc.client=cs.client WHERE COALESCE(cc.active,1)=1 ORDER BY CASE cert_status WHEN 'ERROR' THEN 0 WHEN 'WARNING' THEN 1 WHEN 'UNKNOWN' THEN 2 ELSE 3 END, days_left ASC, cs.client COLLATE NOCASE")]
    return render_template('certificates.html', certificates=rows, summary=cert_summary(rows), generated_at=now(), warning_days=CERT_WARN_DAYS)
@app.route('/zus-certificates')
def zus_certificates():
    with db() as conn: rows = [dict(r) for r in conn.execute("SELECT zs.* FROM zus_cert_status zs LEFT JOIN clients_config cc ON cc.client=zs.client WHERE COALESCE(cc.active,1)=1 ORDER BY CASE cert_status WHEN 'ERROR' THEN 0 WHEN 'WARNING' THEN 1 WHEN 'UNKNOWN' THEN 2 ELSE 3 END, days_left ASC, zs.client COLLATE NOCASE")]
    return render_template('zus_certificates.html', certificates=rows, summary=cert_summary(rows), generated_at=now(), warning_days=ZUS_WARN_DAYS)
@app.route('/clients')
def clients_config():
    return render_template('clients.html', clients=fetch_clients(include_inactive=True), generated_at=now())
@app.route('/clients/new', methods=['GET','POST'])
def client_config_new():
    return client_config_edit('__new__')
@app.route('/clients/<client>/edit', methods=['GET','POST'])
def client_config_edit(client):
    with db() as conn:
        if request.method == 'POST':
            old = client if client != '__new__' else request.form.get('client','').strip(); new = request.form.get('client','').strip()
            if not new: abort(400)
            db2 = request.form.get('db2_type','none'); db2 = db2 if db2 in {'none','linux','windows'} else 'none'
            vals = (new, request.form.get('display_name', new).strip() or new, 1 if request.form.get('active') == 'on' else 0, 1 if request.form.get('backup_enabled') == 'on' else 0, db2, 1 if request.form.get('cert_p1_enabled') == 'on' else 0, request.form.get('notification_email','').strip(), request.form.get('notes','').strip(), now())
            if client != '__new__' and new != old: conn.execute('DELETE FROM clients_config WHERE client=?', (old,))
            conn.execute('INSERT INTO clients_config(client,display_name,active,backup_enabled,db2_type,cert_p1_enabled,notification_email,notes,updated_at) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(client) DO UPDATE SET display_name=excluded.display_name,active=excluded.active,backup_enabled=excluded.backup_enabled,db2_type=excluded.db2_type,cert_p1_enabled=excluded.cert_p1_enabled,notification_email=excluded.notification_email,notes=excluded.notes,updated_at=excluded.updated_at', vals)
            return redirect(url_for('clients_config'))
        cfg = {'client':'','display_name':'','active':1,'backup_enabled':1,'db2_type':'none','cert_p1_enabled':0,'notification_email':'','notes':''} if client == '__new__' else dict(conn.execute('SELECT * FROM clients_config WHERE client=?',(client,)).fetchone() or abort(404))
    return render_template('client_config_form.html', client=client, cfg=cfg)
@app.route('/client/<client>')
def client_report(client):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=HISTORY_DAYS)).isoformat()
    with db() as conn:
        row = conn.execute('SELECT cs.*, cc.db2_type, cc.display_name FROM client_status cs LEFT JOIN clients_config cc ON cc.client=cs.client WHERE cs.client=?',(client,)).fetchone()
        if not row: abort(404)
        results = [dict(r) for r in conn.execute('SELECT * FROM check_results WHERE client=? AND created_at>=? ORDER BY created_at DESC',(client, cutoff))]
        certs = [dict(r) for r in conn.execute('SELECT * FROM cert_status WHERE client=? ORDER BY days_left ASC',(client,))]
        zus_certs = [dict(r) for r in conn.execute('SELECT * FROM zus_cert_status WHERE client=? ORDER BY days_left ASC',(client,))]
        notifications = [dict(r) for r in conn.execute('SELECT * FROM notification_events WHERE client=? AND created_at>=? ORDER BY created_at DESC LIMIT 50',(client, cutoff))]
        cfg = dict(notification_config(conn, client))
    return render_template('client.html', client=client, status=decorate(dict(row)), results=results, certs=certs, zus_certs=zus_certs, notifications=notifications, cfg=cfg, history_days=HISTORY_DAYS, generated_at=now())
@app.route('/client/<client>/notifications', methods=['GET','POST'])
def client_notifications(client):
    with db() as conn:
        if request.method == 'POST':
            notification_config(conn, client)
            conn.execute('UPDATE client_notifications SET enabled=?,email=?,notify_error=?,notify_warning=?,notify_stale=?,updated_at=? WHERE client=?',(1 if request.form.get('enabled') == 'on' else 0, request.form.get('email','').strip(), 1 if request.form.get('notify_error') == 'on' else 0, 1 if request.form.get('notify_warning') == 'on' else 0, 1 if request.form.get('notify_stale') == 'on' else 0, now(), client))
            return redirect(url_for('client_report', client=client))
        cfg = dict(notification_config(conn, client))
    return render_template('notifications.html', client=client, cfg=cfg)

init_db()
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('BACKUP_PANEL_PORT','8080')))
