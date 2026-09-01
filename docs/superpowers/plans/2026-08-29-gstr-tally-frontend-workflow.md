# GSTR Tally Frontend Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign only the frontend workflow for the authenticated GSTR to Tally import application.

**Architecture:** Keep the existing React/Vite app and backend API surface. Move workflow gating and validation into small pure helper modules with node:test coverage, then wire those rules into the React pages and restyle the workflow with a restrained accounting UI.

**Tech Stack:** React, Vite, CSS, node:test.

**Spec:** `C:\Users\Sai_Dev_1\.codex\attachments\92c5c694-685e-4fd8-9285-c24d0a44b7c7\pasted-text.txt`

## Global Constraints

- Modify only the frontend workflow unless a compile fix requires a frontend utility adjustment.
- Do not fake Tally/company/license/import success; display and gate from backend-provided state only.
- Preserve uploaded file, preview, party, company, master, voucher, mismatch, and import state while navigating backward.
- Authenticated Home returns to the GSTR Import screen, not logout.
- No new frontend dependencies unless explicitly approved.

---

### Task 1: Workflow Rules

**Files:**
- Create: `frontend/src/utils/workflowState.js`
- Test: `frontend/src/utils/workflowState.test.js`

**Interfaces:**
- Produces: `detectFileFormat(fileName)`, `canStartImport({ returnType, file, fileFormat })`, `licenseStatusFromPayload(payload)`, `nextWorkflowStep(state)`.

- [ ] Write failing node:test coverage for file-format gating, backend-only license verification, and sequential next-step calculation.
- [ ] Run the tests and confirm they fail because `workflowState.js` does not exist.
- [ ] Implement the pure helper functions.
- [ ] Run the tests and confirm they pass.

### Task 2: Auth Form Rules

**Files:**
- Create: `frontend/src/utils/authForms.js`
- Test: `frontend/src/utils/authForms.test.js`
- Modify: `frontend/src/pages/Login.jsx`

**Interfaces:**
- Produces: `validateRegistration(values)`, `validateLogin(values)`, `normalizeContactNumber(value)`.

- [ ] Write failing node:test coverage for full name, email, 10 digit contact number, username, password, confirm password, remember me, and password visibility compatible state.
- [ ] Run the tests and confirm they fail because `authForms.js` does not exist.
- [ ] Implement auth helpers and wire them into `Login.jsx`.
- [ ] Run the tests and confirm they pass.

### Task 3: React Workflow Shell

**Files:**
- Modify: `frontend/src/App.jsx`
- Modify: `frontend/src/pages/GstTallyImport.jsx`
- Modify/Create components in `frontend/src/components/gst-tally/`

**Interfaces:**
- Consumes: helpers from Tasks 1 and 2 plus existing API functions in `gstTallyApi.js`.
- Produces: authenticated `AppShell`, `TopHeader`, `BackButton`, `StepProgress`, upload, preview, party, company, license, confirmation, masters, vouchers, import, and result screens.

- [ ] Replace Home/logout wiring so Home resets to import while profile/logout remains separate.
- [ ] Replace fake upload timers with real API phase labels.
- [ ] Split company, license, confirmation, masters, voucher, mismatch, import, and result screens while preserving state.
- [ ] Ensure blocking rules follow backend state only.

### Task 4: Professional Styling

**Files:**
- Modify: `frontend/src/styles/gst-tally.css`

- [ ] Replace old mixed styles with a cohesive light accounting UI.
- [ ] Keep tables dense, sticky, horizontally scrollable, and responsive.
- [ ] Style badges, progress, drawers, modals, toasts, loading buttons, and validation errors.

### Task 5: Verification

**Files:**
- All changed frontend files.

- [ ] Run all frontend node tests with `node --test`.
- [ ] Run `npm run build`.
- [ ] Fix any failures, rerun, and report verification output.
