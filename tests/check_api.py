from __future__ import annotations

import base64
import datetime as dt
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

if __name__ == '__main__' and '--serve' in sys.argv:
    from server.main import main
    main()
    raise SystemExit

import requests
from sqlalchemy import create_engine, text
from server.core.config import settings


def main():
    output = ROOT / 'tests' / 'artifacts'
    output.mkdir(parents=True, exist_ok=True)
    schema = 'api_check_' + uuid.uuid4().hex
    control = create_engine(settings.DATABASE_URL, connect_args={'connect_timeout': 5})
    results = []
    process = None
    created = False
    coverage = set()
    operations = set()
    log_path = output / 'server.log'
    session = requests.Session()
    session.trust_env = False
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    base = f'http://127.0.0.1:{port}'

    def check(name, method, path, expected=200, token=None, payload=None, verify=None):
        headers = {'Authorization': f'Bearer {token}'} if token else {}
        response = session.request(method, base + path, json=payload, headers=headers, timeout=15)
        route_path = path.split('?')[0]
        for verb, template in operations:
            if verb == method and re.fullmatch(re.sub(r'\{[^}]+\}', r'[^/]+', template), route_path):
                coverage.add((verb, template))
        try:
            data = response.json() if response.content else None
        except ValueError:
            data = None
        allowed = expected if isinstance(expected, tuple) else (expected,)
        ok = response.status_code in allowed
        detail = ''
        if ok and verify:
            try:
                ok = bool(verify(data, response))
                if not ok:
                    detail = 'Response content assertion failed'
            except Exception as exc:
                ok = False
                detail = type(exc).__name__ + ': ' + str(exc)
        if response.status_code not in allowed:
            detail = response.text[:250] if response.status_code >= 400 else 'Unexpected successful response'
        row = dict(name=name, method=method, path=path, expected=list(allowed), actual=response.status_code,
                   passed=ok, detail=detail, elapsed_ms=round(response.elapsed.total_seconds()*1000, 1))
        results.append(row)
        print(f"{'PASS' if ok else 'FAIL'} {name}: {response.status_code}", flush=True)
        return data, response

    p = '/api/v1'
    try:
        with control.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        created = True
        env = dict(os.environ, PGOPTIONS=f'-c search_path={schema}', ODG_SEED='1', HOST='127.0.0.1', PORT=str(port), PYTHONIOENCODING='utf-8')
        with log_path.open('w', encoding='utf-8') as log:
            process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--serve'], cwd=ROOT, env=env, stdout=log, stderr=log)
            for _ in range(150):
                if process.poll() is not None:
                    raise RuntimeError('Server startup failed; see server.log')
                try:
                    if session.get(base+'/health', timeout=1).status_code == 200:
                        break
                except requests.RequestException:
                    pass
                time.sleep(.2)
            else:
                raise RuntimeError('Server readiness timeout')

            spec = session.get(base+'/openapi.json', timeout=10).json()
            operations = {(m.upper(), path) for path, item in spec['paths'].items() for m in item if m in {'get','post','patch','delete','put'}}
            check('Health', 'GET', '/health', verify=lambda d,r: d == {'status':'ok'})
            check('Service identity', 'GET', p+'/system/info', verify=lambda d,r: d['service']=='office-desk-gear')
            check('Swagger UI', 'GET', '/docs')
            check('OpenAPI', 'GET', '/openapi.json', verify=lambda d,r: len(d['paths']) > 20)
            protected = {'/users/me','/bookings/active','/bookings/history','/equipment/my','/tickets','/tickets/my','/bookings/period','/auth/logout','/bookings/{booking_id}'}
            for verb, path in sorted(operations):
                relative = path.removeprefix(p)
                if relative.startswith('/admin/') or relative in protected or (relative=='/bookings' and verb=='POST'):
                    check('Auth required '+verb+' '+relative, verb, path.replace('{booking_id}','999999').replace('{desk_id}','999999').replace('{ticket_id}','999999').replace('{equipment_id}','999999'), 401, payload={} if verb in {'POST','PATCH'} else None)
            tokens = {}
            for role, email in [('employee','employee@company.ru'), ('it_admin','it@company.ru'), ('office_manager','office@company.ru')]:
                data, _ = check('Login '+role, 'POST', p+'/auth/login', payload={'email':email,'password':'password123'})
                tokens[role] = data['access_token']
            employee, admin, manager = [tokens[r] for r in ('employee','it_admin','office_manager')]
            data, _ = check('Register employee', 'POST', p+'/auth/register', payload={'full_name':'API Test','email':'api-test@example.com','password':'test-pass-123'}, verify=lambda d,r: d['role']=='employee' and 'password_hash' not in d)
            uid = data['id']
            check('Duplicate email', 'POST', p+'/auth/register', 400, payload={'full_name':'API Test','email':'api-test@example.com','password':'test-pass-123'})
            check('Invalid email', 'POST', p+'/auth/register', 400, payload={'full_name':'API Test','email':'wrong','password':'test-pass-123'})
            check('Short password', 'POST', p+'/auth/register', 422, payload={'full_name':'API Test','email':'short@example.com','password':'123'})
            check('Wrong password', 'POST', p+'/auth/login', 401, payload={'email':'employee@company.ru','password':'wrong'})
            check('Missing token', 'GET', p+'/users/me', 401)
            check('Invalid token', 'GET', p+'/users/me', 401, token='invalid')
            check('Own profile', 'GET', p+'/users/me', token=employee, verify=lambda d,r: d['role']=='employee')
            check('Update profile', 'PATCH', p+'/users/me', token=employee, payload={'full_name':'Updated API User','job_title':'Tester'}, verify=lambda d,r: d['full_name']=='Updated API User')
            check('Employee admin access denied', 'GET', p+'/admin/users', 403, token=employee)
            check('Manager equipment access denied', 'GET', p+'/admin/equipment', 403, token=manager)
            check('Admin users', 'GET', p+'/admin/users', token=manager, verify=lambda d,r: len(d)>=4)
            check('Desk features', 'GET', p+'/desk-features', verify=lambda d,r: len(d)==4)
            check('Desks list', 'GET', p+'/desks', verify=lambda d,r: len(d)==4)
            check('Desk feature filtering', 'GET', p+'/desks?features=1,2', verify=lambda d,r: len(d)==1)
            check('Invalid feature filter', 'GET', p+'/desks?features=abc', 422)
            check('Soft filter', 'GET', p+'/desks?sockets=999', verify=lambda d,r: d['soft_filter'] and r.headers['X-Partial-Match']=='true')
            desk, _ = check('Create desk', 'POST', p+'/admin/desks', token=manager, payload={'floor':9,'section':1,'number':901,'sockets':4,'features':[1]})
            did = desk['id']
            check('Duplicate desk', 'POST', p+'/admin/desks', 409, token=manager, payload={'floor':9,'number':901})
            check('Update desk', 'PATCH', p+f'/admin/desks/{did}', token=manager, payload={'sockets':6}, verify=lambda d,r: d['sockets']==6)
            check('Missing desk update', 'PATCH', p+'/admin/desks/999999', 404, token=admin, payload={'sockets':1})
            eq, _ = check('Create equipment', 'POST', p+'/admin/equipment', token=admin, payload={'type':'Test monitor','socket_serial':'API-TEST-001','desk_id':did})
            eid = eq['id']
            check('Duplicate serial', 'POST', p+'/admin/equipment', 409, token=admin, payload={'type':'Monitor','socket_serial':'API-TEST-001'})
            check('Equipment detail', 'GET', p+f'/equipment/{eid}', verify=lambda d,r: d['id']==eid)
            check('Missing equipment', 'GET', p+'/equipment/999999', 404)
            check('Equipment list', 'GET', p+'/admin/equipment', token=admin, verify=lambda d,r: len(d)==3)
            check('Assign equipment', 'PATCH', p+f'/admin/equipment/{eid}/assign', token=admin, payload={'assigned_user_id':1,'desk_id':did}, verify=lambda d,r: d['assigned_user_id']==1)
            check('My equipment', 'GET', p+'/equipment/my', token=employee, verify=lambda d,r: any(x['id']==eid for x in d))
            check('QR PNG generation', 'GET', p+f'/admin/equipment/{eid}/qr', token=admin, verify=lambda d,r: base64.b64decode(d['qr_code_base64']).startswith(b'\x89PNG\r\n\x1a\n') and d['qr_code_url'].endswith('/'+str(eid)))
            ticket, _ = check('Create ticket', 'POST', p+'/tickets', token=employee, payload={'equipment_id':eid,'description':'Test issue'}, verify=lambda d,r: d['status']=='new')
            tid = ticket['id']
            check('My tickets', 'GET', p+'/tickets/my', token=employee, verify=lambda d,r: any(x['id']==tid for x in d))
            check('All tickets', 'GET', p+'/admin/tickets', token=admin, verify=lambda d,r: len(d)==1)
            check('Ticket status', 'PATCH', p+f'/admin/tickets/{tid}/status', token=admin, payload={'status':'in_progress'}, verify=lambda d,r: d['status']=='in_progress')
            check('Invalid ticket status', 'PATCH', p+f'/admin/tickets/{tid}/status', 422, token=admin, payload={'status':'wrong'})
            check('Empty issue', 'POST', p+'/tickets', 422, token=employee, payload={'equipment_id':eid,'description':' '})
            start = (dt.datetime.now()+dt.timedelta(days=2)).replace(hour=9,minute=0,second=0,microsecond=0)
            booking_payload = {'desk_id':did,'planed_at':start.isoformat(),'duration_hours':2}
            booking, _ = check('Create booking', 'POST', p+'/bookings', token=employee, payload=booking_payload)
            bid = booking['id']
            check('Overlapping booking', 'POST', p+'/bookings', 409, token=manager, payload=booking_payload)
            check('Booking list/date filter', 'GET', p+f'/bookings?desk_id={did}&date={start.date()}', verify=lambda d,r: any(x['id']==bid for x in d))
            check('Booking history', 'GET', p+'/bookings/history', token=employee, verify=lambda d,r: any(x['id']==bid for x in d))
            check('No current booking', 'GET', p+'/bookings/active', token=employee, verify=lambda d,r: d is None)
            check('Foreign booking cancellation denied', 'DELETE', p+f'/bookings/{bid}', 403, token=manager)
            check('Cancel own booking', 'DELETE', p+f'/bookings/{bid}', 204, token=employee)
            check('Repeated cancellation', 'DELETE', p+f'/bookings/{bid}', 404, token=employee)
            period = {'desk_id':did,'start_date':start.isoformat(),'end_date':(start+dt.timedelta(days=2)).isoformat(),'daily_duration_hours':2}
            bookings, _ = check('Period booking', 'POST', p+'/bookings/period', token=employee, payload=period, verify=lambda d,r: len(d)==3)
            check('Period conflict skips', 'POST', p+'/bookings/period', token=manager, payload=period, verify=lambda d,r: d==[] and r.headers.get('X-Skipped-Count')=='3')
            check('Admin cancellation requires reason', 'DELETE', p+f"/admin/bookings/{bookings[0]['id']}", 422, token=manager)
            check('Admin cancellation', 'DELETE', p+f"/admin/bookings/{bookings[0]['id']}", 204, token=manager, payload={'reason':'API check'}, verify=lambda d,r: r.headers.get('X-Cancelled-By')=='admin')
            check('Analytics default', 'GET', p+'/admin/analytics', token=manager, verify=lambda d,r: 'occupancy_rate' in d)
            check('Analytics with data', 'GET', p+f'/admin/analytics?start_date={start.date()}&end_date={(start+dt.timedelta(days=2)).date()}', token=admin, verify=lambda d,r: d['total_bookings']==2 and d['occupancy_rate']>0)
            check('Invalid analytics period', 'GET', p+'/admin/analytics?start_date=2026-10-28&end_date=2026-10-27', 422, token=manager)
            check('SECURITY public admin registration denied', 'POST', p+'/auth/register', (400,403,422), payload={'full_name':'Privilege Test','email':'privilege@example.com','password':'test-pass-123','role':'it_admin'}, verify=lambda d,r: True)
            elevated, _ = check('Privilege test login', 'POST', p+'/auth/login', payload={'email':'privilege@example.com','password':'test-pass-123'})
            check('SECURITY self-registered admin cannot list users', 'GET', p+'/admin/users', 403, token=elevated['access_token'])
            timezone_start = start + dt.timedelta(days=10)
            aware = dict(booking_payload, planed_at=timezone_start.astimezone(dt.timezone(dt.timedelta(hours=3))).isoformat())
            check('Timezone-aware booking preserves local time', 'POST', p+'/bookings', token=employee, payload=aware, verify=lambda d,r: d['planed_at']==timezone_start.isoformat())
            check('Repeated timezone-aware booking rejects overlap', 'POST', p+'/bookings', 409, token=employee, payload=aware)
            for hours in (0, -12, 14):
                equivalent = dict(aware, planed_at=timezone_start.astimezone(dt.timezone(dt.timedelta(hours=hours))).isoformat())
                check(f'Equivalent instant offset {hours} conflicts', 'POST', p+'/bookings', 409, token=manager, payload=equivalent)
            check('Naive equivalent conflicts', 'POST', p+'/bookings', 409, token=manager, payload=dict(booking_payload, planed_at=timezone_start.isoformat()))
            check('User overlap on different desk with offset', 'POST', p+'/bookings', 409, token=employee, payload=dict(aware, desk_id=1))
            period_start = (timezone_start+dt.timedelta(days=3)).replace(hour=0, minute=30)
            period_end = period_start+dt.timedelta(days=1)
            aware_period = dict(period, start_date=period_start.astimezone(dt.timezone(dt.timedelta(hours=-12))).isoformat(), end_date=period_end.astimezone(dt.timezone(dt.timedelta(hours=-12))).isoformat())
            check('Period offset preserves local date and time', 'POST', p+'/bookings/period', token=employee, payload=aware_period, verify=lambda d,r: [x['planed_at'] for x in d]==[period_start.isoformat(),period_end.isoformat()])
            check('Naive period equivalent skips overlaps', 'POST', p+'/bookings/period', token=manager, payload=dict(period, start_date=period_start.isoformat(), end_date=period_end.isoformat()), verify=lambda d,r: d==[] and r.headers.get('X-Skipped-Count')=='2')
            now_payload = dict(booking_payload, planed_at=(dt.datetime.now()-dt.timedelta(minutes=5)).isoformat())
            active, _ = check('Create current booking', 'POST', p+'/bookings', token=employee, payload=now_payload)
            check('Current booking returned', 'GET', p+'/bookings/active', token=employee, verify=lambda d,r: d['id']==active['id'])
            check('Desk marked occupied', 'GET', p+'/desks?floor=9', verify=lambda d,r: d[0]['is_free'] is False)
            check('Cancel current booking', 'DELETE', p+f"/bookings/{active['id']}", 204, token=employee)
            data, _ = check('Reset test login', 'POST', p+'/auth/login', payload={'email':'api-test@example.com','password':'test-pass-123'})
            reset_token = data['access_token']
            check('Password reset request', 'POST', p+'/auth/password-reset/request', payload={'email':'api-test@example.com'})
            check('Wrong reset code', 'POST', p+'/auth/password-reset/confirm', 400, payload={'email':'api-test@example.com','code':'invalid','new_password':'new-pass-123'})
            codes = re.findall(r'api-test@example.com: (\d{6})', log_path.read_text(encoding='utf-8'))
            if not codes:
                raise RuntimeError('Reset code missing from local server output')
            check('Password reset confirm', 'POST', p+'/auth/password-reset/confirm', payload={'email':'api-test@example.com','code':codes[-1],'new_password':'new-pass-123'})
            check('Reset revokes sessions', 'GET', p+'/users/me', 401, token=reset_token)
            check('Old password rejected', 'POST', p+'/auth/login', 401, payload={'email':'api-test@example.com','password':'test-pass-123'})
            data, _ = check('New password accepted', 'POST', p+'/auth/login', payload={'email':'api-test@example.com','password':'new-pass-123'})
            check('Logout', 'POST', p+'/auth/logout', 204, token=data['access_token'])
            check('Logout revokes token', 'GET', p+'/users/me', 401, token=data['access_token'])
    finally:
        if process:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if created:
            assert re.fullmatch(r'api_check_[0-9a-f]{32}', schema)
            with control.begin() as conn:
                conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            print('Temporary PostgreSQL schema removed', flush=True)
        control.dispose()
        session.close()
        if log_path.exists():
            log_text = log_path.read_text(encoding='utf-8')
            log_path.write_text(re.sub(r'(код сброса пароля для .*?: )\d{6}', r'\1[REDACTED]', log_text), encoding='utf-8')
        report = {'checks':len(results),'passed':sum(x['passed'] for x in results),'failed':sum(not x['passed'] for x in results),
                  'operations_total':len(operations),'operations_covered':len(coverage),'uncovered':sorted(operations-coverage),'results':results,'schema_removed':created}
        (output/'api_results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({k:v for k,v in report.items() if k!='results'}, ensure_ascii=False), flush=True)
    return 1 if any(not x['passed'] for x in results) else 0


if __name__ == '__main__':
    raise SystemExit(main())
