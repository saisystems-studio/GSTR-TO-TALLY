from unittest.mock import patch

from django.test import TestCase

from gst_tally.models import GSTImportBatch, LicensedDevice, ProductLicense
from gst_tally.services.product_license import pre_import_security_check
from gst_tally.tests.test_product_license_security import make_user, make_license


class Step3AcceptanceTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.license = make_license(self.user, serial='TEST-SERIAL', allowed_devices=2)
        self.batch = GSTImportBatch.objects.create(file_name='return.json', file_type='JSON', company_gstin=self.license.licensed_gstin)
        LicensedDevice.objects.create(license=self.license, device_fingerprint='PC-A', device_name='Office PC')

    def verify(self, gstin=None, serial='TEST-SERIAL', device='PC-A', name='Current Company'):
        with patch('gst_tally.services.product_license.step3_connection_check', return_value={
            'read_connected': True, 'can_import': True, 'company_name': name,
            'company_gstin': self.batch.company_gstin if gstin is None else gstin,
        }), patch('gst_tally.services.product_license.read_tally_license', return_value={'license_available': True, 'serial_number': serial}):
            return pre_import_security_check(self.batch, self.user, device_fingerprint=device, device_name=device)

    def assertBlocked(self, result, *codes):
        self.assertFalse(result['ready'])
        self.assertFalse(result['ready_for_master_preparation'])
        self.assertTrue(set(codes).issubset({item['code'] for item in result['errors']}), result['errors'])

    def test_A_all_checks_pass(self):
        result = self.verify()
        self.assertTrue(result['ready'])
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['product'], {'allowed': True, 'limit': 1, 'used': 1})
        self.assertEqual(result['device']['used'], 1)

    def test_B_wrong_gstin(self):
        self.assertBlocked(self.verify(gstin='29AAAAA0000A1Z5'), 'COMPANY_GSTIN_MISMATCH')

    def test_C_wrong_serial(self):
        result = self.verify(serial='OTHER-SERIAL')
        self.assertBlocked(result, 'TALLY_SERIAL_MISMATCH')
        self.assertEqual(result['tally_license']['registered_serial'], 'TEST-SERIAL')
        self.assertEqual(result['tally_license']['detected_serial'], 'OTHER-SERIAL')

    def test_D_both_errors(self):
        self.assertBlocked(self.verify(gstin='29AAAAA0000A1Z5', serial='OTHER-SERIAL'), 'COMPANY_GSTIN_MISMATCH', 'TALLY_SERIAL_MISMATCH')

    def test_E_device_limit(self):
        self.license.allowed_devices = 1
        self.license.save()
        self.assertBlocked(self.verify(device='PC-B'), 'DEVICE_LIMIT_REACHED')
        self.assertEqual(LicensedDevice.objects.count(), 1)

    def test_F_product_limit(self):
        self.assertBlocked(self.verify(serial='NEW-SERIAL'), 'PRODUCT_LIMIT_REACHED')
        self.assertEqual(ProductLicense.objects.count(), 1)
        self.license.refresh_from_db()
        self.assertEqual(self.license.licensed_tally_serial, 'TEST-SERIAL')

    def test_G_retry_does_not_consume_slots(self):
        for _ in range(3):
            self.assertTrue(self.verify()['ready'])
        self.assertEqual(ProductLicense.objects.count(), 1)
        self.assertEqual(LicensedDevice.objects.count(), 1)

    def test_H_different_company_name_allowed(self):
        self.assertTrue(self.verify(name='Completely Different Company Name')['ready'])

    def test_I_same_serial_wrong_company(self):
        result = self.verify(gstin='29AAAAA0000A1Z5')
        self.assertTrue(result['tally_license']['match'])
        self.assertBlocked(result, 'COMPANY_GSTIN_MISMATCH')

    def test_new_device_free_capacity_registers_once(self):
        for _ in range(2):
            result = self.verify(device='PC-B')
            self.assertTrue(result['ready'])
            self.assertEqual(result['device']['used'], 2)

    def test_revoked_device_is_not_recreated(self):
        LicensedDevice.objects.filter(device_fingerprint='PC-A').update(status=LicensedDevice.REVOKED)
        self.assertBlocked(self.verify(), 'DEVICE_NOT_AUTHORIZED')
        self.assertEqual(LicensedDevice.objects.count(), 1)

    def test_missing_source_does_not_borrow_tally_gstin(self):
        self.batch.company_gstin = ''
        self.batch.save()
        self.assertBlocked(self.verify(gstin=self.license.licensed_gstin), 'SOURCE_COMPANY_GSTIN_MISSING')
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.company_gstin, '')

    def test_normalizes_gstin(self):
        self.assertTrue(self.verify(gstin='  ' + self.batch.company_gstin.lower() + '  ')['ready'])

    def test_missing_registered_serial_shows_detected(self):
        self.license.licensed_tally_serial = ''
        self.license.save()
        result = self.verify()
        self.assertBlocked(result, 'PRODUCT_LICENSE_NOT_CONFIGURED')
        self.assertEqual(result['tally_license']['detected_serial'], 'TEST-SERIAL')


class LicenseOwnershipTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        self.owner = make_user()
        self.license = make_license(self.owner, serial='TEST-SERIAL', allowed_devices=1)
        LicensedDevice.objects.create(license=self.license, device_fingerprint='PC-A')
        self.other = get_user_model().objects.create_user(username='other_acct', password='x')
        self.batch = GSTImportBatch.objects.create(file_name='r.json', file_type='JSON', company_gstin=self.license.licensed_gstin)

    def verify(self, user):
        with patch('gst_tally.services.product_license.step3_connection_check', return_value={
            'read_connected': True, 'can_import': True, 'company_name': 'C', 'company_gstin': self.batch.company_gstin,
        }), patch('gst_tally.services.product_license.read_tally_license', return_value={'license_available': True, 'serial_number': 'TEST-SERIAL'}):
            return pre_import_security_check(self.batch, user, device_fingerprint='PC-A', device_name='PC-A')

    def test_serial_owned_by_other_account_reports_assigned_elsewhere(self):
        result = self.verify(self.other)
        self.assertFalse(result['ready'])
        self.assertEqual(result['verification_result'], 'LICENSE_ASSIGNED_TO_ANOTHER_ACCOUNT')
        self.assertNotIn('PRODUCT_LICENSE_NOT_CONFIGURED', [e['code'] for e in result['errors']])
        self.license.refresh_from_db()
        self.assertEqual(self.license.customer_id, self.owner.id)  # never silently transferred

    def test_superadmin_transfer_revokes_devices_and_requires_reapproval(self):
        from rest_framework.test import APIClient
        from superadmin.models import SuperAdminProfile
        admin = __import__('django.contrib.auth', fromlist=['x']).get_user_model().objects.create_user(username='sa', password='x')
        SuperAdminProfile.objects.create(user=admin)
        c = APIClient(); c.force_authenticate(admin)
        url = f'/api/superadmin/product-licenses/{self.license.id}/transfer-owner/'
        self.assertEqual(c.post(url, {'new_customer_id': self.other.id}, format='json').status_code, 400)  # reason required
        self.assertEqual(c.post(url, {'new_customer_id': self.other.id, 'reason': 'owner change'}, format='json').status_code, 200)
        self.license.refresh_from_db()
        self.assertEqual(self.license.customer_id, self.other.id)
        self.assertEqual(LicensedDevice.objects.get(device_fingerprint='PC-A').status, LicensedDevice.REVOKED)
        from gst_tally.models import LicenseAuditLog
        self.assertTrue(LicenseAuditLog.objects.filter(license=self.license, event_type='LICENSE_OWNER_TRANSFERRED').exists())
        result = self.verify(self.other)
        self.assertFalse(result['ready'])
        self.assertIn('DEVICE_NOT_AUTHORIZED', [e['code'] for e in result['errors']])
