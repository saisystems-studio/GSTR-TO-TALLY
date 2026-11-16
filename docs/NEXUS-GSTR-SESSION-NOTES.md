# GSTR <-> Nexus: session validation and revocation

- Browser -> GSTR backend (`/api/nexus/*`) -> Nexus. The product key (`NEXUS_PRODUCT_KEY`) is backend-only env; never in the browser/EXE.
- Local JWT is issued ONLY after Nexus login/activation succeeds AND `license.authorized == true`. The Nexus token is stored Fernet-encrypted in `nexus_auth_nexuslink` and never sent to the browser.
- Every API request passes `NexusAwareJWTAuthentication`: a linked user is rejected if the link is revoked or `lease_until` has passed.
- The frontend polls `/api/nexus/session/` every 5 min; a Nexus 401 `session_revoked` (device replaced, password changed, logout) revokes the local link at once -> next request 401.
- Nexus unreachable: login is denied; an existing session continues only until the Nexus-issued `lease_until` (default 7 days, `TALLY_LEASE_DAYS` on Nexus).
- Config: `NEXUS_ENABLED` (default False; UI unchanged when off), `NEXUS_ENFORCE`, `NEXUS_BASE_URL`, `NEXUS_PRODUCT_ID`, `NEXUS_PRODUCT_KEY`, `NEXUS_TIMEOUT`.
- Rollback: set `NEXUS_ENABLED=False`, `NEXUS_ENFORCE=False`; `migrate nexus_auth zero` drops only `nexus_auth_nexuslink`.

## Update: device-bound sessions, first-time serial discovery, distribution
- Each local JWT carries a `nexus_link` claim = one `NexusLink` per (Tally serial, client device). Replacing a device revokes the old device's Nexus token, so its link/JWT is rejected on the next check (`session_revoked`). Refresh of a revoked link is refused.
- Accounts that have Nexus links can never fall back to an unbound JWT.
- First-time serial: connector creates an enrollment, opens `/#connector=<id>.<proof>`; the browser pairs (`/api/nexus/connector/pair/`, proof-checked), the connector posts the Tally-read serial/metadata (`/api/nexus/connector/discovery/`, poll-secret-checked). Registration uses only that serial; client-supplied serials are ignored.
- The GSTR backend runs on the VPS (not distributed to customer PCs), so `NEXUS_PRODUCT_KEY` stays in its environment. The connector EXE contains only the GSTR origin (`release.json`), never Nexus URL/keys.
- Edition / license-administrator come from experimental `$$LicenseInfo` params (unverified against real TallyPrime). Not obtainable via the current connector: TSS details, virtual user limit, gateway server, registered email, TSS expiry.

## Rollout: enabling NEXUS_ENFORCE safely (keep it False until step 5)
1. Run `python manage.py nexus_enforce_readiness` (read-only). It lists active customer accounts that have no Nexus link and would be blocked.
2. Migrate each legacy customer: they sign up / sign in through Nexus (serial + password), then activate the device. Their old local account is left untouched.
3. Run `python manage.py deliver_nexus_events` on a schedule (every 5 min). It prints whether the signing secret / events path are configured (never the values).
4. Set `NEXUS_EVENT_SIGNING_SECRET` (same value as Nexus `TALLY_EVENT_SIGNING_SECRET`) and confirm with the Nexus team that `events/device-transfer/` is deployed (it returned HTTP 404 on production when checked). Until then events stay PENDING, retried hourly, and are never lost.
5. Only when the readiness report shows 0 legacy accounts (or each is explicitly accepted), set `NEXUS_ENFORCE=True` in a staging copy first, then production. Rollback = set it back to `False`; no data changes.
- Nexus-linked customers use ONLY Nexus Activate User for a new laptop (the local transfer popup/API returns `USE_NEXUS_FLOW` for them). On a confirmed replacement the old device's local session is revoked immediately and a `DEVICE_TRANSFER_COMPLETED` event is queued in the outbox.
