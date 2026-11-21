# V4 Architecture Decisions — Hermes Agent Evaluation Lab

**Scope:** synthetic research evaluation of an external LLM agent with structured property knowledge, durable conversation state and a shared scheduling UI.

## Ownership by domain

- **JSON fixtures (read only):** Project, Property Type, Unit, media publication, typed confirmed features, advisor identities/assignments, recurring availability, exceptions and policy. The public `shared/catalog.json` and `backend/app/catalog.json` are generated. Source reference files are proposals/examples, not production BlackPenguin tables.
- **SQLite / Turso libSQL (durable):** lead identities, session ownership, conversation snapshots, Hermes continuation history, message evidence, leads' interests, slot offers, meetings and occupancy. Never persist executable Hermes history in a client token.
- **Calculated:** candidates from recurring windows/exceptions + advisor-wide occupied meetings; no materialized infinite future slots.
- **Operational:** 45-minute meeting + 15-minute buffer, 30-minute candidate-start step, 14-day booking horizon, 30-minute proposal expiry, one active visit per lead, three offered choices.

## Persistence and concurrency

`backend/app/persistence.py` has a stdlib `sqlite3` local implementation and an over-the-wire `libsql` adapter chosen when `TURSO_DATABASE_URL`/`TURSO_AUTH_TOKEN` are present. Production rejects unconfigured ephemeral storage.

`meetings` is authoritative for confirmed/cancelled/rescheduled occupancy. `meeting_occupancy` reserves **UTC 15-minute bucket keys** via `PRIMARY KEY(advisor_id, bucket_utc)`. Each 45+15 appointment inserts four bucket keys within the same transaction that saves the updated conversation revision and messages. A second simultaneous booking of any overlapping bucket fails, rolls back and receives HTTP 409. Cancelling or deleting a lead releases keys; rescheduling releases old keys before inserting new ones in one transaction. DB revision CAS on the conversation rejects concurrent writes to the same lead.

Existing demo fixtures are converted from `day_offset` to an **absolute UTC time exactly once**, when the DB is first initialized. This avoids appointments moving overnight. Other-project appointments for the same advisor block its agenda across projects.

Turso/libSQL remote transaction and driver semantics require deployment smoke tests. Local SQLite tests do not substitute for distributed test results.

## Security boundaries

Login mode: `DEMO_ADMIN_PASSWORD` for the sole administrator role (server-side, minimum length 16 unless explicitly in insecure demo mode). `DEMO_ACCESS_CODE` is deprecated and ignored. Authentication is tied to a random bearer cookie, hashed in DB and `HttpOnly`, `SameSite=Lax`, `Secure` on Vercel. Each mutating route requires an `X-Demo-Csrf` token derived from `DEMO_SIGNING_SECRET`; no cross-origin CORS access. `Cache-Control: no-store` on API responses. DB queries scope to owner; admin can inspect/delete all. The single administrator role can inspect all leads and their appointments; previous evaluator sessions are rejected.

**Deletion:** administrator only, remove meetings/occupancy, appointment offers, messages (including original fact evidence), interests, facts, conversation snapshot, Hermes tool history and identity in a DB transaction. DB-backed access returns 404 immediately after deletion; browser polling removes stale viewers. External/backup copies cannot be guaranteed eliminated by the app.

## Agent contracts

Hermes uses nine registered domain tools, restricted to its custom toolset; all other terminal/file/web/browser tools are disabled. The server provides the fictional lead's registered four contact fields in a data-only prompt section so the advisor need not request them twice. `track_property_interest` distinguishes mentions, consideration and explicit interest; a booked visit may be project-wide or for one specific unit.

A successful Hermes tool call is **not** a durable booking until the API transaction commits. If another session takes a time slot between a model lookup and persistence, the DB rejects the overlapping booking; the API returns conflict rather than a false confirmation. Slot/calendar UI uses the same DB occupied intervals.

## Frontend behavior

One pathname and one sidebar link. Left panel has inventory and calendar tabs; expanding the calendar overlays the same workspace. Day view is hour rows with advisor columns; week view shows seven date columns with per-advisor slot counts and meeting events. Toggle all/subset advisors; viewer-local time or `America/Lima`; clock and today indicator update every 30 seconds. Poll shared calendar every 20 seconds and on window focus; refresh immediately following chat.

Property modal draws exclusively from published catalog fields and local media. The chat renders Markdown through React Markdown and only permits `/demo-assets/*.svg|pdf` links. Evidence section references IDs in the `messages` table. The right pane shows contact, qualification, multiple property interests, active appointment and optional evidence/state inspector.

## Production readiness tasks not implemented

- Federated auth/identity verification, per-IP login throttling, strict spend quotas and abuse control.
- Provider-specific external logs/backups deletion guarantees and retention governance.
- Real Calendar FreeBusy / callback integration and real human appointment confirmation.
- Turso remote concurrent writer integration tests, load tests, end-to-end browser tests, disaster recovery.
