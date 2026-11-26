# ConnectCosmos

ConnectCosmos is a sales workspace for capturing customer enquiries, recording conversations, understanding what the customer needs, and moving the opportunity through follow-up and Zoho Books/Bigin.

It is designed for reception teams, salespeople, sales managers, and administrators who need one place to manage walk-ins, conversations, products, documents, customers, and leads.

## What the app does

- Registers walk-in visitors and assigns them to salespeople.
- Captures customer conversations through microphone recording or audio upload.
- Stores audio securely and prepares transcription, speaker information, and English translation.
- Keeps the original recording and transcript attached to the enquiry for review.
- Detects useful sales context such as products, quantities, quotations, orders, invoices, and follow-up needs.
- Shows missed visitors and enquiries that need follow-up.
- Connects to Zoho Books for customers, products, quotes, sales orders, invoices, payments, and document downloads.
- Connects to Zoho Bigin for contacts, pipeline leads, stages, and sales ownership.
- Prevents duplicate CRM records by checking phone numbers and email addresses before creating contacts.
- Supports a sales pipeline beginning with **New enquiry**, where leads can be progressed through later stages.

## Main workflows

### 1. Walk-in visitor

Reception registers the visitor with name, phone, email, purpose, company, and assigned salesperson. ConnectCosmos creates or links the customer in Zoho Books and Bigin when the integrations are available.

The salesperson can start a conversation, capture requirements, or leave the visitor in the missed-visitor follow-up list if no session is started.

### 2. Customer conversation

The salesperson confirms consent, starts recording, and speaks with the customer. After recording stops, the audio is saved before processing begins. The enquiry can be reviewed with its original audio, transcript, speakers, translation, customer details, and product requirements.

### 3. Sales follow-up

When the conversation does not produce a clear product or document intent, the customer is still retained as a follow-up opportunity. These enquiries appear in the sales inbox and can be moved through the pipeline.

### 4. Zoho documents

Salespeople can search existing Zoho Books documents and create quotes, sales orders, or invoices from the customer cart. Creating an updated document updates the existing Zoho document where supported. Invoice cards provide payment actions, including full or partial payment, and quote cards provide acceptance actions.

### 5. Product catalogue

The Products page reads live Zoho Books items. Salespeople can add one item or upload multiple items using the downloadable CSV template. Bulk uploads are validated row by row and report successful and failed items.

### 6. CRM lead management

A new enquiry starts in the ConnectCosmos **New enquiry** column. When it is moved into a Bigin pipeline stage, the backend links the existing Bigin contact or creates one, then creates the corresponding Bigin lead with the original customer details.

## Who uses it

- **Reception:** registers visitors and assigns them to salespeople.
- **Salesperson:** records conversations, reviews transcripts, manages carts, creates documents, and follows up.
- **Sales manager:** monitors enquiries, pipeline movement, missed visitors, and customer activity.
- **Administrator/owner:** manages workspaces, team access, Zoho connections, and deployment settings.

## Local demo

Local mode runs without Supabase login and is useful for demonstrations and development. It stores local data under `backend/.local-data/`.

Requirements: Node 22.12+ and Python 3.11 or 3.12.

```sh
cd frontend
npm ci
npm run setup
npm run dev
```

Open <http://127.0.0.1:5176>. The frontend uses API port 8010. Separate commands are available with `npm run dev:web` and `npm run dev:api`.

Local mode is for one computer and one local workspace. It is not a production multi-user deployment.

## Hosted setup

The hosted application uses:

- Supabase Auth for user login and workspace membership.
- Supabase PostgreSQL for enquiries, customers, recordings, processing state, and audit data.
- Private Supabase Storage for audio files.
- A backend API for recording, processing, CRM, Zoho, and document workflows.
- A React frontend for the sales workspace.

For a hosted environment, configure the Supabase project, backend environment, provider keys, encryption key, and exact frontend/API origins. Apply the files in `supabase/migrations/` in order before inviting users.

Zoho credentials are stored and refreshed by the backend. They must never be placed in frontend `VITE_` variables or committed to the repository. The Zoho Books item create/update/delete APIs use the `ZohoBooks.settings.CREATE`, `ZohoBooks.settings.UPDATE`, and `ZohoBooks.settings.DELETE` scopes; reconnect after adding these scopes to an existing OAuth grant.

## Verification

From `frontend`:

```sh
npm run check
npm test
npm run test:backend
npm run build
```

The repository also contains browser and deployment verification notes in `docs/VERIFICATION.md`.

## Deployment

The root `render.yaml` describes the frontend and backend services for Render. Production deployment requires HTTPS, the required environment variables, database migrations, private storage, restricted CORS, and backups/monitoring.

The backend can also be started directly from the `backend` directory:

```sh
pip install -r requirements.txt
python -m recorder.launch
```

ConnectCosmos is a workflow application: it keeps the customer conversation, sales context, CRM record, product selection, document creation, and follow-up connected from the first visitor interaction to the next sales action.
