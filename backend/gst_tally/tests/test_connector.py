import hashlib
import secrets
import uuid
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from gst_tally.models import ProductLicense, LicensedDevice, LocalTallyAgent, LocalTallyJob, GSTImportBatch


@override_settings(TALLY_LOCAL_AGENT_REQUIRED=True)
class ConnectorTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('connector-owner')
        self.other = get_user_model().objects.create_user('other-owner')
        self.license = ProductLicense.objects.create(customer=self.user, activation_key_hash='a'*64,
            licensed_gstin='33AFHPM6103Q1Z8', licensed_tally_serial='123456', status='ACTIVE',
            expiry_date=timezone.localdate()+timedelta(days=30))
        self.device = LicensedDevice.objects.create(license=self.license, device_fingerprint='browser-one')
        self.token = secrets.token_urlsafe(48)
        self.agent = LocalTallyAgent.objects.create(device=self.device,
            token_hash=hashlib.sha256(self.token.encode()).hexdigest(), browser_device='browser-one')
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.robot = APIClient()
        self.robot.credentials(HTTP_X_TALLY_AGENT_TOKEN=self.token)
        self.batch = GSTImportBatch.objects.create(uploaded_by=self.user, file_name='a.json',
            file_type='JSON', company_gstin=self.license.licensed_gstin, product_license=self.license)

    def heartbeat(self, **changes):
        return self.robot.post('/api/gst-tally/local-agent/heartbeat/', {
            'tally_reachable': True, 'company_name': 'Example',
            'company_gstin': self.license.licensed_gstin, 'tally_serial': '123456', **changes}, format='json')

    def test_wrong_company_is_connected_but_not_verified(self):
        self.heartbeat(company_gstin='27AAPFU0939F1ZV')
        result = self.client.get('/api/gst-tally/local-agent/status/', HTTP_X_DEVICE_ID='browser-one').json()
        self.assertTrue(result['tally_reachable'])
        self.assertFalse(result['verified'])
        self.assertEqual(result['status'], 'Company Verification Required')

    def test_status_never_selects_another_browser_device(self):
        self.heartbeat()
        result = self.client.get('/api/gst-tally/local-agent/status/', HTTP_X_DEVICE_ID='unknown').json()
        self.assertFalse(result['connected'])

    def test_stale_heartbeat_cannot_claim(self):
        self.heartbeat()
        LocalTallyAgent.objects.filter(pk=self.agent.pk).update(last_seen_at=timezone.now()-timedelta(minutes=5))
        self.assertEqual(self.robot.post('/api/gst-tally/local-agent/next-job/').status_code, 409)

    def test_result_requires_claim_and_real_response(self):
        job = LocalTallyJob.objects.create(agent=self.agent, batch=self.batch, idempotency_key='b'*64)
        url = f'/api/gst-tally/local-agent/jobs/{job.job_id}/result/'
        self.assertEqual(self.robot.post(url, {'success': True}, format='json').status_code, 409)
        job.status = LocalTallyJob.SENDING
        job.save()
        self.assertEqual(self.robot.post(url, {'success': True}, format='json').status_code, 400)

    def test_revoked_license_cannot_claim(self):
        self.heartbeat()
        self.license.status = 'REVOKED'
        self.license.save()
        self.assertEqual(self.robot.post('/api/gst-tally/local-agent/next-job/').status_code, 403)

    def test_enrollment_separates_browser_proof_and_connector_secret(self):
        self.agent.device.delete()
        public = APIClient()
        install = str(uuid.uuid4())
        poll = secrets.token_urlsafe(48)
        proof = secrets.token_urlsafe(48)
        token = secrets.token_urlsafe(48)
        begin = public.post('/api/gst-tally/local-agent/enroll/', {
            'installation_id': install, 'poll_hash': hashlib.sha256(poll.encode()).hexdigest(),
            'proof_hash': hashlib.sha256(proof.encode()).hexdigest()}, format='json')
        self.assertEqual(begin.status_code, 201)
        enrollment = begin.json()['enrollment_id']
        url = '/api/gst-tally/local-agent/pair/'
        self.assertEqual(self.client.post(url, {'enrollment_id': enrollment, 'proof': poll},
            format='json', HTTP_X_DEVICE_ID='browser-one').status_code, 400)
        paired = self.client.post(url, {'enrollment_id': enrollment, 'proof': proof},
            format='json', HTTP_X_DEVICE_ID='browser-one')
        self.assertEqual(paired.status_code, 200)
        claim = public.post('/api/gst-tally/local-agent/enroll/claim/', {
            'enrollment_id': enrollment, 'poll_secret': poll, 'token': token,
            'tally_serial': '123456', 'company_gstin': self.license.licensed_gstin}, format='json')
        self.assertEqual(claim.status_code, 200)
        self.assertNotIn('token', claim.json())
        agent = LocalTallyAgent.objects.get(token_hash=hashlib.sha256(token.encode()).hexdigest())
        self.assertEqual(agent.token_hash, hashlib.sha256(token.encode()).hexdigest())
        self.assertEqual(str(agent.installation_id), install)
        self.client.force_authenticate(self.other)
        self.assertNotEqual(self.client.post(url, {'enrollment_id': enrollment, 'proof': proof},
            format='json', HTTP_X_DEVICE_ID='browser-one').status_code, 200)
