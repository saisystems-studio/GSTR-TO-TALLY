const available = value => value || value === 0 ? value : 'Not Available'

const row = (label, value, tone) => ({ label, value: available(value), ...(tone ? { tone } : {}) })

export function profileDeviceDetails(data = {}) {
  const company = data.company
  const system = data.system_configuration || {}
  const device = data.device_authentication || {}

  return {
    company: company ? [
      row('Company Name', company.company_name),
      row('Company GSTIN', company.gstin),
      row('State', company.state),
      row('State Code', company.state_code),
      row('Financial Year', company.financial_year),
      row('Company Status', company.verified ? 'Verified' : 'Not Verified', company.verified ? 'verified' : undefined),
    ] : null,
    system: [
      row('Device Name', system.device_name),
      row('Processor', system.processor),
      row('RAM', system.ram),
      row('Total Storage', system.total_storage),
      row('Available Storage', system.available_storage),
      row('Operating System', system.operating_system),
      row('System Type', system.system_type),
      row('App Version', system.app_version),
    ],
    device: [
      row('Device Name', device.device_name),
      row('Device ID', device.device_id),
      row('First Seen', device.first_seen),
      row('Last Seen', device.last_seen),
      row('Status', device.status === 'AUTHORIZED' ? 'Authorized' : available(device.status), device.status === 'AUTHORIZED' ? 'verified' : undefined),
    ],
  }
}
