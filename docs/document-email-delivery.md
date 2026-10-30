# PDF document email delivery

GuruConnect creates the selected invoice, quotation/estimate, or sales order in Zoho Books. SMTP2GO is the separate mail delivery service. Bigin remains a CRM contact source, not the accounting document system.

The backend adapter is `app.document_email.send_document_email`. It accepts the recipient, subject, plain-text message, PDF bytes, PDF filename, and the workflow's durable delivery key. PDFs are attached as base64 `fileblob` with `application/pdf`, using the documented [SMTP2GO attachment API](https://developers.smtp2go.com/docs/adding-attachments). Credentials stay in the server-side `X-Smtp2go-Api-Key` header.

## Server configuration

Add these values to the backend environment when credentials are available:

```dotenv
SMTP2GO_API_KEY=
SMTP2GO_SENDER_EMAIL=
SMTP2GO_SENDER_NAME=GuruConnect
SMTP2GO_TIMEOUT_SECONDS=20
```

The sender address must belong to a sender authorized in the SMTP2GO account. Unverified senders are rejected according to the [SMTP2GO setup guide](https://developers.smtp2go.com/docs/getting-started). `email_delivery_configuration()` reports readiness without exposing the key or making a network request. Configured credentials do not mean a real delivery has been verified.

Missing configuration never reports an email as sent. The workflow can preserve the already-created document and offer its PDF download. When the customer has no email address, the workflow skips mail submission and returns the PDF download directly.

## Safe retries and status

The implemented workflow uses `POST /api/voice-sessions/{session_id}/sales-documents`, with a UUID `request_id` and matching `Idempotency-Key`. The server validates every inventory ID and current rate, then commits a creation claim before contacting Books. A repeated key reuses the saved result. Download-only requests never submit mail. Local mode validates sample catalogue items and generates a PDF visibly labelled SAMPLE; it does not call Zoho or SMTP2GO.

Apply `supabase/migrations/202610290001_voice_sales_documents.sql` before deploying the hosted API. Its document records are scoped by workspace, member and session; anonymous access and direct client mutations are disabled. `/ready` checks that this table exists. No-email customers can start Sessions and create/download documents without supplying an email address.

An email rejection leaves the created document available. `POST /api/voice-sessions/{session_id}/sales-documents/{document_id}/email` is the explicit retry action; it uses a new delivery key against that existing document. `GET` on the same document path retrieves its PDF without sending email. The modal keeps the original request after a lost creation response, so checking the result does not create another invoice. Ambiguous Books creation is retained for review using its `GC-...` reference.

The document workflow persists its scoped delivery claim **before** calling the mail adapter, including `delivery_fingerprint(...)`, status `sending`, and a stable delivery action ID under a database lock. Document creation and email submission have distinct status, so a failed email does not create another invoice or order.

After the call, the workflow persists returned SMTP2GO email/request IDs and the `accepted` receipt. It returns existing receipts and rejects repeat submissions with `sending` or `uncertain` outcomes. The adapter also exports `delivery_replay(existing, fingerprint)` for other callers using the `submitting` state convention; a missing accepted receipt is uncertain rather than permission to send again.

The adapter performs one HTTP submission and follows no redirects. A connection failure before submission or a definitive provider rejection can be retried through an explicit workflow action. A read/write timeout, malformed successful response, partial acceptance, or server-side error has an ambiguous result and is marked `uncertain`; it is not automatically retried. The SMTP2GO endpoint does not list an idempotency parameter, so this adapter treats repeated submissions as potentially separate emails. This is a conservative inference from the documented [send endpoint](https://developers.smtp2go.com/reference/send-standard-email).

SMTP2GO can return HTTP 200 with recipient failures, so the adapter checks `succeeded`, `failed`, and `failures` as required by the same endpoint documentation. A successful API response is **accepted for delivery**, not a confirmed delivery to the recipient's inbox. Final delivery/bounce status requires the provider's activity records or a configured [SMTP2GO webhook](https://developers.smtp2go.com/docs/webhooks-overview).

`EmailDeliveryError.as_dict()` provides sanitized codes, retry/uncertainty flags, and safe provider IDs. Raw provider messages, credentials, document bytes, and customer email text are not added to errors or logs. The hashed `X-GuruConnect-Delivery` custom header correlates provider activity without exposing the internal delivery key; it is a trace header, not provider-side duplicate suppression.

## Verification

`backend/tests/test_document_email.py` uses `httpx.MockTransport` with synthetic credentials. It covers PDF attachment encoding, missing configuration, input validation, HTTP 200 failures, provider rejections, ambiguous outcomes, one-attempt behavior, redirect protection, and durable receipt replay. It sends no real email.

```sh
PYTHONPATH=backend PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 backend/.venv/bin/python -c "import pytest,pytest_asyncio.plugin;raise SystemExit(pytest.main(['backend/tests/test_document_email.py','-q'],plugins=[pytest_asyncio.plugin]))"
```

A live credentials/sender verification and a consented test delivery remain necessary once the SMTP2GO credentials are supplied.
