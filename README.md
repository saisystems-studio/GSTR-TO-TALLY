# GST to Tally Import System

React/Vite and Django REST Framework application for importing GSTR-1 JSON, GSTR-2A CSV, and GSTR-2B XLSX data into Microsoft SQL Server. The workflow covers upload, invoice preview, GST party verification, Tally master preparation, voucher validation, and the final Tally connection stage.

## Backend

```powershell
cd D:\PD\GST\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

Configure SQL Server through `backend/.env`; `backend/.env.example` documents supported variables. Microsoft ODBC Driver 17 for SQL Server is required. There is no SQLite fallback.

Create a license with:

```powershell
python manage.py create_license `
  --serial GST-000001 `
  --email admin@gmail.com `
  --key GST-1111-ABCD `
  --status ACTIVE `
  --max-devices 1 `
  --expiry 2027-08-20
```

## Frontend

```powershell
cd D:\PD\GST\frontend
npm install
npm run dev
```

Vite proxies `/api` to `http://127.0.0.1:8000`. Use `VITE_API_BASE_URL` to override it.

## Active GST API

- `POST /api/gst-tally/preview/`
- `POST /api/gst-tally/import/`
- `GET /api/gst-tally/batches/`
- `GET /api/gst-tally/batches/{id}/`
- `GET|POST /api/gst-tally/import-batches/{id}/parties/`
## TallyPrime integration

Apply migrations, then copy the Tally variables from `backend/.env.example` to `backend/.env`. Keep `TALLY_DRY_RUN=true` while reviewing Step 4 and Step 5. Step 4 only prepares the required master preview; Step 6 checks the connection, open company, existing masters, and duplicate voucher mapping before making writes.

In TallyPrime, open the intended company, enable ODBC, and keep the HTTP/XML server enabled for writes. Configure `TALLY_ODBC_ENABLED=true`, `TALLY_ODBC_DSN=TallyODBC64_9000`, and `TALLY_ODBC_TIMEOUT=10` (or `TALLY_ODBC_CONNECTION_STRING` when the installed driver requires it). The open company GSTIN must exactly match the uploaded company GSTIN; company names are display-only. Once one invoice has passed preview review, set `TALLY_DRY_RUN=false`, restart Django, and import that one-invoice batch first. The application never creates or alters a company.

An invoice is marked Imported only when the parsed Tally response reports a created/altered object with no errors. Raw responses are retained in `tally_voucher_mapping_tbl` for diagnostics. Parties lacking a safe name are skipped without blocking eligible invoices.
