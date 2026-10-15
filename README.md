# GuruConnect

An independent enquiry workspace with private audio recording/upload, ElevenLabs source transcription and speaker detection, and OpenAI English translation. Everything needed to run the frontend, API and processing worker lives in this repository. Office Recorder does not need to be running.

## Architecture and implementation status

The attached Guru Group AI Sales architecture describes the target business flow:

`call / walk-in → capture → secure audio → speech-to-text → AI extraction → validation → Zoho transaction → quote and follow-up → outcome analytics`

The current repository implements the core capture and processing path in the GuruConnect workspace: walk-in/enquiry registration, browser audio capture and upload, private Supabase Storage, queued processing, speech/transcript handling, speaker detection, English translation, immutable transcript versions, manual review, and workspace-scoped access controls.

The copied GG Project implements the Zoho sales-assistant path: server-side Zoho OAuth/token refresh, Bigin contact search/create/update, Books products/customers/vendors, quotes, sales orders, purchase orders, invoices, email actions, customer chat, structured enquiry context, and Supabase persistence. Its UI is available from the GuruConnect sidebar as **Zoho integration**.

The architecture image is a proposed end-to-end design, so telephony routing/recording, WhatsApp/email provider delivery, full Inventory API automation, branch dashboards, outcome analytics, and continuous-learning feedback still require provider-specific implementation and validation. The code does not claim those capabilities are live merely because they appear in the diagram.

## Start locally — no Supabase and no login

Requires Node 22.12+ and Python 3.11 or 3.12. From the `frontend` folder:

```sh
cd frontend
npm ci
npm run setup
npm run dev
```

Open **http://127.0.0.1:5176**. The development command starts all three services: frontend on 5176, API on 8010, and the separate background worker. Ctrl+C stops those processes together. No environment file, Supabase project or login is required for local mode. If Python cannot be found, install a supported version or set `GURUCONNECT_PYTHON` to its executable before running setup.

Local enquiries, immutable audio chunks, playback, transcripts and encrypted provider connections persist in `backend/.local-data/`. This directory is ignored by Git and restricted to the operating-system account. Back up the entire directory, including its encryption key, if you need to preserve local data. Local mode is for this computer, with one local user/workspace; it is not team authentication and must not be published as a SaaS.

Use separate terminals from `frontend` if preferred:

```sh
npm run dev:web
npm run dev:api
npm run dev:worker
```

If a frontend started earlier on `localhost:5174` shows “Failed to fetch,” the API may not be running. Start the full stack, then use the dedicated URL above. Local mode accepts the existing 5174 origin as well. Do not run two copies on the same ports.

## Recording and translation flow

1. Open **Connections**. Save an ElevenLabs key with Speech to Text access and User Read for the metadata test. Save an OpenAI project API key with model lookup and Responses permissions. Provider keys are entered here, never in frontend environment variables.
2. **Test connection** checks account/model access. The separately confirmed **Functional smoke test** runs real provider work and can incur charges. OpenAI uses the selected `gpt-5.6-luna` model; API billing is separate from ChatGPT subscriptions. This integration does not make provider usage free.
3. Open **New enquiry**, enter the customer name and confirm participant consent. Record with the microphone, or choose and upload a supported audio file. File decoding and size/duration limits are checked before creating processing work.
4. Stop recording. Wait until the private audio has been uploaded and finalized. The form protects unfinished capture/uploads from navigation and keeps a scoped recovery outbox when transfers fail.
5. Save the enquiry. Its detail page shows the actual audio/transcription/translation/speaker states, source playback and source-linked English turns. You can review original text, correct a turn and confirm participant names.

You can save and play local audio without provider credentials. The worker blocks speech/translation honestly when a required key is missing; it does not invent a transcript. After configuring a provider, explicitly choose **Retry failed stages** or **Retry English translation**. Translation retry uses retained source text without resubmitting speech. Provider requests whose paid outcome is uncertain are held for reconciliation rather than automatically repeated.

**Recordings** also lists saved conversations before they are linked to an enquiry. Draft customer fields and the audio link are scoped to the current account/workspace in this browser tab. Local recovery audio uses IndexedDB. Clearing browser storage removes unsent recovery data; server-confirmed local audio remains in the backend data directory.

## Configure the new Supabase project

Local mode remains available until credentials are supplied. The hosted path requires your **new GuruConnect project**; no credentials, recordings or provider settings were copied from Office Recorder.

1. Create the project. Enable email/password Auth, email confirmation and asymmetric JWT signing. Add exact Site URL/callback/reset URLs for the frontend: development `http://127.0.0.1:5176`, `/auth/callback` and `/reset` (also allow localhost if used). Configure production SMTP and password policy before inviting a team.
2. Copy `frontend/.env.example` to `frontend/.env`, then fill the project's public URL/publishable key and the single combined backend URL. Only public values belong in `VITE_` variables. Both GuruConnect and Zoho use `VITE_API_URL`.
3. Copy `backend/.env.example` to `backend/.env`. This is the single backend environment for the recorder, worker, GG chat, and Zoho services. Fill the project URL, service-role key, Postgres session-pooler connection URL, and server-only provider settings. Percent-encode password characters such as `@`, `#`, `%`, `/` and `?`. Use `sslmode=verify-full` with the bundled public `supabase-ca.crt`.
4. Generate `CREDENTIAL_ENCRYPTION_KEY` with the command below and store it only in the backend environment. API and worker must use the same key. Keep CORS restricted to your exact frontend origins.

   ```sh
   backend/.venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
   ```

5. Apply every file in `supabase/migrations/` in lexical order. The migration script requires explicit target credentials and confirmation:

   ```sh
   export MIGRATION_DATABASE_URL='postgresql://...new-project.../postgres?sslmode=verify-full&sslrootcert=/absolute/path/to/supabase-ca.crt'
   export GURU_MIGRATION_CONFIRM=development
   backend/.venv/bin/python frontend/scripts/migrate.py
   ```

   The checksum ledger rejects changed applied migrations. Never apply `frontend/tests/database/native_fixture.sql` to Supabase; that is disposable test emulation.
6. Restart all services. With valid public credentials the frontend requires login; the hosted API verifies JWTs and workspace ownership. Register/verify an account, create a workspace, and configure ElevenLabs/OpenAI again in **Connections**. Invite teammates with their name, email, and role; they receive a password-setup link and are added to the workspace automatically.

Hosted mode uses private Supabase Storage, database RLS, recording leases, durable job queues, immutable processing results and audited manual corrections. Local data is not automatically imported into Supabase when switching modes. Production missing credentials fail closed; there is no production no-login fallback. An explicitly local build can set `VITE_LOCAL_MODE=true` for loopback-only use, but do not enable this in a hosted frontend.

## Checks

From `frontend`:

```sh
npm run check
npm test
npm run test:backend
npm run test:browser
npm run build
npm audit
```

Backend tests require development Python requirements installed by setup; some isolation/concurrency checks require native PostgreSQL binaries. Browser tests use installed Chrome, a controlled microphone fixture, disposable local storage and mocked/absent provider credentials. They do not establish live provider accuracy or hosted Supabase deployment readiness. See `docs/VERIFICATION.md` for the actual completed checks and limits.

For deployment, run the API and worker as separate supervised services, set `ENVIRONMENT=production`, apply all migrations, use HTTPS and explicit frontend CORS, configure backups/retention and monitor queue/errors/provider usage. Vite's dev server and localhost mode are not deployment services. Publish the frontend build from `frontend/dist/` with SPA navigation fallback.

### Render backend settings

Use `backend` as the service root directory. Keep one dependency file and use:

```sh
pip install -r requirements.txt
python -m recorder.launch
```

The launch module validates configuration, reads Render’s `PORT`, and serves both GuruConnect `/api/v1/...` and GG/Zoho `/api/...` routes from the same process.

