"""Isolated development validation. Never connects to a customer database.
Run with --send-authorized-email only after inbox authorization; sends one welcome email.
"""
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]

if '--gstr-child' in sys.argv:
    data = json.load(sys.stdin)
    sys.path.insert(0, str(ROOT / 'backend'))
    os.environ['DJANGO_SETTINGS_MODULE'] = 'config.test_settings'
    import django
    django.setup()
    from django.test import override_settings
    from rest_framework.test import APIClient
    from unittest.mock import patch
    with override_settings(NEXUS_ENABLED=True, NEXUS_BASE_URL=data['origin'], NEXUS_PRODUCT_ID='gstr2tally', NEXUS_PRODUCT_KEY=data['key'], ALLOWED_HOSTS=['testserver']):
        with patch('nexus_auth.views.detected_tally', return_value={'detected': True, 'tally_serial': data['serial'], 'identity': {}}):
            response = APIClient().post('/api/nexus/register/', data['signup'], format='json') if data['action'] == 'register' else APIClient().post('/api/nexus/login/', {'user_id': data['serial'], 'password': data['password'], 'device': {'client_device_id': 'isolated-registration-test', 'fingerprint': 'isolated-registration-test'}}, format='json')
            print(json.dumps({'status': response.status_code, **{k: response.data[k] for k in ('code', 'user_id', 'welcome_email') if k in response.data}}))
    sys.exit(0)

if '--send-authorized-email' not in sys.argv:
    raise SystemExit('Explicit --send-authorized-email required; sends one real development email.')

import hashlib
import re
import secrets
import smtplib
import subprocess
import threading
import uuid
from unittest.mock import patch
from wsgiref.simple_server import make_server, WSGIRequestHandler

sys.path.insert(0, 'D:/SaiInfosysNexus/backend')
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings'
from django.conf import settings
# Replace database settings BEFORE Django initializes any connection.
db = ROOT / 'docs' / ('nexus-validation-' + uuid.uuid4().hex + '.sqlite3')
settings.DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': str(db)}}
settings.CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
settings.ALLOWED_HOSTS = ['127.0.0.1', 'localhost', 'testserver']
settings.NEW_REQUEST_NOTIFY_EMAILS = []
settings.EMAIL_TIMEOUT = 15
settings.MAIL_SEND_ATTEMPTS = 1
import django
django.setup()
from django.core.management import call_command
from django.core.handlers.wsgi import WSGIHandler
from django.core.mail import EmailMultiAlternatives
from apps.tallysl.models import TallyProduct, TallyProductKey, TallyCustomer
from apps.registrations.models import EmailLog
# Fresh disposable SQLite schema ONLY. No existing Nexus migration is rerun.
call_command('migrate', verbosity=0, interactive=False)
product = TallyProduct.objects.get(code='gstr2tally')
key = secrets.token_urlsafe(32)
TallyProductKey.objects.create(product=product, key_hash=hashlib.sha256(key.encode()).hexdigest())
serial = '9' + str(secrets.randbelow(100000000)).zfill(8)
email = 'saiinfosysofficial@gmail.com'
report = {'test_serial': serial, 'simulated_detection': True, 'database': str(db), 'registered_email': 'sai***@gmail.com', 'smtp_backend': settings.EMAIL_BACKEND}
if 'smtp' not in settings.EMAIL_BACKEND:
    raise SystemExit('Real SMTP backend is not configured; no email sent.')
captured = {'recipients': [], 'rcpt_codes': [], 'password': '', 'activation_key_present': False, 'messages': 0}
original_send = EmailMultiAlternatives.send
original_rcpt = smtplib.SMTP.rcpt

def capture_send(message, *args, **kwargs):
    captured['messages'] += 1
    captured['recipients'].extend(message.to)
    match = re.search(r'Temporary Password: (\S+)', message.body)
    captured['password'] = match.group(1) if match else ''
    captured['activation_key_present'] = bool(re.search(r'Activation Key:\s*\S+', message.body))
    return original_send(message, *args, **kwargs)

def capture_rcpt(client, recipient, *args, **kwargs):
    result = original_rcpt(client, recipient, *args, **kwargs)
    captured['rcpt_codes'].append({'recipient': 'sai***@gmail.com' if recipient == email else 'UNEXPECTED', 'status': result[0]})
    return result

class Quiet(WSGIRequestHandler):
    def log_message(self, *args):
        pass

server = make_server('127.0.0.1', 0, WSGIHandler(), handler_class=Quiet)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
base = {'origin': f'http://127.0.0.1:{server.server_port}', 'key': key, 'serial': serial, 'signup': {'name': 'GSTR Isolated Development Validation', 'email': email, 'contact_number': '9876543210'}}
def gstr(action, **extra):
    result = subprocess.run([str(ROOT / 'backend/.venv/Scripts/python.exe'), str(Path(__file__)), '--gstr-child'], input=json.dumps({**base, 'action': action, **extra}), text=True, capture_output=True, timeout=90, cwd=ROOT)
    if result.returncode:
        raise RuntimeError('GSTR isolated subprocess failed: ' + result.stderr[-1500:])
    return json.loads(result.stdout.strip().splitlines()[-1])

try:
    with patch.object(EmailMultiAlternatives, 'send', capture_send), patch.object(smtplib.SMTP, 'rcpt', capture_rcpt):
        report['register_api'] = gstr('register')
        report['duplicate_api'] = gstr('register')
        report['login_api'] = gstr('login', password=captured['password']) if captured['password'] else {'status': 'not_tested'}
    customer = TallyCustomer.objects.get(product=product, tally_serial=serial)
    report.update(customer_count=TallyCustomer.objects.filter(product=product, tally_serial=serial).count(), user_id_matches_serial=customer.user_id == serial, saved_email_matches_input=customer.email == email, actual_recipient='sai***@gmail.com' if captured['recipients'] == [email] else 'UNEXPECTED', smtp_rcpt=captured['rcpt_codes'], email_submission=list(EmailLog.objects.values('kind', 'status', 'attempts')), welcome_messages=captured['messages'], activation_key_in_registration_email=captured['activation_key_present'], inbox_delivery='NOT_VERIFIED', existing_customer_databases='NOT_CONNECTED')
    assert report['register_api']['status'] == 201
    assert report['duplicate_api']['status'] == 409
    assert report['customer_count'] == 1 and report['saved_email_matches_input'] and report['user_id_matches_serial']
    assert captured['recipients'] == [email] and not captured['activation_key_present']
    assert report['login_api'] == {'status': 403, 'code': 'password_change_required'}
finally:
    server.shutdown()
    output = ROOT / 'docs/registration-validation-result.json'
    output.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
