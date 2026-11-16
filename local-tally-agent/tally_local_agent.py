"""Silent outbound-only GSTR2Tally Windows connector. No customer configuration."""
from __future__ import annotations
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import random
import secrets
import ssl
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
import uuid
import webbrowser
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'backend'))
from gst_tally.tally.connector_protocol import prepare_request, MAX_BYTES
from gst_tally.tally.read_requests import build_company_query_xml, build_license_query_xml
from windows_runtime import protect, unprotect, single_instance

APP_DIR = Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'GSTR2TallyConnector'
CONFIG_PATH = APP_DIR / 'credentials.dat'
TALLY_URL = 'http://127.0.0.1:9000'
API_PREFIX = '/api/gst-tally/local-agent/'
WEB_URL = ''  # set only by the loopback developer override in main(); empty = origin + /gstr2tally/


def save_private(path, value, encrypt=protect):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    with temp.open('wb') as stream:
        stream.write(encrypt(json.dumps(value).encode()))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


class Journal:
    def __init__(self, path, protect=protect, unprotect=unprotect):
        self.path, self.protect = path, protect
        self.rows = json.loads(unprotect(path.read_bytes())) if path.exists() else {}

    def previous(self, job_id):
        row = self.rows.get(job_id)
        if row is None:
            return None
        return row.get('result') or {'success': False,
            'error': 'Tally write outcome is uncertain after interruption; reconcile before retrying.'}

    def begin(self, job_id):
        self.rows[job_id] = {'started': time.time()}
        save_private(self.path, self.rows, self.protect)

    def finish(self, job_id, result):
        self.rows[job_id]['result'] = result
        save_private(self.path, self.rows, self.protect)

    def acknowledged(self, job_id):
        self.rows[job_id]['acknowledged'] = time.time()
        self.rows = {key: row for key, row in self.rows.items()
                     if not row.get('acknowledged') or row['acknowledged'] > time.time()-7*86400}
        save_private(self.path, self.rows, self.protect)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('Redirects are not allowed for connector traffic.')


def bounded_read(response):
    raw = response.read(MAX_BYTES+1)
    if len(raw) > MAX_BYTES:
        raise ValueError('Tally response exceeds the supported size.')
    return raw


def api(origin, path, payload=None, token=''):
    parsed = urlsplit(origin)
    # HTTPS only. Plain HTTP is accepted solely for a loopback origin (developer builds talking to a local backend).
    loopback_dev = parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost')
    if (parsed.scheme != 'https' and not loopback_dev) or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
        raise ValueError('A valid release HTTPS origin is required.')
    if not path.startswith((API_PREFIX, '/api/nexus/connector/')):
        raise ValueError('Unsupported API operation.')
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['X-Tally-Agent-Token'] = token
    request = urllib.request.Request(origin.rstrip('/')+path, data=json.dumps(payload or {}).encode(),
                                     headers=headers, method='POST')
    opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    with opener.open(request, timeout=25) as response:
        return response.status, json.loads(bounded_read(response).decode() or '{}')


def tally_post(body, headers=None):
    request = urllib.request.Request(TALLY_URL, data=body,
        headers=headers or {'Content-Type': 'application/xml; charset=utf-8'}, method='POST')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=30) as response:
        return bounded_read(response)


def read_tally_identity(expected_gstin=''):
    result = {'tally_reachable': False, 'company_name': '', 'company_gstin': '', 'tally_serial': ''}
    try:
        raw = tally_post(build_company_query_xml())
        result['tally_reachable'] = True
        root = ET.fromstring(raw)
        companies = list(root.iter('COMPANY'))
        matches = [c for c in companies if (c.findtext('GSTREGISTRATIONNUMBER') or '').strip().upper() == expected_gstin]
        company = matches[0] if len(matches) == 1 else companies[0] if len(companies) == 1 else None
        if company is not None:
            result.update(company_name=(company.get('NAME') or company.findtext('NAME') or '').strip(),
                company_gstin=(company.findtext('GSTREGISTRATIONNUMBER') or '').strip().upper(),
                company_state=(company.findtext('STATENAME') or '').strip(),
                financial_year_from=(company.findtext('FINANCIALYEARFROM') or '').strip(),
                financial_year_to=(company.findtext('FINANCIALYEARTO') or '').strip(),
                books_from=(company.findtext('BOOKSFROM') or '').strip())
    except (OSError, ValueError, urllib.error.URLError, ET.ParseError):
        logging.info('Tally company identity is unavailable or incomplete.')
    # License discovery must work even with no usable company response.
    try:
        serial = ET.fromstring(tally_post(build_license_query_xml()))
        if serial.findtext('.//HEADER/STATUS') == '1':
            result['tally_serial'] = (serial.findtext('.//DATA/RESULT') or serial.findtext('.//RESULT') or '').strip()
    except (OSError, ValueError, urllib.error.URLError, ET.ParseError):
        logging.info('Tally identity is unavailable or incomplete.')
    return result


def read_license_meta():
    """Optional TallyPrime licence details via $$LicenseInfo. Only values Tally actually returns are
    included; unsupported parameters are skipped (never guessed). Experimental: see docs."""
    def param(name):
        try:
            root = ET.fromstring(tally_post(build_license_query_xml(name)))
            if root.findtext('.//HEADER/STATUS') == '1':
                return (root.findtext('.//DATA/RESULT') or root.findtext('.//RESULT') or '').strip()
        except (OSError, ValueError, urllib.error.URLError, ET.ParseError):
            pass
        return ''
    meta = {}
    gold, silver = param('IsGold').lower(), param('IsSilver').lower()
    if gold == 'yes':
        meta['edition'] = 'TallyPrime Gold'
    elif silver == 'yes':
        meta['edition'] = 'TallyPrime Silver'
    admin = param('AdminEmailID')
    if admin:
        meta['license_administrator'] = admin
    return meta


def validate_job(job, scope, identity):
    if job.get('scope') != scope:
        raise ValueError('Job belongs to another installation, user, device or company.')
    uuid.UUID(job['job_id'])
    expires = datetime.fromisoformat(job['expires_at'])
    if expires.tzinfo is None or expires <= datetime.now(timezone.utc):
        raise ValueError('Job expired before execution.')
    if not identity.get('tally_reachable') or not identity.get('company_name'):
        raise ValueError('Tally company is unavailable.')
    if any(not scope.get(k) or identity.get(k) != scope[k] for k in ('company_gstin', 'tally_serial')):
        raise ValueError('Registered serial and company GSTIN must both match.')
    return prepare_request(job['payload'], identity['company_name'])


def execute_job(job, scope, identity):
    try:
        body, headers = validate_job(job, scope, identity)
        raw = tally_post(body, headers)
        if not raw.strip():
            raise ValueError('Empty Tally response; outcome uncertain.')
        text = raw.decode('utf-8', 'replace')
        accepted = True
        if headers.get('Content-Type', '').startswith('application/xml'):
            response = ET.fromstring(text)
            status = (response.findtext('.//HEADER/STATUS') or response.findtext('.//STATUS') or '1').strip()
            errors = int(response.findtext('.//ERRORS') or response.findtext('.//LINEERRORS') or '0')
            created = int(response.findtext('.//CREATED') or response.findtext('.//ALTERED') or '0')
            accepted = status == '1' and errors == 0 and created > 0
        return {'success': accepted, 'acknowledgement': {'raw_response': text},
                **({} if accepted else {'error': 'Tally acknowledged the request with errors or no created/altered objects.'})}
    except (OSError, ValueError, urllib.error.URLError, ET.ParseError):
        return {'success': False, 'error': 'Tally request rejected or outcome uncertain; reconciliation required.'}


def enroll(origin, config):
    if not config.get('enrollment') or config['enrollment']['created'] < time.time()-23*3600:
        poll, proof, token = (secrets.token_urlsafe(48) for _ in range(3))
        _, row = api(origin, API_PREFIX+'enroll/', {'installation_id': config['installation_id'],
            'poll_hash': hashlib.sha256(poll.encode()).hexdigest(), 'proof_hash': hashlib.sha256(proof.encode()).hexdigest()})
        config['enrollment'] = {**row, 'poll': poll, 'proof': proof, 'token': token, 'created': time.time(), 'opened': False}
        save_private(CONFIG_PATH, config)
    pending = config['enrollment']
    if not pending['opened']:
        webbrowser.open((WEB_URL or origin+'/gstr2tally/')+'#connector='+pending['enrollment_id']+'.'+pending['proof'])
        pending['opened'] = True
        save_private(CONFIG_PATH, config)
    identity = read_tally_identity()
    # First-time discovery: lets a not-yet-registered customer see the verified Tally serial in GSTR.
    try:
        api(origin, '/api/nexus/connector/discovery/', {
            'enrollment_id': pending['enrollment_id'], 'poll_secret': pending['poll'],
            'tally_reachable': identity.get('tally_reachable', False) and bool(identity.get('tally_serial')),
            'tally_serial': identity.get('tally_serial', ''), **(read_license_meta() if identity.get('tally_serial') else {})})
    except (urllib.error.URLError, OSError, ValueError):
        logging.info('Discovery report will retry.')
    status, response = api(origin, API_PREFIX+'enroll/claim/', {
        'enrollment_id': pending['enrollment_id'], 'poll_secret': pending['poll'],
        'token': pending['token'], **identity})
    if status == 200:
        config.update(token=pending['token'], scope=response)
        del config['enrollment']
        save_private(CONFIG_PATH, config)


def run(origin):
    config = json.loads(unprotect(CONFIG_PATH.read_bytes())) if CONFIG_PATH.exists() else {'installation_id': str(uuid.uuid4())}
    save_private(CONFIG_PATH, config)
    journal = Journal(APP_DIR/'execution-journal.dat')
    failures = 0
    while True:
        try:
            if not config.get('token'):
                enroll(origin, config)
            else:
                token, scope = config['token'], config['scope']
                for job_id, row in list(journal.rows.items()):
                    if not row.get('acknowledged'):
                        api(origin, API_PREFIX+f'jobs/{job_id}/result/', journal.previous(job_id), token)
                        journal.acknowledged(job_id)
                identity = read_tally_identity(scope['company_gstin'])
                _, health = api(origin, API_PREFIX+'heartbeat/', identity, token)
                if health.get('verified'):
                    status, job = api(origin, API_PREFIX+'next-job/', token=token)
                    if status == 200 and job.get('job_id'):
                        result = journal.previous(job['job_id'])
                        if result is None:
                            journal.begin(job['job_id'])
                            identity = read_tally_identity(scope['company_gstin'])
                            result = execute_job(job, scope, identity)
                            journal.finish(job['job_id'], result)
                        api(origin, API_PREFIX+f"jobs/{job['job_id']}/result/", result, token)
                        journal.acknowledged(job['job_id'])
            failures = 0
        except urllib.error.HTTPError as exc:
            failures += 1
            logging.warning('VPS request rejected: HTTP %d', exc.code)
        except Exception as exc:
            failures += 1
            logging.warning('Connector will retry (%s).', type(exc).__name__)
        time.sleep(random.uniform(2, 4) if not failures else random.uniform(1, min(60, 2**min(failures, 6))))


def main():
    APP_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(APP_DIR/'connector.log', maxBytes=1024*1024, backupCount=3)
    logging.basicConfig(handlers=[handler], level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    with single_instance() as acquired:
        if not acquired:
            return
        # PyInstaller 6 onedir keeps bundled code in <app>\_internal, but the installer places release.json next
        # to the executable; accept either location (executable folder first).
        candidates = [Path(sys.executable).parent/'release.json', Path(__file__).parent/'release.json']
        release_file = next((c for c in candidates if c.exists()), candidates[-1])
        release = json.loads(release_file.read_text(encoding='utf-8-sig'))
        # Developer-only: run from source against a LOCAL backend/web app instead of production.
        # Loopback http origins only; anything else is ignored, so the packaged connector cannot be redirected.
        global WEB_URL
        def loopback(value):
            parts = urlsplit(value)
            return parts.scheme == 'http' and parts.hostname in ('127.0.0.1', 'localhost')
        dev_origin = os.environ.get('GSTR_CONNECTOR_DEV_ORIGIN', '').strip()
        if dev_origin and loopback(dev_origin):
            release['origin'] = dev_origin.rstrip('/')
            dev_web = os.environ.get('GSTR_CONNECTOR_DEV_WEB_URL', '').strip()
            WEB_URL = dev_web if dev_web and loopback(dev_web) else ''
            logging.info('Developer origin override active: %s', release['origin'])
        while True:
            try:
                run(release['origin'])
            except Exception as exc:
                logging.error('Connector recovery pending (%s).', type(exc).__name__)
                time.sleep(30)


if __name__ == '__main__':
    main()
