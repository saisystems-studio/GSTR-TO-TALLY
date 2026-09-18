export function step3Ready(result) {
  return result?.ready === true && result.tally_connected === true
    && result.company?.gstin_match === true && result.tally_license?.match === true
    && result.product?.allowed === true && result.device?.authorized === true
    && result.device?.limit_allowed === true && result.errors?.length === 0
}

export function step3Rows(result) {
  const value = input => input === null || input === undefined || input === '' ? 'Not detected' : input
  const usage = item => item?.limit == null ? 'Not configured' : `${item.used}/${item.limit} Used`
  return [
    ['Tally Connection', 'Connected', result.tally_connected ? 'Connected' : 'Disconnected', result.tally_connected, 'Tally Connected'],
    ['Company GSTIN', value(result.company?.source_gstin), value(result.company?.tally_gstin), result.company?.gstin_match, 'Correct Company Opened — Company GSTIN Verified'],
    ['Tally License', result.tally_license?.registered_serial || 'Not Configured', value(result.tally_license?.detected_serial), result.tally_license?.match, 'Registered Tally License In Use'],
    ['Product Limit', result.product?.limit ?? 'Not Configured', usage(result.product), result.product?.allowed, 'Product Allowed'],
  ]
}
