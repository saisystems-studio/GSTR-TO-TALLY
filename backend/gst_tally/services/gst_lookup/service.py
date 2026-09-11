from django.conf import settings
from django.utils import timezone
from datetime import timedelta
from email.utils import parsedate_to_datetime
import time

from .base import (GSTLookupAuthenticationError, GSTLookupConfigurationError,
                   GSTLookupNotFoundError, GSTLookupProviderError, GSTLookupRateLimitError,
                   GSTLookupTimeoutError, has_usable_text)
from .providers import provider_class
from gst_tally.models import GSTParty
from gst_tally.tally.validators import state_name


class GSTLookupService:
    @staticmethod
    def _retry_delay(value):
        if value in (None, ""):
            return 1.0 if settings.GST_LOOKUP_MAX_RETRY_DELAY >= 1 else None
        try: delay = float(value)
        except (TypeError, ValueError):
            try: delay = max(0.0, (parsedate_to_datetime(str(value)) - timezone.now()).total_seconds())
            except (TypeError, ValueError, OverflowError): return None
        return delay if 0 <= delay <= settings.GST_LOOKUP_MAX_RETRY_DELAY else None

    @classmethod
    def _wait_once(cls, rate_error):
        delay = cls._retry_delay(rate_error.retry_after)
        if delay is None: return False
        if delay: time.sleep(delay)
        return True

    @classmethod
    def status(cls):
        enabled = bool(settings.GST_LOOKUP_ENABLED)
        name = str(settings.GST_LOOKUP_PROVIDER or settings.GST_LOOKUP_PRIMARY_PROVIDER or "").strip().lower()
        provider = provider_class(name)
        if not enabled:
            missing = []
        elif not name:
            missing = ["GST_LOOKUP_PROVIDER"]
        elif not provider:
            missing = ["GST_LOOKUP_PROVIDER"]
        elif hasattr(provider, "configuration_issues"):
            missing = provider.configuration_issues()
        else:
            missing = []
            if not settings.GST_LOOKUP_BASE_URL: missing.append("GST_LOOKUP_BASE_URL")
            if name in {"generic_rest", "sandbox"} and not settings.GST_LOOKUP_ENDPOINT and "{gstin}" not in settings.GST_LOOKUP_BASE_URL:
                missing.append("GST_LOOKUP_ENDPOINT")
        fallback_name = str(settings.GST_LOOKUP_FALLBACK_PROVIDER or "").strip().lower()
        fallback = provider_class(fallback_name) if fallback_name else None
        fallback_missing = fallback.configuration_issues() if fallback and hasattr(fallback, "configuration_issues") else []
        result = {"enabled": enabled, "provider": name or None, "registered": bool(provider),
                "configured": bool(enabled and provider and not missing), "missing": missing,
                "fallback_provider": fallback_name or None, "fallback_registered": bool(fallback),
                "fallback_configured": bool(fallback and not fallback_missing)}
        if name == "sandbox" and provider:
            result.update(provider.from_settings().safe_status(), enabled=enabled, registered=True,
                          fallback_provider=fallback_name or None, fallback_registered=bool(fallback),
                          fallback_configured=bool(fallback and not fallback_missing))
        return result

    @classmethod
    def configuration_issues(cls):
        issues = []
        if not settings.GST_LOOKUP_ENABLED: issues.append("GST lookup is disabled")
        primary_name = settings.GST_LOOKUP_PROVIDER or settings.GST_LOOKUP_PRIMARY_PROVIDER
        provider = provider_class(primary_name)
        if not primary_name: issues.append("GST lookup provider is missing")
        elif not provider: issues.append("GST lookup provider is unsupported")
        if provider and hasattr(provider, "configuration_issues"):
            issues.extend(provider.configuration_issues())
        elif provider:
            if not settings.GST_LOOKUP_BASE_URL: issues.append("GST lookup base URL is missing")
            elif str(primary_name).lower() in {"generic_rest", "sandbox"} and not settings.GST_LOOKUP_ENDPOINT and "{gstin}" not in settings.GST_LOOKUP_BASE_URL:
                issues.append("generic REST endpoint must contain {gstin}")
        return issues

    @classmethod
    def configured(cls):
        return cls.status()["configured"]

    @classmethod
    def from_settings(cls):
        return cls.from_provider_name(settings.GST_LOOKUP_PROVIDER or settings.GST_LOOKUP_PRIMARY_PROVIDER)

    @classmethod
    def from_provider_name(cls, provider_name):
        issues = cls.configuration_issues()
        primary_name = settings.GST_LOOKUP_PROVIDER or settings.GST_LOOKUP_PRIMARY_PROVIDER
        if str(provider_name).strip().lower() == str(primary_name).strip().lower() and issues:
            raise GSTLookupConfigurationError("; ".join(issues))
        provider = provider_class(provider_name)
        if not provider: raise GSTLookupConfigurationError("GST lookup provider is unsupported")
        if hasattr(provider, "configuration_issues"):
            provider_issues = provider.configuration_issues()
            if provider_issues: raise GSTLookupConfigurationError("; ".join(provider_issues))
        if hasattr(provider, "from_settings"):
            return cls(provider.from_settings())
        config = {
            "base_url": settings.GST_LOOKUP_BASE_URL,
            "endpoint": settings.GST_LOOKUP_ENDPOINT,
            "api_key": settings.GST_LOOKUP_API_KEY,
            "api_key_header": settings.GST_LOOKUP_API_KEY_HEADER,
            "client_id": settings.GST_LOOKUP_CLIENT_ID,
            "client_secret": settings.GST_LOOKUP_CLIENT_SECRET,
            "username": settings.GST_LOOKUP_USERNAME,
            "password": settings.GST_LOOKUP_PASSWORD,
            "authorization": settings.GST_LOOKUP_AUTHORIZATION,
            "timeout": settings.GST_LOOKUP_TIMEOUT,
        }
        return cls(provider(config))

    def __init__(self, provider): self.provider = provider
    def lookup(self, gstin, **context):
        cleaned = {key: value for key, value in context.items() if value not in (None, False, "", [], {})}
        result = self.provider.lookup(gstin, **cleaned) if cleaned else self.provider.lookup(gstin)
        return result.as_dict()

    @staticmethod
    def _party_data(party):
        clean = lambda value: value if has_usable_text(value) else None
        return {
            "gstin": party.gstin,
            "legal_name": clean(party.legal_name),
            "trade_name": clean(party.trade_name) or clean(party.legal_name),
            "status": clean(party.registration_status),
            "principal_address": clean(party.principal_place_of_business),
            "state": clean(party.state_name),
            "state_code": clean(party.state_code),
            "registration_date": clean(party.registration_date),
            "cancellation_date": clean(party.cancellation_date),
            "constitution_of_business": clean(party.constitution_of_business),
            "taxpayer_type": clean(party.taxpayer_type),
            "pincode": clean(party.pincode),
            "central_jurisdiction": clean(party.central_jurisdiction),
            "centre_jurisdiction": clean(party.central_jurisdiction),
            "state_jurisdiction": clean(party.state_jurisdiction),
            "party_data_status": party.party_data_status or None,
            "lookup_status": party.lookup_status or None,
        }

    @classmethod
    def lookup_cached(cls, gstin, fallback_party_name=None, force_refresh=False, allow_fallback=True, company_gstin=""):
        existing = GSTParty.objects.filter(gstin=gstin).first()
        fresh_after = timezone.now() - timedelta(days=settings.GST_PARTY_FRESH_DAYS)
        existing_gstin = str(getattr(existing, "gstin", "") or gstin).strip().upper()
        has_name = existing and (
            (has_usable_text(existing.trade_name) and str(existing.trade_name).strip().upper() != existing_gstin) or
            (has_usable_text(existing.legal_name) and str(existing.legal_name).strip().upper() != existing_gstin)
        )
        has_address = existing and has_usable_text(existing.principal_place_of_business)
        sandbox_mode = str(settings.GST_LOOKUP_PROVIDER or settings.GST_LOOKUP_PRIMARY_PROVIDER).strip().lower() == "sandbox"
        has_registration = existing and (has_usable_text(existing.registration_status) or has_usable_text(existing.taxpayer_type))
        usable_cached = has_name and (has_address or (sandbox_mode and has_registration))
        if not force_refresh and existing and usable_cached and existing.last_fetched_at and existing.last_fetched_at >= fresh_after:
            if existing.lookup_status != "Existing" or existing.lookup_error:
                existing.lookup_status = "Existing"
                existing.lookup_error = ""
                existing.party_data_status = "Complete"
                existing.save(update_fields=["lookup_status", "lookup_error", "party_data_status", "updated_at"])
            cached = cls._party_data(existing)
            cached["_diagnostics"] = {"gstin": gstin, "configured_provider": str(settings.GST_LOOKUP_PROVIDER or "").lower(),
                "actual_provider_used": existing.lookup_source or str(settings.GST_LOOKUP_PROVIDER or "").lower(), "cache_hit": True,
                "lookup_attempted": False, "sandbox_success": None, "normalized": True, "status": "Existing", "warning": None}
            return "existing", cached
        primary_name = str(settings.GST_LOOKUP_PROVIDER or settings.GST_LOOKUP_PRIMARY_PROVIDER).strip().lower()
        diagnostics = {"gstin": gstin, "configured_provider": primary_name, "provider_requested": primary_name,
                       "actual_provider_used": primary_name, "provider_used": primary_name, "cache_hit": False,
                       "lookup_attempted": False, "http_status": None, "sandbox_success": None, "normalized": False,
                       "jamku_429_count": 0, "gstinapi_429_count": 0,
                       "retry_after_present": False, "fallback_attempted_after_429": False,
                       "retry_count": 0, "unresolved_rate_limited": False,
                       "retry_after": None, "api_calls": 0}
        fallback_name = str(settings.GST_LOOKUP_FALLBACK_PROVIDER or "").strip().lower()
        fallback_ready = allow_fallback and cls.status()["fallback_configured"]
        primary_service = cls.from_settings()
        if primary_name == "sandbox" and hasattr(primary_service.provider, "safe_status"):
            sandbox_status = primary_service.provider.safe_status(company_gstin)
            if isinstance(sandbox_status, dict):
                diagnostics.update(sandbox_access_token_valid=bool(sandbox_status.get("authenticated")),
                                   taxpayer_session_active=bool(sandbox_status.get("taxpayer_session_active")),
                                   provider_configured=bool(sandbox_status.get("provider_configured")),
                                   session_active=bool(sandbox_status.get("session_active")),
                                   session_required=bool(sandbox_status.get("session_required")),
                                   session_expired=bool(sandbox_status.get("session_expired")),
                                   authentication_attempted=False,
                                   otp_required=bool(sandbox_status.get("otp_required")),
                                   lookup_ready=bool(sandbox_status.get("lookup_ready")),
                                   lookup_failed=bool(sandbox_status.get("lookup_failed")))
        try:
            # force_refresh here also means "bypass a cached account-level
            # block" (see SandboxGSTProvider.authenticate's PROVIDER_BLOCK_CACHE_KEY
            # check) -- an explicit user-triggered retry must always genuinely
            # re-check Sandbox, never silently reuse a stale "quota exhausted"
            # verdict from before the user retried.
            if primary_name == "sandbox":
                lookup_kwargs = {"company_gstin": company_gstin}
                if force_refresh:
                    lookup_kwargs["force"] = True
                data = primary_service.lookup(gstin, **lookup_kwargs)
            else:
                data = primary_service.lookup(gstin)
            attempted = bool(getattr(primary_service.provider, "last_lookup_attempted", True))
            diagnostics["lookup_attempted"] = attempted
            request_count = getattr(primary_service.provider, "lookup_request_count", None)
            diagnostics["api_calls"] += request_count if isinstance(request_count, int) else int(attempted)
            diagnostics["http_status"] = getattr(primary_service.provider, "last_http_status", None)
            diagnostics["raw_provider_response"] = getattr(primary_service.provider, "last_response_body", "")
            diagnostics["request_metadata"] = getattr(primary_service.provider, "last_request_metadata", {})
            diagnostics["sandbox_success"] = primary_name == "sandbox"
            diagnostics["normalized"] = True
        except GSTLookupAuthenticationError as exc:
            provider = primary_service.provider
            attempted = bool(getattr(provider, "last_lookup_attempted", False))
            extra = dict(getattr(exc, "diagnostics", {}) or {})
            request_count = getattr(provider, "lookup_request_count", None)
            diagnostics.update(extra, lookup_attempted=attempted,
                               api_calls=request_count if isinstance(request_count, int) else int(attempted), http_status=getattr(provider, "last_http_status", None),
                               provider_code=getattr(provider, "last_provider_code", None),
                               provider_message=getattr(provider, "last_provider_message", None),
                               provider_transaction_id=getattr(provider, "last_provider_transaction_id", None),
                               request_metadata=getattr(provider, "last_request_metadata", {}),
                               response_shape=getattr(provider, "last_response_shape", None),
                               normalized=False, sandbox_success=False)
            exc.lookup_diagnostics = diagnostics
            raise
        except (GSTLookupTimeoutError, GSTLookupProviderError, GSTLookupNotFoundError) as exc:
            provider = primary_service.provider
            attempted = bool(getattr(provider, "last_lookup_attempted", primary_name != "sandbox"))
            request_count = getattr(provider, "lookup_request_count", None)
            diagnostics.update(lookup_attempted=attempted, api_calls=request_count if isinstance(request_count, int) else int(attempted),
                               http_status=getattr(provider, "last_http_status", None),
                               raw_provider_response=getattr(provider, "last_response_body", ""),
                               provider_code=getattr(provider, "last_provider_code", None),
                               provider_message=getattr(provider, "last_provider_message", None),
                               provider_transaction_id=getattr(provider, "last_provider_transaction_id", None),
                               request_metadata=getattr(provider, "last_request_metadata", {}),
                               response_shape=getattr(provider, "last_response_shape", None),
                               normalized=False, sandbox_success=False)
            exc.lookup_diagnostics = diagnostics
            raise
        except GSTLookupRateLimitError as exc:
            diagnostics[f"{primary_name}_429_count"] = diagnostics.get(f"{primary_name}_429_count", 0) + 1
            diagnostics["retry_after_present"] = bool(exc.retry_after)
            diagnostics["retry_after"] = exc.retry_after
            if fallback_ready:
                diagnostics["fallback_attempted_after_429"] = True
                data = cls._party_data(existing) if existing else {"gstin": gstin}
            elif cls._wait_once(exc):
                diagnostics["retry_count"] += 1
                try:
                    diagnostics["api_calls"] += 1
                    data = primary_service.lookup(gstin)
                except GSTLookupRateLimitError as retry_exc:
                    retry_exc.retry_count = 1
                    retry_exc.rate_count = 2
                    raise
            else:
                raise
        if existing:
            cached = cls._party_data(existing)
            for key in ("legal_name", "trade_name", "status", "principal_address", "state", "state_code",
                        "registration_date", "cancellation_date", "constitution_of_business",
                        "taxpayer_type", "pincode", "central_jurisdiction", "centre_jurisdiction",
                        "state_jurisdiction"):
                if not has_usable_text(data.get(key)) and has_usable_text(cached.get(key)):
                    data[key] = cached[key]
        existing_name = None
        if existing:
            existing_name = existing.trade_name if has_usable_text(existing.trade_name) else existing.legal_name
        data["trade_name"] = data.get("trade_name") or data.get("legal_name") or existing_name or fallback_party_name
        data["state"] = data.get("state") or state_name(str(gstin)[:2])
        def save_result(values, source, lookup_status):
            missing_parts = []
            if not has_usable_text(values.get("trade_name")): missing_parts.append("name")
            if primary_name == "sandbox" and not has_usable_text(values.get("state")): missing_parts.append("state")
            address_missing = not has_usable_text(values.get("principal_address"))
            if address_missing and primary_name != "sandbox": missing_parts.append("address")
            reason = f'Provider did not return {" and ".join(missing_parts)}' if missing_parts else ""
            effective_status = "Incomplete" if missing_parts else lookup_status
            model_fields = {
                "legal_name": values.get("legal_name"), "trade_name": values.get("trade_name"),
                "address": values.get("principal_address"),
                "principal_place_of_business": values.get("principal_address"), "state_name": values.get("state"),
                "state_code": values.get("state_code"),
                "registration_status": values.get("status"), "registration_date": values.get("registration_date"),
                "cancellation_date": values.get("cancellation_date"),
                "constitution_of_business": values.get("constitution_of_business"),
                "taxpayer_type": values.get("taxpayer_type"), "pincode": values.get("pincode"),
                "central_jurisdiction": values.get("central_jurisdiction") or values.get("centre_jurisdiction"),
                "state_jurisdiction": values.get("state_jurisdiction"),
                "lookup_source": source, "lookup_status": effective_status, "lookup_error": reason,
                "party_data_status": "Incomplete" if missing_parts else "Complete", "last_fetched_at": timezone.now(),
            }
            defaults = {key: value for key, value in model_fields.items() if value not in (None, "")}
            defaults["retry_not_before"] = None
            return GSTParty.objects.update_or_create(gstin=gstin, defaults=defaults)[0]

        party = save_result(data, primary_name, "Fetched")
        diagnostics["status"] = "Fetched" if party.party_data_status == "Complete" else "Sandbox Lookup Failed" if primary_name == "sandbox" else "Incomplete"
        diagnostics["warning"] = "Address unavailable" if primary_name == "sandbox" and not has_usable_text(data.get("principal_address")) else None
        used_fallback = False
        if party.party_data_status == "Incomplete" and fallback_ready and fallback_name != primary_name:
            try:
                fallback_service = cls.from_provider_name(fallback_name)
                try:
                    diagnostics["api_calls"] += 1
                    fallback_data = fallback_service.lookup(gstin)
                except GSTLookupRateLimitError as exc:
                    diagnostics[f"{fallback_name}_429_count"] = diagnostics.get(f"{fallback_name}_429_count", 0) + 1
                    diagnostics["retry_after_present"] = diagnostics["retry_after_present"] or bool(exc.retry_after)
                    diagnostics["retry_after"] = exc.retry_after or diagnostics["retry_after"]
                    if not cls._wait_once(exc): raise
                    diagnostics["retry_count"] += 1
                    diagnostics["api_calls"] += 1
                    fallback_data = fallback_service.lookup(gstin)
                for key, value in fallback_data.items():
                    if has_usable_text(value) and not has_usable_text(data.get(key)): data[key] = value
                data["trade_name"] = data.get("trade_name") or data.get("legal_name") or existing_name or fallback_party_name
                party = save_result(data, fallback_name, "Fetched via Fallback")
                diagnostics["actual_provider_used"] = diagnostics["provider_used"] = fallback_name
                used_fallback = party.party_data_status == "Complete"
                party.lookup_source = f"{primary_name}+{fallback_name}"
                party.save(update_fields=["lookup_source", "updated_at"])
            except GSTLookupConfigurationError:
                party.lookup_error = "fallback_configuration_error"
                party.save(update_fields=["lookup_error", "updated_at"])
            except GSTLookupAuthenticationError:
                party.lookup_error = "fallback_auth_failed"
                party.save(update_fields=["lookup_error", "updated_at"])
            except GSTLookupNotFoundError:
                party.lookup_error = "fallback_not_found"
                party.save(update_fields=["lookup_error", "updated_at"])
            except GSTLookupTimeoutError:
                party.lookup_error = "fallback_timeout"
                party.save(update_fields=["lookup_error", "updated_at"])
            except GSTLookupRateLimitError as exc:
                diagnostics[f"{fallback_name}_429_count"] = diagnostics.get(f"{fallback_name}_429_count", 0) + 1
                diagnostics["unresolved_rate_limited"] = True
                party.lookup_status = "Rate Limited"
                party.lookup_error = f"{fallback_name} rate limit"
                party.save(update_fields=["lookup_status", "lookup_error", "updated_at"])
            except GSTLookupProviderError as exc:
                known = {"fallback_invalid_json", "fallback_empty_response", "fallback_schema_mismatch",
                         "fallback_normalization_failed", "fallback_5xx"}
                code = str(exc) if str(exc) in known else "fallback_provider_error"
                party.lookup_error = code
                party.save(update_fields=["lookup_error", "updated_at"])
            except Exception:
                party.lookup_error = "fallback_normalization_failed"
                party.save(update_fields=["lookup_error", "updated_at"])
        normalized = dict(data)
        normalized["_diagnostics"] = diagnostics
        for key, value in cls._party_data(party).items():
            if normalized.get(key) in (None, "", []): normalized[key] = value
        return party.lookup_source or ("fallback" if used_fallback else primary_name), normalized
