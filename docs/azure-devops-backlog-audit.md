# GuruConnect Azure DevOps backlog audit

Date: 2026-10-15

## Scope

This audit compares the repository implementation with the current Azure DevOps export, the Sales Automation Designs document, and the architecture diagram. The revised import file intentionally contains only new detailed work items; it does not delete or duplicate the existing Azure DevOps items.

Scope correction: Zoho Bigin is limited to CRM pipeline layouts/records and contacts. Accounts, products, tasks, events, calls, notes, quotes, orders, invoices, catalogue, and commercial operations belong in Zoho Books or the application layer and are not Bigin backlog items.

## Current work-item status

| Existing item | Audit result | Evidence / remaining work |
|---|---|---|
| MVP Readiness, Feasibility, and Data Mapping | Partial | Supabase schema, RLS, OAuth/token refresh, workspace/member foundations, and provider settings exist. Tenant-specific Zoho field/layout/ownership validation, live smoke tests, and final data mapping remain. |
| Telecalling Route Integration and Call Capture | Missing | No provider-specific routing/recording adapter for the 22 numbers, inbound webhooks, number-to-salesperson/branch mapping, or call consent flow. |
| Session-Based Showroom Enquiry Capture | Mostly implemented | Walk-in registration, customer details, temporary ticket tracking, recordings, assignment, enquiry status, and history routes/pages exist. Real-world UAT and edge-case hardening remain. |
| Multilingual Transcription and Enquiry Extraction | Core implemented / integration partial | Chunked private audio, queued processing, ElevenLabs transcription, speaker data, transcript versions, OpenAI English translation, revisions, and retries exist. Provider credentials, language-quality validation, structured extraction accuracy, and production smoke tests remain. |
| Product Matching and Quote Rule Validation | Partial | Books item/catalogue operations and enquiry context exist. Deterministic SKU matching, pricing/discount/tax/payment validation, ambiguity review, and stock-after-quote rules need completion. |
| Zoho Bigin CRM pipeline and contacts | Mostly implemented / bounded scope | OAuth, contacts, pipeline layouts/records/search/single-record, cache, and member-scoped credentials exist. Complete pipeline write-back, contact deduplication, field mapping, and production smoke tests remain. All commercial operations are tracked under Books instead. |
| MVP UAT, Reporting, Training, and Handover | Not completed | Automated tests are substantial, but provider-backed UAT, pilot evidence, production runbooks, dashboards, training, and handover acceptance are not complete. |

## Implemented repository areas

- Authentication, workspace membership, role-aware access, profiles, invitations, presence, and RLS foundations.
- Walk-in visitor registration, daily ticket tracking, enquiry persistence, assignment/status flows, unread counts, and customer/session history.
- Browser audio capture, private Supabase Storage, chunk upload/finalization/recovery, recording leases, background processing, transcript versions, speaker grouping, revisions, playback/export, and translation jobs.
- Server-side Zoho OAuth/token refresh and member-scoped credential storage foundations.
- Bigin pipeline/layout/search/single-record and contact operations.
- Books organizations, items, customers/vendors, estimates, sales orders, purchase orders, invoices, and email action routes.
- Frontend pages and backend tests covering the main capture, enquiry, recording, transcript, auth, and Zoho paths.

## Missing or incomplete against the design

- Telephony provider integration for 22 numbers, routing, call metadata, recording references, webhook retries, and consent.
- Provider-backed WhatsApp/email delivery, delivery status, consent, templates, and reply tracking.
- Complete Bigin write-back lifecycle: contact/account/product ownership, pipeline create/update/delete, event/call actions, note/task lifecycle, and reconciliation.
- Zoho Inventory stock, package, shipment, and availability validation after quote creation.
- Product/SKU matching and commercial-rule engine with human approval for low confidence or exceptions.
- Quote approval/send gate, explicit acceptance evidence, and sales-order creation guardrail.
- Follow-up outcome model: won/lost/pending, loss reason including stock, payment follow-up, and continuous-learning feedback.
- Branch/executive dashboards, AI quality metrics, operational alerts, and scale validation for all 22 numbers.
- Live provider smoke tests, pilot/UAT evidence, handover documentation, retention/deletion verification, and security review.

## Verified implementation gates

- Frontend TypeScript check: passed (`npm run check`).
- Python compilation: passed (`python -m compileall -q recorder app`).
- Frontend tests: 120 passed, 2 failed. The failures are `OfficeContext` delayed workspace-list behavior and the local audio upload destination contract; both remain backlog items.
- Backend test runner: not runnable in the current checkout because the expected backend development virtual environment is not present.

Priority order in the revised import is: P0 foundation UI/access/enquiries/sessions/pipeline and contacts; P1 transcription and translation; P2 Books catalogue and transactions; P3 telecalling and provider/service setup; P4 analytics/UAT/handover. Use `azure-devops-prioritized-backlog-import.csv`; the earlier `azure-devops-detailed-backlog-import.csv` is superseded by it.

## Import approach

The companion CSV is a new-items import. Azure DevOps requires only `Work Item Type` and title columns for new items and defaults imported items to `New`. It uses indented `Title 1`, `Title 2`, and `Title 3` columns for hierarchy. Existing feature names are included as the first-level parents so the detailed items can be reviewed and then linked under the corresponding existing feature if the importer cannot resolve an existing parent automatically.

The CSV deliberately omits `ID`, `State`, `Assigned To`, `Changed Date`, `Created By`, `Area Path`, and `Tags` so it follows Azure DevOps new-item import rules. Status is captured in each description as `Done`, `Partial`, or `Missing`; change states after review rather than silently overwriting the current board.

