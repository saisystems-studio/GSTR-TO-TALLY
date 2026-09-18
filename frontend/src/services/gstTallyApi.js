import { authenticatedFetch } from './authApi'
import { buildQueryUrl } from '../utils/url'

// Keep API requests same-origin in the browser so Vite proxies them to the
// Django development server. A build must not accidentally point at a stale
// localhost port from an environment override.
const BASE = '/api/gst-tally'
// Mirrors authApi.js's own 'gst-session-expired' event -- the subscription
// enforcement middleware (backend subscriptions/middleware.py) returns this
// exact {code, detail, expiry_date} shape (HTTP 402) for any blocked GSTR 2
// Tally operation. Dispatched here (not just left as a thrown error) so the
// app can redirect to /subscription-expired the instant ANY protected call
// is blocked, not only when the screen that happened to call it bothers to
// check error.code itself.
const SUBSCRIPTION_BLOCKED_EVENT = 'gst-subscription-blocked'
async function request(path, options={}){try{const response=await authenticatedFetch(`${BASE}${path}`,options);let body={};try{body=await response.json()}catch{}if(!response.ok){if(response.status===402&&(body.code==='SUBSCRIPTION_EXPIRED'||body.code==='SUBSCRIPTION_SUSPENDED')){window.dispatchEvent(new CustomEvent(SUBSCRIPTION_BLOCKED_EVENT,{detail:body}))}const error=new Error(body.detail||body.message||'Request failed');Object.assign(error,body);throw error}return body}catch(error){if(error.name==='AbortError')throw new Error('Tally did not complete the request within the allowed time. Check Tally for an open dialog before retrying.');throw error}}
export function uploadGSTFile({returnType,returnPeriod,file}){const data=new FormData();data.append('return_type',returnType);data.append('return_period',returnPeriod);data.append('file',file);return request('/import/',{method:'POST',body:data})}
export function previewGSTFile({returnType,file,sheetName}){const data=new FormData();data.append('return_type',returnType);data.append('file',file);if(sheetName)data.append('sheet_name',sheetName);return request('/preview/',{method:'POST',body:data})}
export const getImportBatch=id=>request(`/batches/${id}/`)
export const getBatchPreview=(id,{page=1,pageSize=50,search=''}={})=>request(`/batches/${id}/preview/?${new URLSearchParams({page:String(page),page_size:String(pageSize),...(search ? {search} : {})})}`)
export async function downloadBatchPreviewPdf(id) {
  const response = await authenticatedFetch(`${BASE}/batches/${id}/preview/pdf/`)
  if (!response.ok) throw new Error('Unable to generate PDF.')
  return response.blob()
}
export const getImportHistory=()=>request('/batches/')
export const fetchBatchParties=(id,{force=false,retryIncomplete=false,gstins=[]}={})=>request(`/import-batches/${id}/fetch-parties/`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({force,retry_incomplete:retryIncomplete,gstins})})
export const getBatchParties=id=>request(`/import-batches/${id}/parties/`)
export const completeBatchParty=(id,gstin,details)=>request(`/import-batches/${id}/parties/${encodeURIComponent(gstin)}/`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(details)})
export const prepareTallyMasters=(id,tallyCompanyName='')=>request(`/import-batches/${id}/tally-masters/prepare/`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({tally_company_name:tallyCompanyName})})
export const verifyCompanyName=(id,tallyCompanyName='')=>request(`/import-batches/${id}/company/verify/`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({tally_company_name:tallyCompanyName})})
export const resolveBatchCompany=(id,companyGstin='')=>request(`/import-batches/${id}/company/`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({company_gstin:companyGstin})})
export const previewTallyVouchers=id=>request(`/import-batches/${id}/tally-vouchers/preview/`)
export const applySuggestedInvoiceValue=(id,partyGstin,invoiceNumber,invoiceDate)=>request(`/import-batches/${id}/tally-vouchers/correct/`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode:'suggested',party_gstin:partyGstin,invoice_number:invoiceNumber,invoice_date:invoiceDate})})
export const editInvoiceValueManually=(id,partyGstin,invoiceNumber,invoiceDate,roundOff)=>request(`/import-batches/${id}/tally-vouchers/correct/`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode:'manual',party_gstin:partyGstin,invoice_number:invoiceNumber,invoice_date:invoiceDate,round_off:roundOff})})
export const saveVoucherCorrection=(id,partyGstin,invoiceNumber,invoiceDate,field,value,correctionSource='manual')=>request(`/import-batches/${id}/tally-vouchers/correct/`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode:'save_correction',party_gstin:partyGstin,invoice_number:invoiceNumber,invoice_date:invoiceDate,field,value,correction_source:correctionSource})})
export const getTallyConnection=()=>request('/tally/connection/')
export const verifyTallyLicense=id=>request(`/import-batches/${id}/tally-license/verify/`,{method:'POST'})
export const verifyProductLicense=payload=>request('/license/verify/',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)})
export const heartbeatProductLicense=payload=>request('/license/heartbeat/',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)})
export const preImportLicenseCheck=(id,payload={})=>request(`/import-batches/${id}/license/pre-import-check/`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)})
// Step 6 job model: this call only starts/attaches to a background import job
// and returns almost immediately (status "running"/"already_running"/
// "interrupted") -- it never blocks for the actual Tally import, so it needs
// no client-side abort timeout. Progress/result comes from polling
// getTallyImportJobStatus below.
const jobStartRequests = new Map()
export function startTallyImportJob(id) {
  if (jobStartRequests.has(id)) return jobStartRequests.get(id)
  const pending = request(`/import-batches/${id}/tally-import/`, { method: 'POST' })
    .finally(() => jobStartRequests.delete(id))
  jobStartRequests.set(id, pending)
  return pending
}
export const getTallyImportJobStatus = jobId => request(`/tally-import/jobs/${jobId}/`)
export const getActiveTallyImportJob = id => request(`/import-batches/${id}/tally-import/active-job/`)
export const pauseTallyImportJob = jobId => request(`/tally-import/jobs/${jobId}/pause/`, { method: 'POST' })
export const resumeTallyImportJob = jobId => request(`/tally-import/jobs/${jobId}/resume/`, { method: 'POST' })
export async function lookupGSTIN(gstin){const response=await authenticatedFetch(`/api/gst/lookup/${encodeURIComponent(gstin)}/`);let body={};try{body=await response.json()}catch{}if(!response.ok){const error=new Error(body.message||'Unable to fetch GST details.');Object.assign(error,body);throw error}return body}
export async function getGSTLookupStatus(){const response=await authenticatedFetch('/api/gst/lookup/status/');let body={};try{body=await response.json()}catch{}if(!response.ok)throw new Error(body.message||'Unable to check GST lookup status.');return body}
async function sandboxRequest(path,{params={},...options}={}){const url=buildQueryUrl(`/api/gst/sandbox/${path}/`,params);const response=await authenticatedFetch(url,options);let body={};try{body=await response.json()}catch{}if(!response.ok){const error=new Error(body.message||'Sandbox GST request failed.');Object.assign(error,body);throw error}return body}
export const getSandboxStatus=batchId=>sandboxRequest('status',{params:{batch_id:batchId}})
export const authenticateSandbox=batchId=>sandboxRequest('authenticate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({batch_id:batchId})})
export const requestSandboxOTP=(batchId,username)=>sandboxRequest('request-otp',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({batch_id:batchId,username})})
export const verifySandboxOTP=(batchId,username,otp)=>sandboxRequest('verify-otp',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({batch_id:batchId,username,otp})})
export const getMyProfile=()=>request('/me/profile/')
export const updateMyProfile=payload=>request('/me/profile/',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)})
export const changeMyPassword=(currentPassword,newPassword)=>request('/me/change-password/',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({current_password:currentPassword,new_password:newPassword})})
export const getSupportContact=()=>request('/support-contact/')
