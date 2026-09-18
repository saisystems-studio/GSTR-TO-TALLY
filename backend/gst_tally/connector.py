"""Outbound connector API. Credentials never enter browser responses or logs."""
import hashlib
import re
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from .models import ConnectorEnrollment, LicensedDevice, LocalTallyAgent, LocalTallyJob, ProductLicense


def digest(value):
    return hashlib.sha256(str(value).encode()).hexdigest()


def fingerprint(request):
    return str(request.headers.get('X-Device-Fingerprint') or request.headers.get('X-Device-ID') or '')[:128]


def online(agent):
    return bool(agent and agent.last_seen_at and
                (timezone.now() - agent.last_seen_at).total_seconds() <= 45)


def license_allowed(license):
    from subscriptions.services import check_request_block
    return (license.customer.is_active and license.status in ('ACTIVE', 'EXPIRING')
            and (not license.expiry_date or license.expiry_date >= timezone.localdate())
            and check_request_block(license.customer) is None)


def verified(agent):
    return bool(online(agent) and agent.tally_reachable and license_allowed(agent.device.license)
                and agent.device.status == LicensedDevice.ACTIVE
                and agent.detected_serial and agent.detected_company_gstin
                and agent.detected_serial == agent.device.license.licensed_tally_serial
                and agent.detected_company_gstin == agent.device.license.licensed_gstin)


def find_agent(user, device_fingerprint):
    if not device_fingerprint:
        return None
    return LocalTallyAgent.objects.select_related('device__license__customer').filter(
        device__license__customer=user, browser_device=device_fingerprint,
        device__status=LicensedDevice.ACTIVE).order_by('-last_seen_at').first()


def snapshot(agent):
    connected = online(agent)
    reachable = bool(connected and agent.tally_reachable)
    ready = verified(agent)
    label = ('Connector Not Installed' if not agent else 'Connecting...' if not connected
             else 'Tally Not Running' if not reachable else
             'Ready to Import' if ready else 'Company Verification Required')
    return {**(agent.identity if agent and reachable else {}), 'status': label, 'agent_status': label, 'connected': connected, 'verified': ready,
            'tally_reachable': reachable, 'tally_connected': reachable,
            'company_open': bool(reachable and agent.detected_company_name),
            'read_connected': reachable, 'http_connected': reachable, 'can_import': ready,
            'company_name': agent.detected_company_name if reachable else '',
            'company_gstin': agent.detected_company_gstin if reachable else '',
            'serial_number': agent.detected_serial if reachable else '',
            'message': label, 'transport': 'XML', 'odbc_connected': reachable,
            'download_url': getattr(settings, 'CONNECTOR_DOWNLOAD_URL', ''),
            'last_seen_at': agent.last_seen_at.isoformat() if agent and agent.last_seen_at else None}


def authenticated_agent(request):
    token = request.headers.get('X-Tally-Agent-Token', '')
    agent = LocalTallyAgent.objects.select_related('device__license__customer').filter(
        token_hash=digest(token), device__status=LicensedDevice.ACTIVE).first() if token else None
    if not agent:
        exc = APIException('Invalid connector credential.')
        exc.status_code = 401
        raise exc
    return agent


class EnrollmentThrottle(AnonRateThrottle):
    rate = '30/hour'


class PublicConnectorView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]


class EnrollView(PublicConnectorView):
    throttle_classes = [EnrollmentThrottle]

    def post(self, request):
        try:
            installation = uuid.UUID(str(request.data.get('installation_id')))
        except (ValueError, TypeError):
            raise ValidationError('Invalid installation identity.')
        hashes = [str(request.data.get(key, '')) for key in ('poll_hash', 'proof_hash')]
        if not all(re.fullmatch('[a-f0-9]{64}', value) for value in hashes):
            raise ValidationError('Invalid enrollment proof.')
        row = ConnectorEnrollment.objects.create(installation_id=installation, poll_hash=hashes[0],
            proof_hash=hashes[1], expires_at=timezone.now()+timedelta(hours=24))
        return Response({'enrollment_id': str(row.enrollment_id)}, status=201)


class PairView(APIView):
    @transaction.atomic
    def post(self, request):
        row = get_object_or_404(ConnectorEnrollment.objects.select_for_update(),
                              enrollment_id=request.data.get('enrollment_id'))
        if (row.expires_at <= timezone.now() or row.consumed_at or row.owner_id
                or row.proof_hash != digest(request.data.get('proof', '')) or not fingerprint(request)):
            raise ValidationError('Enrollment is invalid or already used. Open the connector to reconnect.')
        row.owner, row.browser_device = request.user, fingerprint(request)
        row.save(update_fields=['owner', 'browser_device'])
        return Response({'paired': True})


class ClaimEnrollmentView(PublicConnectorView):
    @transaction.atomic
    def post(self, request):
        row = get_object_or_404(ConnectorEnrollment.objects.select_for_update(),
                              enrollment_id=request.data.get('enrollment_id'))
        token = str(request.data.get('token', ''))
        if (row.expires_at <= timezone.now() or row.poll_hash != digest(request.data.get('poll_secret', ''))
                or not re.fullmatch(r'[A-Za-z0-9_-]{64}', token)):
            raise ValidationError('Invalid enrollment.')
        # Client chooses and securely saves a random credential before exchange.
        # Lost responses can be recovered without storing plaintext on the VPS.
        if row.consumed_at:
            if row.agent and row.agent.token_hash == digest(token):
                return Response(self.credentials(row.agent))
            raise ValidationError('Enrollment already consumed.')
        if not row.owner_id:
            return Response({'status': 'Waiting for website sign-in'}, status=202)
        license = ProductLicense.objects.select_for_update().filter(customer=row.owner,
            licensed_tally_serial=str(request.data.get('tally_serial', '')).strip(),
            licensed_gstin=str(request.data.get('company_gstin', '')).strip().upper()).first()
        if not license or not license_allowed(license):
            return Response({'status': 'Company Verification Required'}, status=202)
        device = LicensedDevice.objects.filter(license=license, device_fingerprint=str(row.installation_id)).first()
        if device and device.status != LicensedDevice.ACTIVE:
            return Response({'detail': 'Device requires administrator authorization.'}, status=403)
        if not device:
            if license.licensed_devices.filter(status=LicensedDevice.ACTIVE).count() >= license.allowed_devices:
                return Response({'detail': 'Device limit reached.'}, status=403)
            device = LicensedDevice.objects.create(license=license, device_fingerprint=str(row.installation_id),
                                                  device_name='GSTR2Tally Windows Connector')
        agent, _ = LocalTallyAgent.objects.update_or_create(device=device, defaults={
            'token_hash': digest(token), 'installation_id': row.installation_id,
            'browser_device': row.browser_device,
            'last_seen_at': None, 'tally_reachable': False})
        row.agent, row.consumed_at = agent, timezone.now()
        row.save(update_fields=['agent', 'consumed_at'])
        return Response(self.credentials(agent))

    @staticmethod
    def credentials(agent):
        return {'agent_id': agent.pk, 'user_id': agent.device.license.customer_id,
                'device_id': agent.device_id, 'installation_id': str(agent.installation_id),
                'company_gstin': agent.device.license.licensed_gstin,
                'tally_serial': agent.device.license.licensed_tally_serial}


class HeartbeatView(PublicConnectorView):
    def post(self, request):
        agent = authenticated_agent(request)
        identity = {key: str(request.data.get(key, ''))[:100] for key in
                    ('company_state', 'financial_year_from', 'financial_year_to', 'books_from')}
        LocalTallyAgent.objects.filter(pk=agent.pk).update(
            identity=identity,
            detected_serial=str(request.data.get('tally_serial', '')).strip()[:64],
            detected_company_gstin=str(request.data.get('company_gstin', '')).strip().upper()[:15],
            detected_company_name=str(request.data.get('company_name', ''))[:255],
            tally_reachable=request.data.get('tally_reachable') is True, last_seen_at=timezone.now())
        agent.refresh_from_db()
        return Response(snapshot(agent))


class StatusView(APIView):
    def get(self, request):
        agent = find_agent(request.user, fingerprint(request))
        result = snapshot(agent)
        if not agent and ConnectorEnrollment.objects.filter(owner=request.user,
                browser_device=fingerprint(request), consumed_at__isnull=True,
                expires_at__gt=timezone.now()).exists():
            result.update(status='Connecting...', message='Open Tally and the registered company.')
        return Response(result)


class NextJobView(PublicConnectorView):
    @transaction.atomic
    def post(self, request):
        agent = authenticated_agent(request)
        if not license_allowed(agent.device.license):
            return Response({'detail': 'License is unavailable.'}, status=403)
        if not verified(agent):
            return Response({'detail': 'Connector or company is not ready.'}, status=409)
        # Lock the agent row as well as the job: only one outstanding job/device.
        LocalTallyAgent.objects.select_for_update().get(pk=agent.pk)
        jobs = LocalTallyJob.objects.filter(agent=agent)
        jobs.filter(status=LocalTallyJob.QUEUED, expires_at__lte=timezone.now()).update(status=LocalTallyJob.EXPIRED)
        job = jobs.select_for_update().filter(status=LocalTallyJob.SENDING).order_by('created_at').first()
        if not job:
            job = jobs.select_for_update().filter(status=LocalTallyJob.QUEUED,
                expires_at__gt=timezone.now()).order_by('created_at').first()
        if not job:
            return Response(status=204)
        if (job.batch.uploaded_by_id != agent.device.license.customer_id or
                job.batch.company_gstin != agent.device.license.licensed_gstin or
                job.batch.product_license_id != agent.device.license_id):
            job.status, job.error_message = LocalTallyJob.FAILED, 'Job scope mismatch.'
            job.save(update_fields=['status', 'error_message'])
            return Response({'detail': 'Job scope mismatch.'}, status=403)
        job.status = LocalTallyJob.SENDING
        job.claimed_at = job.claimed_at or timezone.now()
        job.save(update_fields=['status', 'claimed_at'])
        return Response({'job_id': str(job.job_id), 'idempotency_key': job.idempotency_key,
            'expires_at': job.expires_at.isoformat(), 'scope': ClaimEnrollmentView.credentials(agent),
            'payload': job.payload})


class ResultView(PublicConnectorView):
    @transaction.atomic
    def post(self, request, job_id):
        agent = authenticated_agent(request)
        job = get_object_or_404(LocalTallyJob.objects.select_for_update(), job_id=job_id, agent=agent)
        if job.status in (LocalTallyJob.SUCCESS, LocalTallyJob.FAILED, LocalTallyJob.RETRYABLE):
            return Response({'accepted': True, 'status': job.status})
        if job.status != LocalTallyJob.SENDING:
            return Response({'detail': 'Job was not claimed.'}, status=409)
        success = request.data.get('success')
        ack = request.data.get('acknowledgement') or {}
        raw = ack.get('raw_response') if isinstance(ack, dict) else None
        if type(success) is not bool or (success and (not isinstance(raw, str) or not raw.strip())):
            raise ValidationError('A real Tally response is required.')
        if isinstance(raw, str) and len(raw.encode()) > 16 * 1024 * 1024:
            raise ValidationError('Response exceeds limit.')
        job.status = LocalTallyJob.SUCCESS if success else LocalTallyJob.FAILED
        job.acknowledgement = {'raw_response': raw} if raw else {}
        job.error_message = str(request.data.get('error', ''))[:1000]
        job.completed_at = timezone.now()
        job.save(update_fields=['status', 'acknowledgement', 'error_message', 'completed_at'])
        return Response({'accepted': True, 'status': job.status})
