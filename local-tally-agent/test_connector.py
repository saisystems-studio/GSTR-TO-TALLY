import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
import tally_local_agent as agent


class ConnectorSafetyTests(unittest.TestCase):
    def setUp(self):
        self.scope = {'installation_id': 'installation', 'agent_id': 1, 'user_id': 2,
                      'device_id': 3, 'company_gstin': '33AFHPM6103Q1Z8', 'tally_serial': '123456'}
        self.job = {'job_id': '12345678-1234-1234-1234-123456789abc', 'scope': self.scope,
                    'expires_at': (datetime.now(timezone.utc)+timedelta(minutes=2)).isoformat(),
                    'payload': {'operation': 'import_xml', 'body': '<ENVELOPE><HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER><BODY><IMPORTDATA><REQUESTDESC><REPORTNAME>Vouchers</REPORTNAME><STATICVARIABLES><SVCURRENTCOMPANY>Example</SVCURRENTCOMPANY></STATICVARIABLES></REQUESTDESC><REQUESTDATA><TALLYMESSAGE><VOUCHER ACTION="Create"><NARRATION>Invoice</NARRATION></VOUCHER></TALLYMESSAGE></REQUESTDATA></IMPORTDATA></BODY></ENVELOPE>'}}
        self.identity = {'tally_reachable': True, 'company_name': 'Example',
                         'company_gstin': self.scope['company_gstin'], 'tally_serial': '123456'}

    def test_rejects_another_user_before_tally_write(self):
        other = {**self.scope, 'user_id': 4}
        with self.assertRaises(ValueError):
            agent.validate_job(self.job, other, self.identity)

    def test_rejects_company_switch_and_expired_job(self):
        with self.assertRaises(ValueError):
            agent.validate_job(self.job, self.scope, {**self.identity, 'company_gstin': '27AAPFU0939F1ZV'})
        self.job['expires_at'] = '2020-01-01T00:00:00+00:00'
        with self.assertRaises(ValueError):
            agent.validate_job(self.job, self.scope, self.identity)

    def test_rejects_arbitrary_tdl_and_company_creation(self):
        for dangerous in ('<TDL><TDLMESSAGE><FUNCTION NAME="Run"/></TDLMESSAGE></TDL>',
                          '<COMPANY NAME="Unrelated"/>', '<VOUCHER ACTION="Delete"/>'):
            self.job['payload']['body'] = self.job['payload']['body'].replace('<NARRATION>Invoice</NARRATION>', dangerous)
            with self.assertRaises(ValueError):
                agent.validate_job(self.job, self.scope, self.identity)

    def test_valid_import_preserves_real_full_ack(self):
        raw = b'<RESPONSE><CREATED>1</CREATED><ERRORS>0</ERRORS>'+b' '*25000+b'</RESPONSE>'
        with patch.object(agent, 'tally_post', return_value=raw):
            result = agent.execute_job(self.job, self.scope, self.identity)
        self.assertEqual(result['acknowledgement']['raw_response'], raw.decode())

    def test_execution_journal_never_repeats_uncertain_write(self):
        with tempfile.TemporaryDirectory() as folder:
            journal = agent.Journal(Path(folder)/'journal.json', protect=lambda b: b, unprotect=lambda b: b)
            journal.begin(self.job['job_id'])
            restarted = agent.Journal(Path(folder)/'journal.json', protect=lambda b: b, unprotect=lambda b: b)
            result = restarted.previous(self.job['job_id'])
            self.assertFalse(result['success'])
            self.assertIn('uncertain', result['error'].lower())

    def test_journal_replays_response_after_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            journal = agent.Journal(Path(folder)/'journal.json', protect=lambda b: b, unprotect=lambda b: b)
            result = {'success': True, 'acknowledgement': {'raw_response': '<RESPONSE><CREATED>1</CREATED></RESPONSE>'}}
            journal.begin(self.job['job_id'])
            journal.finish(self.job['job_id'], result)
            restarted = agent.Journal(Path(folder)/'journal.json', protect=lambda b: b, unprotect=lambda b: b)
            self.assertEqual(restarted.previous(self.job['job_id']), result)


if __name__ == '__main__':
    unittest.main()
