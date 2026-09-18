"""Request/thread scoped transport; never a process-global customer selection."""
from contextvars import ContextVar
from django.conf import settings
from django.http import JsonResponse
from .connector import find_agent, fingerprint, snapshot
from .models import GSTImportBatch, TallyImportJob

current_agent = ContextVar('tally_connector_agent', default=None)
current_batch = ContextVar('tally_connector_batch', default=None)


def connection_snapshot():
    agent = current_agent.get()
    if agent:
        agent.refresh_from_db()
    result = snapshot(agent)
    result.setdefault('company_state', '')
    result['state'] = result['company_state']
    result['company'] = result['company_name']
    result['gstin'] = result['company_gstin']
    return result


def relay_client():
    from .tally.client import TallyConnectionError
    from .tally.local_agent_transport import LocalAgentTallyClient
    agent, batch = current_agent.get(), current_batch.get()
    if not agent or not batch:
        raise TallyConnectionError('TALLY_AGENT_OFFLINE', 'Connect the registered Windows connector.')
    return LocalAgentTallyClient(agent, batch)


class ConnectorContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        agent_token, batch_token = current_agent.set(None), current_batch.set(None)
        try:
            return self.get_response(request)
        finally:
            current_agent.reset(agent_token)
            current_batch.reset(batch_token)

    def process_view(self, request, view_func, view_args, view_kwargs):
        if not getattr(settings, 'TALLY_LOCAL_AGENT_REQUIRED', False) or not request.path.startswith('/api/gst-tally/'):
            return None
        from rest_framework_simplejwt.authentication import JWTAuthentication
        try:
            auth = JWTAuthentication().authenticate(request)
        except Exception:
            return None
        if not auth:
            return None
        user = auth[0]
        current_agent.set(find_agent(user, fingerprint(request)))
        batch_id = view_kwargs.get('pk')
        if not batch_id and 'job_id' in view_kwargs and '/tally-import/' in request.path:
            batch_id = TallyImportJob.objects.filter(job_id=view_kwargs['job_id']).values_list('batch_id', flat=True).first()
        if batch_id:
            batch = GSTImportBatch.objects.filter(pk=batch_id, uploaded_by=user).first()
            if not batch:
                return JsonResponse({'detail': 'Batch not found.'}, status=404)
            current_batch.set(batch)
