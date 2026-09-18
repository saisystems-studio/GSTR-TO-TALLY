"""Pure Python protocol shared by Django and the frozen Windows connector.

Only supported accounting requests pass this boundary. No URLs, shell commands,
arbitrary TDL functions, company writes, delete or cancellation operations.
"""
import json
from xml.etree import ElementTree as ET
from .read_requests import (build_company_query_xml, build_ledger_query_xml,
                            build_voucher_query_xml, build_daybook_query_xml, build_license_query_xml)

MAX_BYTES = 16 * 1024 * 1024
OBJECTS = {'Ledger', 'Stock Item', 'Unit', 'Company', 'TaxUnit'}
LICENSE_PARAMS = {'SerialNumber', 'IsGold', 'IsSilver', 'IsEducationalMode', 'IsLicensedMode', 'AdminEmailID'}


def xml_root(body):
    if len(body.encode()) > MAX_BYTES or '<!DOCTYPE' in body.upper() or '<!ENTITY' in body.upper():
        raise ValueError('Unsafe or oversized XML.')
    return ET.fromstring(body.replace('&#4;', '').replace('&#x4;', ''))


def operation(body, headers):
    if 'json' in str(headers.get('Content-Type', headers.get('content-type', ''))).lower():
        return 'import_json' if str(headers.get('tallyrequest', '')).lower() == 'import' else 'read_json'
    root = xml_root(body)
    verb = (root.findtext('./HEADER/TALLYREQUEST') or '').lower()
    return 'import_xml' if verb in ('import', 'import data') else 'read_xml'


def canonical(root):
    return (root.tag, tuple(sorted(root.attrib.items())), (root.text or '').strip(),
            tuple(canonical(child) for child in root))


def prepare_request(payload, company):
    body = payload.get('body')
    headers = payload.get('headers') or {}
    if not isinstance(body, str) or not isinstance(headers, dict) or len(body.encode()) > MAX_BYTES:
        raise ValueError('Invalid Tally payload.')
    allowed_headers = {'content-type', 'version', 'tallyrequest', 'type', 'subtype', 'id',
                       'detailed-response', 'svexportformat', 'svcurrentcompany'}
    if set(str(k).lower() for k in headers) - allowed_headers:
        raise ValueError('Unsupported request headers.')
    if any('\r' in str(v) or '\n' in str(v) for v in headers.values()):
        raise ValueError('Invalid header value.')
    op = operation(body, headers)
    if payload.get('operation') != op:
        raise ValueError('Unsupported operation.')
    if op.endswith('json'):
        return prepare_json(body, headers, op, company)
    root = xml_root(body)
    if root.tag != 'ENVELOPE' or len(root.findall('HEADER')) != 1 or len(root.findall('BODY')) != 1:
        raise ValueError('Invalid Tally envelope.')
    if op == 'import_xml':
        if any(node.tag.upper() in {'TDL', 'FUNCTION', 'COMPANY', 'SYSTEM', 'EXECUTE', 'COLLECTION'} for node in root.iter()):
            raise ValueError('Unsupported import content.')
        if any(str(node.get('ACTION', 'Create')).lower() not in ('create', 'alter') for node in root.iter()):
            raise ValueError('Only create/alter accounting operations are allowed.')
        messages = root.findall('./BODY/IMPORTDATA/REQUESTDATA/TALLYMESSAGE')
        if not messages or any(child.tag not in {'VOUCHER', 'LEDGER', 'STOCKITEM', 'UNIT'} for m in messages for child in m):
            raise ValueError('Unsupported import object.')
        companies = root.findall('.//SVCURRENTCOMPANY')
        if len(companies) != 1 or companies[0].text != company:
            raise ValueError('Import targets a different company.')
        if root.findtext('.//REPORTNAME') not in ('Vouchers', 'All Masters'):
            raise ValueError('Unsupported import report.')
    else:
        kind = (root.findtext('./HEADER/TYPE') or '').lower()
        identifier = root.findtext('./HEADER/ID') or ''
        templates = [build_company_query_xml(), build_ledger_query_xml()]
        if kind == 'function' and identifier == '$$LicenseInfo':
            param = root.findtext('.//PARAM')
            if param in LICENSE_PARAMS:
                templates.append(build_license_query_xml(param))
        elif kind == 'collection' and identifier == 'GSTVoucherQuery':
            templates.append(build_voucher_query_xml(root.findtext('.//SVCURRENTCOMPANY'),
                root.findtext('.//SVFROMDATE'), root.findtext('.//SVTODATE')))
        elif root.findtext('.//REPORTNAME') == 'Day Book':
            templates.append(build_daybook_query_xml(root.findtext('.//SVCURRENTCOMPANY'),
                root.findtext('.//SVFROMDATE'), root.findtext('.//SVTODATE')))
        if kind == 'object':
            if root.findtext('./HEADER/SUBTYPE') not in OBJECTS:
                raise ValueError('Unsupported object read.')
            allowed = {'ENVELOPE', 'HEADER', 'VERSION', 'TALLYREQUEST', 'TYPE', 'SUBTYPE', 'ID',
                       'BODY', 'DESC', 'FETCHLIST', 'FETCH', 'STATICVARIABLES', 'SVCURRENTCOMPANY', 'SVEXPORTFORMAT'}
            if any(node.tag not in allowed for node in root.iter()):
                raise ValueError('Unsupported object query.')
            if any('$$' in (node.text or '') or ':' in (node.text or '') for node in root.findall('.//FETCH')):
                raise ValueError('Computed fetch expressions are forbidden.')
        elif not any(canonical(root) == canonical(ET.fromstring(template)) for template in templates):
            raise ValueError('Query is not a predefined Tally read.')
        for node in root.findall('.//SVCURRENTCOMPANY'):
            if node.text and node.text != company:
                raise ValueError('Read targets another company.')
        # Pin otherwise unscoped reads to the locally verified company.
        desc = root.find('./BODY/DESC')
        if desc is not None and kind != 'function':
            variables = desc.find('STATICVARIABLES')
            if variables is None:
                variables = ET.SubElement(desc, 'STATICVARIABLES')
            target = variables.find('SVCURRENTCOMPANY')
            if target is None:
                target = ET.SubElement(variables, 'SVCURRENTCOMPANY')
            target.text = company
    return ET.tostring(root, encoding='utf-8'), {'Content-Type': 'application/xml; charset=utf-8'}


def prepare_json(body, headers, op, company):
    data = json.loads(body) if body else {}
    if not isinstance(data, dict):
        raise ValueError('Invalid JSON payload.')
    normalized = {str(k).lower(): str(v) for k, v in headers.items()}
    if set(data) - {'static_variables', 'tallymessage', 'fetch_list'}:
        raise ValueError('Unsupported JSON operation.')
    variables = data.get('static_variables', [])
    if not isinstance(variables, list):
        raise ValueError('Invalid static variables.')
    for var in variables:
        if var.get('name', '').lower() not in {'svcurrentcompany', 'svexportformat', 'svmstimportformat', 'svvchimportformat'}:
            raise ValueError('Unsupported static variable.')
        if var['name'].lower() == 'svcurrentcompany' and var.get('value') not in ('', company):
            raise ValueError('JSON targets another company.')
    if normalized.get('svcurrentcompany', company) != company:
        raise ValueError('JSON targets another company.')
    if op == 'import_json':
        if normalized.get('type', '').lower() != 'data' or normalized.get('id') not in ('All Masters', 'Vouchers'):
            raise ValueError('Unsupported JSON import.')
        messages = data.get('tallymessage')
        if not isinstance(messages, list) or not messages:
            raise ValueError('Missing import objects.')
        for message in messages:
            metadata = message.get('metadata', {})
            if metadata.get('type') not in {'Ledger', 'Stock Item', 'Unit', 'Voucher'} or metadata.get('action') not in {'create', 'alter'}:
                raise ValueError('Unsupported JSON object/action.')
    else:
        if normalized.get('type', '').lower() != 'object' or normalized.get('subtype') not in OBJECTS or 'tallymessage' in data:
            raise ValueError('Unsupported JSON read.')
        fields = data.get('fetch_list', ['*'])
        if not isinstance(fields, list) or any(not isinstance(f, str) or '$' in f or ':' in f for f in fields):
            raise ValueError('Unsupported fetch expression.')
    data['static_variables'] = [v for v in variables if v['name'].lower() != 'svcurrentcompany'] + [
        {'name': 'svCurrentCompany', 'value': company}]
    normalized['svcurrentcompany'] = company
    return json.dumps(data, ensure_ascii=True).encode(), normalized
