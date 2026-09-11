const value = v => (v === undefined || v === null || v === '' ? '-' : String(v))

export function licenseFailureUi(result = {}) {
  // A license with no registered Tally Serial is an administrative setup
  // gap, not a transient Tally/connection problem -- retrying the check can
  // never fix it, so this always wins regardless of which endpoint (the
  // dedicated verify call vs a pre-import check) reported it, before the
  // generic PRE_IMPORT_LICENSE_FAILED wrapper below would otherwise hide it.
  if (result.verification_result === 'PRODUCT_LICENSE_NOT_CONFIGURED') {
    return {
      title: 'License Setup Required',
      detail: 'No Tally Serial is registered for this product license yet. Contact your administrator to complete license setup before importing.',
      actions: ['Contact Administrator'],
      tone: 'error',
    }
  }

  const code = result.code === 'PRE_IMPORT_LICENSE_FAILED'
    ? 'PRE_IMPORT_LICENSE_FAILED'
    : result.verification_result || result.code || ''

  if (code === 'PRE_IMPORT_LICENSE_FAILED') {
    return {
      title: 'Import Blocked',
      detail: `The currently connected Tally installation does not match this GSTR 2 Tally license. Registered Tally Serial: ${value(result.registered_tally_serial)}. Current Tally Serial: ${value(result.detected_tally_serial)}. No vouchers were sent to Tally.`,
      actions: ['Recheck Tally', 'Contact Administrator'],
      tone: 'error',
    }
  }
  if (code === 'TALLY_SERIAL_MISMATCH') {
    return {
      title: 'Tally License Mismatch',
      detail: `This GSTR 2 Tally license is registered to another Tally installation. Registered Tally Serial: ${value(result.registered_tally_serial)}. Detected Tally Serial: ${value(result.detected_tally_serial)}.`,
      actions: ['Contact Administrator'],
      tone: 'error',
    }
  }
  if (code === 'DEVICE_LIMIT_REACHED' || code === 'DEVICE_CHANGE_DETECTED') {
    return {
      title: 'Device Approval Required',
      detail: `This license is already active on another device. Registered Device: ${value(result.registered_device)}.`,
      actions: ['Contact Administrator'],
      tone: 'warning',
    }
  }
  if (code === 'COMPANY_GSTIN_MISMATCH') {
    return {
      title: 'Company GSTIN Mismatch',
      detail: `Licensed GSTIN: ${value(result.licensed_gstin)}. Current GSTIN: ${value(result.current_company_gstin)}.`,
      actions: ['Contact Administrator'],
      tone: 'error',
    }
  }
  if (code === 'SOURCE_COMPANY_GSTIN_MISSING') {
    return {
      title: 'Company GSTIN Not Found in Return',
      detail: 'Unable to identify the company GSTIN from the uploaded return.',
      actions: ['Contact Administrator'],
      tone: 'error',
    }
  }
  if (code === 'LICENSE_EXPIRED') {
    return {
      title: 'Subscription Expired',
      detail: `Your GSTR 2 Tally subscription expired on: ${value(result.expiry_date)}.`,
      actions: ['Contact Administrator'],
      tone: 'error',
    }
  }
  return {
    title: 'License Verification Failed',
    detail: result.message || 'Protected GSTR 2 Tally functions are blocked.',
    actions: ['Contact Administrator'],
    tone: 'error',
  }
}
