import test from 'node:test'
import assert from 'node:assert/strict'

import { profileDeviceDetails } from './profileDeviceDetails.js'

test('web and EXE device payloads use the same profile fields', () => {
  const details = profileDeviceDetails({
    company: { company_name: 'GSTRCOMPANY', gstin: '33AFHPM6103Q1Z8', state: 'Tamil Nadu', state_code: '33', financial_year: '2025-26', verified: true },
    system_configuration: { device_name: 'DESKTOP-ABC123', processor: 'Intel CPU', ram: '16 GB', total_storage: '512 GB', available_storage: '268 GB', operating_system: 'Windows 11', system_type: '64-bit', app_version: '1.0.0' },
    device_authentication: { device_name: 'DESKTOP-ABC123', device_id: 'DEV-123', first_seen: '2026-01-01', last_seen: '2026-09-09', status: 'AUTHORIZED', windows_version: 'must-not-leak', app_version: 'must-not-leak' },
    tally_license: { registered_tally_serial: 'must-not-leak', tally_edition: 'must-not-leak' },
  })

  assert.deepEqual(details.company.map(row => row.label), ['Company Name', 'Company GSTIN', 'State', 'State Code', 'Financial Year', 'Company Status'])
  assert.deepEqual(details.system.map(row => row.label), ['Device Name', 'Processor', 'RAM', 'Total Storage', 'Available Storage', 'Operating System', 'System Type', 'App Version'])
  assert.deepEqual(details.device.map(row => row.label), ['Device Name', 'Device ID', 'First Seen', 'Last Seen', 'Status'])
  assert.equal(details.device[1].value, 'DEV-123')
  assert.equal(details.device[4].value, 'Authorized')
  assert.equal(JSON.stringify(details).includes('must-not-leak'), false)
})

test('missing individual system values remain available as independent fallbacks', () => {
  const details = profileDeviceDetails({ system_configuration: { device_name: 'OFFICE-PC' } })

  assert.equal(details.system[0].value, 'OFFICE-PC')
  assert.equal(details.system[1].value, 'Not Available')
  assert.equal(details.company, null)
})
