# GSTR2Tally registration validation — 16 November 2026

## Verified results

- Isolated SQLite Nexus database only; existing GSTR/Nexus/PRN customer databases were not connected by the end-to-end test.
- Simulated test serial: **925793256**. Simulation exists only in the development harness; the customer API still requires connector verification.
- GSTR registration API -> real local Nexus HTTP API: **201**, exactly **one** customer.
- User ID = Product Serial Number = Test Tally Serial Number: **925793256**.
- Saved customer email: **sai***@gmail.com**. Actual welcome-message recipient: **sai***@gmail.com**. SMTP RCPT TO returned **250**; Nexus EmailLog recorded **accepted**, **one attempt**.
- User confirmed receipt in the authorized inbox and that recipient/contents match; temporary password present, no Activation Key.
- Duplicate registration: **409 / account_exists**, one customer remains and email ownership is unchanged.
- GSTR login using the generated temporary password: **403 / password_change_required**, expected first-login setup. No unrestricted application session is issued before password setup and licensing.
- Real running Tally -> connector -> detection API: **200**, serial **776776311**. Registration in that test is mocked; no existing customer was altered.
- Selected backend suite: **25 passed**. Connector suite: **7 passed**. Frontend production build: passed.

## Root causes and fixes

- Silent installer previously used `skipifsilent`, preventing immediate connector launch. It now runs after silent/interactive installation with `runasoriginaluser`.
- Company-response XML parsing failure previously prevented a separate Tally license read. Serial discovery now executes independently.
- Nexus field-validation payloads and `detail` errors were discarded by the GSTR API client. Original reasons and field errors are now preserved.
- Browser connector pairing failures were swallowed; pairing and detection now expose the failure, and activation/login retain specific backend reasons.
- Successful registration retains the returned User ID for login.
- Nexus welcome-email code addresses the saved customer email. Separate administrator registration notices explain additional recipient classes; no evidence that the tested welcome email was misaddressed. Sender identity is not used as recipient evidence.

## Limits

- No genuinely unregistered licensed Tally installation was available. Real new-customer browser registration still requires that installation and staging browser checks.
- This run verified temporary-password acceptance and the mandatory setup response, not completion of a licensed application login.
- Connector startup installer configuration was corrected; Windows sign-in and installed-runtime launch were not exercised. A development package build does not prove installation or autorun works.
- No production migration, customer import, ownership/password change, license transfer, deployment, or PRN modification was performed.
- The disposable development database is retained locally for review. Do not import or deploy it.
