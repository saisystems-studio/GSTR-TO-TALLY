export const authModalShouldOpen = (requested, status) => Boolean(
  requested && status?.configured && !status?.taxpayer_session_active
)

export const normalizeSandboxError = error => String(error?.message || 'Sandbox GST request failed.')
  .replace(/^OTP request failed:\s*/i, '')
  .trim()

const GSTIN_PATTERN = /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$/

export const fetchPartyDetailsAvailability = (rows = [], processing = false) => {
  const unresolvedCount = rows.filter(row =>
    GSTIN_PATTERN.test(String(row?.gstin || '').trim().toUpperCase()) && !row?.tally_ready
  ).length
  return { disabled: Boolean(processing || unresolvedCount === 0), unresolvedCount }
}

// Normal Party Details lookup uses the Sandbox public GSTIN search endpoint, which only needs
// application-level Sandbox credentials (SANDBOX_API_KEY/SANDBOX_API_SECRET) — never a taxpayer
// OTP/session. `providerStatus.lookup_ready` already reflects that (see safe_status() on the backend).
export const lookupAvailability = providerStatus => {
  if (!providerStatus) return { ready: false, message: '' }
  if (!providerStatus.enabled) return { ready: false, message: 'GST lookup is currently disabled.' }
  if (!providerStatus.configured) return { ready: false, message: `Sandbox configuration missing: ${(providerStatus.missing || []).join(', ') || 'required backend settings'}.` }
  const sandboxProvider = !providerStatus.provider || providerStatus.provider === 'sandbox'
  if (sandboxProvider && !providerStatus.lookup_ready) return { ready: false, message: 'GST Sandbox is not configured for GSTIN lookup.' }
  return { ready: true, message: 'GST Lookup ready.' }
}
