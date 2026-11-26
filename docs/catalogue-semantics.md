# Catalogue retrieval and agent guidance

This implementation combines the member's changing Zoho Books inventory with semantic candidate retrieval and deterministic checks. Inventory supplies product facts. AI interprets the conversation and suggests what to clarify; it does not supply missing catalogue specifications, prices or stock.

The application uses the existing backend, PostgreSQL/Supabase and a managed embedding API. It does not require another self-hosted service, a model download or an additional inference server. Verification includes mocked regressions, a read-only CSV replay and a separate actual embedding-API smoke check. No production deployment, Zoho write or customer email was performed during these checks.

## Conversation and catalogue are separate workloads

Recording, transcription and English translation continue through the existing progressive speech pipeline. Analysis advances through the contiguous translated audio frontier, preserving the order of corrections and withdrawals. Durable requirement IDs retain quantities, budgets, specifications, exclusions and source evidence across speech windows. Re-published database turn IDs do not make unchanged speech a new requirement.

Semantic intent routing uses recent dialogue and durable customer context. A price enquiry, comparison, stock check, purchase request or document request can produce different internal guidance. The route and its confidence are advisory. They do not approve a product, create a financial document or interrupt the customer with an automatic question. Missing analysis credentials or provider failure produce an unavailable/error state; untranslated or failed work is not presented as completed analysis.

The structured model selects evidence IDs from a closed schema enum. The backend expands each ID to its original transcript/context text and validates facts against that source; the model does not manufacture evidence sentences. There is at most one repair request for invalid structured output. Unchanged saved facts repeated by the model are omitted from the delta; a different value cited only from old dialogue cannot overwrite a newer correction. Conventional written numbers and technical units normalize without fuzzy evidence matching. The evidence-reference budget is 256 and oversized batches report a visible limit rather than silently losing facts.

Existing requirements also have closed target references in each request. A model can resolve a shortened device reference against saved context and update that record, instead of adding a renamed duplicate. Targets outside the supplied context are rejected. This covers semantic references such as a later withdrawal using a different device name without embedding inventory-specific synonym tables in application code.

Catalogue synchronization runs in background tasks rather than fetching the entire inventory for each utterance. The database scope includes the GuruConnect organisation, salesperson/member and selected Books organisation. Changing one of those identities cannot reuse another scope's active inventory snapshot.

## Complete, fenced Zoho snapshots

`backend/app/catalogue_index.py` reads the active Books catalogue with an empty search query and paginates the source. A snapshot becomes active only after every page completes and the selected Books organisation is checked again. Item IDs deduplicate records; location rows are never summed into invented stock totals. An item removed or deactivated in Books disappears when a later complete snapshot replaces the previous one.

Database leases and increasing fencing numbers prevent a delayed worker from publishing over a newer synchronization. Failed, malformed, repeated or incomplete page sequences leave the last complete snapshot active. That snapshot is visibly stale; stale items require fresh selling-price and stock confirmation. An initial synchronization has no previous complete data to display and reports syncing/unavailable instead.

The implementation requests pages of up to 200 records and guards against more than 500 pages. These are operational bounds, not a promise that a provider returned a complete catalogue. Missing pagination metadata requires an explicit empty terminal page. Refresh is demand-driven when catalogue retrieval runs; this is not a separately deployed scheduler or a real-time inventory webhook.

Opening a hosted customer session starts a due snapshot sync after the HTTP response, before microphone input. The first complete inventory/embedding build can take longer than later searches. The previous complete snapshot remains usable during refresh with age/review metadata; an empty first build reports synchronization rather than inventing results.

Apply `supabase/migrations/202610290002_catalogue_index.sql` through the deployment's normal database migration process. It adds member-private scope, snapshot and item tables, full-text/SKU indexes and optional vector storage. Browser roles cannot read these tables directly. Backend queries carry the full scope. Existing organisation/member and recording migrations must already be present.

## Candidate retrieval does not prove compatibility

Exact SKU, PostgreSQL full-text search and optional trigram spelling search retrieve inventory candidates. When configured and available, dense embeddings add semantically related candidates. Reciprocal rank fusion combines ranked lists; it does not convert them into probabilities. The optional pgvector extension stores 384-dimensional vectors, with exact distance search available when its HNSW index cannot be created. Without vector capability or an available embedding provider, lexical retrieval remains available with truthful semantic status.

The index retains the model identity with each vector. Unchanged item text can reuse vectors from the previous active snapshot under the same scope and model. Different models do not share an embedding space. Background item embedding uses bounded batches rather than a catalogue-sized request. Product search terms are embedded separately from the speech pipeline.

When fresh inventory has missing embeddings or a changed model, background semantic repair reads that complete stored snapshot and preserves successful reusable batches. Durable retry metadata bounds repair attempts. The inventory observation time remains the source of stock/price freshness; rebuilding vectors does not make old inventory newly observed. Retrieval uses dense vectors only when the active model identity matches, and otherwise continues with lexical results and a pending/unavailable semantic status.

Retrieval has a fair budget of 64 distinct queries. Each active requirement receives its focused SKU/product/specification query before spare capacity is used for synonyms. Requirements sharing the same query can share a lookup. If a bulk request exceeds that budget, `deferred_requirements` preserves the remaining requirement IDs with `status: lookup_deferred`; those requirements are not reported as incompatible or as having no match. The workspace shows the deferred count and each affected requirement in agent guidance. `retrieval_exhaustive: false` also makes clear that ranked candidate retrieval is not an exhaustive compatibility scan of every inventory record, even when the source snapshot itself is complete.

### Existing-provider API configuration

`backend/app/catalogue_embeddings.py` uses the already-installed OpenAI SDK and the backend's existing `OPENAI_API_KEY`. The default is `text-embedding-3-small`; `text-embedding-3-large` is also accepted. Requests explicitly ask for 384 floating-point dimensions through the embeddings API. The `dimensions` parameter is supported for these models in the [official OpenAI API reference](https://developers.openai.com/api/reference/python/resources/embeddings/methods/create).

Configure the existing backend process; keep credentials out of the browser:

```dotenv
# Retain the deployment's existing backend OpenAI credential.
OPENAI_API_KEY=
CATALOGUE_SEMANTICS_ENABLED=true
CATALOGUE_EMBEDDING_MODEL=text-embedding-3-small
CATALOGUE_EMBEDDING_TIMEOUT_SECONDS=20
CATALOGUE_REFRESH_SECONDS=900
CATALOGUE_SYNC_LEASE_SECONDS=120
```

An empty key or `CATALOGUE_SEMANTICS_ENABLED=false` leaves catalogue retrieval lexical. Semantic requests use the existing provider account and can incur usage charges; no provider request is needed to run the mocked regression tests. No embedding URL, inference container, model download or extra service is configured. The client is supplied item text and product search queries rather than complete transcripts or customer contact objects.

Each provider request contains at most 32 inputs, each bounded to 6,000 UTF-8 bytes. Two requests may be in flight per client; the timeout defaults to 20 seconds and automatic SDK retries are disabled. Returned model identity, batch indexes, vector dimensions, finite values and nonzero magnitude are checked. Query vectors have a process-local cache capped at 128 entries and five minutes; durable item vectors are reused by text hash and model identity. Provider failure leaves lexical search available and exposes semantic unavailability instead of treating a mock/vector fallback as real semantic output.

`backend/app/catalogue_matching.py` checks the retrieved records against each active requirement. There are no runtime lists of catalogue products, brands or SKUs. Category synonyms, accessory exclusions and required features come from the current requirement plan; records and their source fields remain authoritative.

The checks include exact SKU/model identity, explicit category conflicts, accessories, requested features, brands, budgets and typed technical specifications. Numeric units distinguish values such as 1 HP and 10 HP, 10 A and 16 A, one and three phases, SP and DP, 1.5 and 15 sq mm, 120 and 1200 mm, voltage, speed, frequency and related ratings. Explicit ranges and slash-separated alternatives are handled differently. A dimension named width cannot be satisfied by an unrelated diameter. Characters inside a SKU do not become a technical rating.

Known incompatible values reject a candidate. Missing values produce `needs_confirmation`; they are not silently supplied from general product knowledge. Ambiguous options or conflicting source fields remain unverified. A description stating that a product is not suitable for 3-phase installations cannot prove 3-phase compatibility. Denied ratings are retained separately with source evidence, while an explicitly supported 1-phase rating can still match. Expressions such as “not only … but also” and “no-load speed” do not automatically negate their ratings.

Recommendations expose `matched_specs`, `missing_specs`, `conflicts`, `match_reason`, source spans and per-requirement assessments. `verified` means the available record supports the stated constraints that were checked. It is not an engineering certification, installation approval or guarantee that an unstated requirement has been met.

| Response field | Meaning |
| --- | --- |
| `catalogue_status` | `syncing`, `ready`, `stale` or `unavailable`; snapshot metadata identifies the current complete source and its age. |
| `match_status` | `verified` or `needs_confirmation`; known incompatible candidates are excluded. |
| `price_status` | A known source price or a requirement to confirm it; a placeholder zero is not automatically free. |
| `availability_status` | `in_stock`, `out_of_stock`, `needs_review` or `not_tracked`. Missing/negative source stock cannot promise availability; untracked services/goods do not acquire an invented stock count. |
| `clarification_suggestions` | Agent-facing questions tied to requirement IDs, missing fields and evidence. |
| `current_intent` / `agent_guidance` | Advisory conversation context for the human agent. |
| `requirements[].status: lookup_deferred` | This requirement remains pending catalogue lookup; a query-budget limit is not evidence of no match. |
| `deferred_requirements` / `retrieval_exhaustive` | Coverage metadata distinguishes unsearched requirements and bounded ranked retrieval from a complete source snapshot. |

## Agent-only clarifications and cart review

The live API supplies clarification suggestions with stable IDs, requirement IDs, missing fields and evidence. The workspace renders them in **Agent guidance**, outside the customer conversation. The human agent asks the customer and records the answer through the ongoing conversation or session workflow. The application neither asks on the agent's behalf nor invents the response.

Product cards, catalogue search and product details use the same review controls. Unverified technical matches require an explicit acknowledgement before adding. That acknowledgement preserves `needs_confirmation`; it does not manufacture a verified specification. Changed live evidence resets the review, including an already-open product details dialog. Source catalogue evidence is available in product details.

Unknown stock is labelled **Confirm stock**. Known out-of-stock inventory items are labelled and blocked from addition. Explicit untracked services/goods show **Stock not tracked**, preserving source counters without an in-stock promise. They can be added with a known positive price and any required match review; an explicit immediate-availability request still needs human confirmation. Missing, zero, invalid or confirmation-required prices are labelled **Confirm price** and cannot enter the cart. Existing unpriced cart lines do not show a free total and cannot complete checkout. Local demo inventory remains explicitly labelled sample data and does not stand in for a connected Books catalogue.

## Financial documents recheck current Books facts

`backend/app/sales_documents.py` fetches current, member-scoped Books item details before creating an invoice, quote or sales order. It validates identity, active/sellable state, positive finite selling price and the price reviewed in the cart. A missing, zero, negative, nonfinite or confirmation-required selling price blocks creation. A zero-price complimentary-item workflow is not supported here; existing explicitly reviewed percentage/fixed discounts remain supported.

Tracked inventory requires known positive stock sufficient for the sum of all cart lines for the item. A negative/zero available quantity cannot be replaced by a positive on-hand quantity. Missing or malformed stock does not become zero or “in stock.” Explicit noninventory goods/services skip stock requirements, including meaningless zero stock counters on service records. They gain no invented availability fields. These distinctions use Books' `track_inventory`, `item_type` and `product_type` metadata. See the [official Books Items API](https://www.zoho.com/books/api/v3/items/).

This check is a fresh read before creation, not a stock reservation across competing external sales. Books remains authoritative for the final document and tax. Existing idempotency and uncertain-creation handling prevent a lost response from automatically creating another document. See [document-email-delivery.md](document-email-delivery.md) for PDF and delivery behavior.

## CSV and document limits

A supplied CSV can be replayed read-only for matching evaluation. It is not silently imported into a production catalogue. Blank manufacturer/brand/specification cells, abbreviated names, duplicated location rows, missing stock and placeholder prices stay missing or uncertain. Text similarity cannot recover a fact that the source never recorded. New product types may require clearer structured attributes or additional parser coverage; an unsupported technical expression should be reviewed before promising compatibility.

Docling addresses document conversion, layout, OCR and extraction from formats such as PDF. Semantic embeddings address retrieval over text that has already been extracted. Docling is not part of this runtime and would not validate electrical specifications or repair missing CSV facts. Its role would be a separately requested document-ingestion workflow. See [Docling's upstream project](https://github.com/docling-project/docling).

## Verification and dependencies

The focused checks use synthetic inventory/provider responses and an optional user-supplied read-only CSV replay. They cover technical mismatches, negated ratings, stale/missing data, scoped snapshots, pagination failures, stock/price checkout guards, agent guidance and browser recording/cart behavior. Passing these checks does not establish production retrieval quality or demonstrate a live embedding-provider call. Validate representative catalogue records and spoken requirements before relying on automatic ranking.

A separate actual OpenAI embedding-API smoke check returned 384-dimensional vectors and ranked the fan candidate above a fan regulator and motor for a fan request. This verifies provider connectivity, vector shape and one small candidate-ranking example. It does not establish general catalogue accuracy or end-to-end structured speech understanding. The native API/migration regression suite also passed 10 tests.

The subsequent actual three-turn understanding check passed: a 1 HP single-phase motor and 10 A double-pole C-curve MCB were extracted, a correction changed only the motor power to 2 HP while retaining phase, a quotation request remained advisory, and the breaker withdrawal was preserved. Analysis completed in about 4–6 seconds per turn in that run, excluding audio capture/STT/translation; this is an observation, not a latency guarantee. `backend/scripts/check-live-semantics.py --live-api` reproduces this synthetic provider check with assertions. Run from `backend` using its virtualenv; it incurs configured API usage and creates no CRM data, business documents or email. These small checks do not establish general accuracy across real conversations.

Combined final regressions passed 282 backend tests including the supplied CSV replay, 42 focused UI tests, TypeScript and the production frontend build. Native catalogue tests passed 31 checks; the full-schema native API suite passed 10. The local PostgreSQL installation lacked pgvector, so its extension-unavailable fallback was exercised; hosted pgvector retrieval and real connected Zoho inventory still require deployment-specific verification.

The supplied `Items-by-gg.csv` was replayed read-only on 2026-10-30. It contained 10,582 rows and 9,970 unique item IDs. These counts describe export rows, including repeated IDs, rather than aggregated stock:

| Source-data observation | Rows |
| --- | ---: |
| Blank brand | 10,496 |
| Selling price requires confirmation | 7,694 |
| Known selling price | 2,888 |
| Stock requires review | 967 |
| Out of stock | 6,414 |
| In stock | 3,201 |

For the 10 A / DP / C-curve MCB benchmark, matching returned the expected item `3366620000000045383`, **10A DP 6KA TYPE C MCB - BA20100C**, with source-backed current/pole/curve matches and rate 1,025. Its exported stock was zero, so it remained `needs_confirmation` with `out_of_stock`; technical similarity did not become a supply promise. The combined matcher and sales-document run passed 142 tests with this optional replay enabled. The export was not imported into application inventory, and these export facts do not establish current live Books availability.

PostgreSQL uses the [PostgreSQL License](https://www.postgresql.org/about/licence/); optional pgvector uses its upstream [PostgreSQL License](https://github.com/pgvector/pgvector/blob/master/LICENSE). The application does not bundle an open-model embedding server or model weights. Managed API access is configured on the backend under the deployment's existing provider account.
