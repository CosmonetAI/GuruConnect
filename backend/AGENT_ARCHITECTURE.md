# Guru Group Sales Agent Architecture

## Request lifecycle

`chat request -> identity/session -> understand -> route -> Zoho tools -> safety/grounding -> compose -> persist -> response`

### Understand node

- Detects the latest-turn language: English, Hindi, Roman-script Hinglish, or Kannada/native Kannada.
- Extracts product/model/SKU, quantity, phone, email, and customer name.
- Uses OpenAI for structured JSON classification when `OPENAI_API_KEY` is configured.
- Falls back to deterministic multilingual intent cues when OpenAI is unavailable.

### Route node

Maps the intent to a small allowlisted plan. It never lets model text choose an arbitrary URL or Python function.

- Product search/details/recommendation/availability/quotation -> Zoho Books items.
- Customer lookup -> Zoho Bigin contacts.
- Quotation/follow-up/general enquiry -> Bigin CRM context/contact capture.
- Order status -> Zoho Books sales orders.
- Human handoff -> response escalation without pretending a handoff happened.

### Execute Zoho node

Only this node calls Zoho. It uses the existing server-side OAuth client, refreshes access tokens when needed, and returns bounded verified records. If Zoho is disconnected or a call fails, the result is marked unavailable and the response cannot claim a price, stock level, customer record, quote, or order.

### Compose node

OpenAI receives the customer message plus verified Zoho results and must answer in the detected language. It is instructed to preserve exact IDs/prices/statuses, ask only for missing details, and avoid inventing commercial facts. No tool result means no factual claim.
