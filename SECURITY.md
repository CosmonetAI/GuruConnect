# Security reporting and maintenance

Do not open a public issue containing customer data, audio, provider keys, access tokens or exploit details. Use the repository's GitHub private vulnerability reporting facility when enabled; otherwise contact the repository owner privately through an established channel. Acknowledge and track reports internally; assign an incident owner before launch.

Production releases must pass the CI security, isolation and regression checks. A clean dependency scan is not a security certification. Changes to authentication, workspace ownership, database functions, storage, upload limits, provider retries or deletion require focused adversarial tests and reviewer approval. Applied SQL migrations are immutable; use forward migrations.

Never put service-role, encryption or provider secrets in `VITE_` variables. Rotate credentials if exposed and investigate access; deleting a Git commit does not revoke a credential. Restricted frontend builds reject privileged Supabase keys. Runtime secrets belong only in the Render API/worker environments. Keep recovery and database encryption keys available in protected backups.

See [the deployment runbook](docs/PRODUCTION.md) and [the audit](docs/PRODUCTION_AUDIT.md) for release gates and the remaining operational work.
