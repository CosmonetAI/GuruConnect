# Live customer conversation workflow

Customer Sessions are one continuing conversation. Audio upload chunks do not reset the AI's context.

Conversation bubbles and the timestamped transcript use the same speech data. The transcript starts collapsed to avoid showing the same text twice; its review toggle and download remain available.

The Sessions page captures five-second PCM/WAV chunks, commits each chunk to the browser outbox, uploads it to private recording storage, and confirms its checksum. The recorder retries transient transfers and exposes recovery for interrupted uploads. The open chunk remains in memory until its five-second boundary or Pause/End flushes it.

The durable recorder worker schedules ten-second progressive transcription windows (`LIVE_WINDOW_SECONDS`, configurable from 5–30 seconds). Each window includes the preceding two seconds of audio by default; sample ownership publishes overlapping words once. Source text can appear while English translation is pending. Upload, provider, translation, and network latency add to the window duration; this is incremental processing, not character-level streaming.

`GET /api/voice-sessions/{session_id}/live` returns the selected recording's active transcript version immediately. It includes source and translated text, explicit speaker assignments where available, recording status, product requirements, and actual Zoho inventory matches. The page polls every two seconds during capture/processing and reconnects after missed realtime events. It retains visible text when saving the same recording and rejects responses for previously selected customers.

The AI receives persistent structured customer context plus recent translated dialogue. Requirements retain identity, quantities, attributes, budget, exclusions, and source references across windows. Explicit corrections update an existing requirement; withdrawals retain exclusion state until the customer explicitly reinstates the item. Prompts omit catalogue images and audit metadata instead of cutting the customer's facts at an arbitrary JSON character boundary.

Live polling and the optional database webhook share a PostgreSQL conversation lock. Speech-content fingerprints survive copied progressive transcript IDs. AI consumes the contiguous audio frontier and stops at the first untranslated turn: a later correction cannot be applied before an earlier request merely because providers completed out of order. Native English/source parallel rows are handled without blocking their existing translation. Webhooks use the same ordering barrier as polling. Completed fingerprints and context persist in PostgreSQL. In-flight work rolls back on process failure and can be resumed by a later poll. Analysis tasks are bounded to two per API process; catalogue calls are bounded to three concurrent searches. Failures have a retry delay, and transcript/capture responses do not await AI or catalogue calls.

Product suggestions reuse the Product catalogue's member-scoped Zoho Books `/items` search. Every active requested category/SKU is searched, with specifications used for ranking. Prices, stock, currency and images come from returned inventory records. Missing prices cannot enter the cart. Tax is deferred to Books when it is unknown. Catalogue failure preserves the transcript and previous verified suggestions, with a visible notice.

Adding an item opens the animated cart layout without remounting the recorder. The cart scrolls vertically when items and totals exceed its available height and supports keyboard scrolling. Cart hydration never writes an empty cart over stored context. Cart writes are serialized; removing an item during an in-flight add queues the restored cart. Customer/workspace changes are blocked while recording or cart saving is active. Speaker roles are displayed only when explicitly assigned; an unassigned voice remains neutral.

End session flushes the shorter remainder, confirms all chunks, marks audio saved, and queues the final whole-recording pass. The final pass adds provider work and cost beyond progressive processing. It remains useful for final transcription and speaker refinement.

## Verification

Frontend unit tests cover recording and recovery, continuous live rendering, late customer responses, same-recording text retention, cart hydration/save ordering, speaker identity and cart transitions. Backend tests cover scheduling and overlap, ongoing requirements/corrections/withdrawals, fingerprint deduplication, active-version/customer scope, and real inventory normalization.

```sh
cd frontend
npm test -- --pool=threads --maxWorkers=1
npm run build
```

```sh
PYTHONPATH=backend PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 backend/.venv/bin/python -c 'import pytest, pytest_asyncio.plugin; raise SystemExit(pytest.main(["backend/tests/test_live_voice.py", "backend/tests/test_live_voice_chronology.py", "backend/tests/test_local_voice_analysis.py", "backend/tests/test_demo_catalogue.py", "frontend/tests/unit/test_local_progressive.py", "-q"], plugins=[pytest_asyncio.plugin]))'
```

The standalone browser check uses a real fake-device microphone with synthetic API responses, never live accounts. Start an isolated preview with empty Supabase settings, then run the check in another terminal:

```sh
cd frontend
VITE_SUPABASE_URL='' VITE_SUPABASE_PUBLISHABLE_KEY='' VITE_SUPABASE_ANON_KEY='' VITE_API_URL=http://127.0.0.1:8010 npm run dev:web -- --port 5183
```

```sh
cd frontend
npm run test:live-workspace
```

Screenshots are saved under `frontend/test-results/live-workspace`. The check verifies uploads during capture, translated text before End session, multi-product suggestions, cart/discount/note interactions, desktop/mobile overflow, and final save.

An isolated real-provider smoke on 29 October 2026 used ten seconds of synthetic Hindi speech and the configured backend providers. While capture remained `recording`, it produced source text about three seconds after upload, English text about five seconds after upload, and requirements for two ceiling fans plus one motor with demo inventory matches about nine seconds after upload. The model used `customer_requirements` instead of the prompt's `requirements` field; the shared merger now accepts that observed output shape, with a regression covering both product categories. One earlier cold test exceeded ninety seconds without a result, so these timings are observations rather than a delivery guarantee. Real provider behavior, connection warmup and sustained queue throughput still need representative load measurements.

The local browser check uses a separate SQLite directory and API/frontend ports. It verifies ten seconds of durable uploads and a live job before Stop, truthful missing-key states, three catalogue items, quantity/discount edits, cart scrolling above the recording footer at desktop widths, mobile overflow, and cart/note persistence after reload. The contacts-open cart also has a geometry regression preventing internal notes from overlapping cart items or totals.

## Deployment and operating limits

The independent **Local workspace** supports the same ten-second progressive speech cadence using durable SQLite window jobs, validated five-second audio chunks, and overlap ownership. It publishes cumulative source/English text while capture continues and keeps the final whole-recording pass authoritative. Late window translations cannot overwrite a final version. Real configured speech/translation/AI providers are used; no transcript or AI extraction is fabricated. Readiness is reconciled against runtime credentials while retaining genuine provider failures.

For the requested local check, inventory alone uses explicitly labelled sample fans, motors, air conditioners and lights. The local Product catalogue search and live suggestions share the same demo adapter. Sample prices/stock/images never masquerade as a connected Zoho organization. Demo items cannot create Zoho documents. Hosted deployments continue using member-scoped Zoho inventory and have no demo fallback. A real-browser regression using an isolated local API is available as `npm run test:browser -- local-session.spec.ts`.

`npm run dev` reloads backend application changes with a polling watcher, avoiding native file-provider hydration events. GuruConnect uses built-in Pydantic validation without validation plugins and skips unused plugin autodiscovery by default; `PYDANTIC_DISABLE_PLUGINS` can explicitly override that default. This avoids blocking local startup on cloud-managed package metadata.

Both frontend and API must run the same updated revision. The Render blueprint currently tracks `main`; changing a local `dev` checkout alone does not update production. Existing voice context/extraction and recording migrations must already be applied. Managed speech/translation/AI services and the selected member's Zoho Books organization must be connected.

The hosted publisher currently copies prior completed turns into each immutable progressive version. Long sessions therefore increase stored transcript rows and per-turn webhook traffic faster than audio duration. The configured two-hour maximum is not a demonstrated capacity guarantee; production load testing must include that publication/storage behavior. Local progressive publication keeps one cumulative current view plus its independent windows.

Local fixtures and passing tests do not verify live provider latency, accuracy, quotas, credits, or production migrations. Benchmark those with representative consented multilingual conversations and the intended concurrent-session load before an enterprise rollout. AI requirements remain suggestions for the salesperson to review; cart changes and document creation remain explicit user actions.
