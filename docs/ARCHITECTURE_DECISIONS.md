# Architecture decisions — Hermes Real Estate Lab v3

**Scope:** a **single-project synthetic sales simulation**, designed to be explainable, demonstrable and deployable from a monorepo. **Not** a schema migration or production replacement for BlackPenguin.

## 1. Source of truth versus projections

| Domain | Canonical source | Derived or temporary |
|---|---|---|
| Identity and publications | `project.json` | `catalog.project_info` |
| Model-level traits | `property_types.json` + `features.json` | Unit highlights, gallery card attributes |
| Unit status/price | `units.json` | Available catalog, “Last Unit” badge derived from publishable counts |
| Assets | `assets.json` | Approved, simulation-allowed public media URLs |
| Seller identities/assignments | `team.json` | Eligible advisors for a specific project |
| Availability | `availability.json` + `meetings.json` + `scheduling_policy.json` | Calculated slots and shortlist of offered options |
| Lead declarations | `lead.json` template → `bp_episode.leads.lead_profile_data.sales_profile.current` | UI's nine-field `lead` view, qualification completeness, natural-language summary |
| Booking | Signed session's `bp_episode.meetings` + `sales_booking_operations` | “Visit confirmed” card in browser |

The publication builder has explicit allowlist rules. Internal/private sources and nonapproved assets never enter the published catalog. Offers of financing remain outside the agent-facing catalog.

## 2. Project / Property Type / Unit

`Company → Project → Property Type → Unit`. A unit must point to an unambiguous parent type. Type-level confirmed features are inherited as a default; unit-level confirmed values override. `null` is unknown, never synonymous with `false`. Project-wide common amenities do not imply private unit amenities. Fields such as published `starting_price` are derived summaries, not additional editable sources of truth.

The demo preserves H-102's `reserved` status in `units.json` and **never** recommends it. `available_from: null` maps to “Immediate” only within this synthetic completed-project scenario; production would require explicit verification of readiness.

## 3. Lead / Conversation / Evidence

- The original user text is stored once as a simulated message with `lead_id`, `project_id` and `message_id`.
- Commercial facts are stored as `current[field]`, including `value`, `status=declared`, `source`, `captured_at`, `evidence`, `source_ref.message_id` and `supersedes`. Previous versions move to bounded `history`.
- `declared` means **the lead said it**, not verified budget, personal identity, consent or creditworthiness.
- The dialogue's `selected_unit_id` represents conversational focus; it is not automatically identical to purchase intent.
- The profile is the canonical evidence store for 16 commercial fields; UI summary fields are materialized projections for display.
- The app doesn't infer age, income, ability to pay or scoring.

## 4. Scheduling and booking

**Scheduler contracts:**

| Term | Meaning |
|---|---|
| `sales_availability_windows` | Weekly hours belonging to a sales user, not a project |
| `sales_availability_exceptions` | Synthetic v3 extension: add/subtract availability on dates |
| `demo_existing_meetings` | Busy periods, including visits for other projects |
| `project_user_assignments` | Determines user eligibility for the requested project |
| Candidate slot | Calculated instant every 30m, for an available 60m interval |
| Offered option | One of up to three candidate slots presented to the lead; expires after 30m, no hold |
| Booking | Only after explicit confirmation in a **later** lead turn and recomputation of the exact option |
| Meeting | Simulated record with `status=confirmed`, 45-minute duration, 15-minute buffer and UTC start |
| Booking operation | Idempotency-oriented receipt, `calendar_status=simulation` |

**Deliberate compatibility distinction:** BlackPenguin's documented current scheduler checks 45-minute meetings on 30-minute candidate starts, but does not automatically implement a 15-minute buffer or unavailable exception type. Both are explicit research-demo extensions here.

**Implemented within session:** cancellation and reprogramming leave the old Meeting as cancelled, record a linked operation, and create a new Meeting when rescheduling. **Not implemented:** cross-session capacity locking, external OAuth/FreeBusy, real-time calendar side effects, real event outbox, global rate limiting or durable lead purging. Do not claim them in the UI.

## 5. Integrity and security

- Company and project identity are checked when preparing data; generated catalog items reference valid IDs.
- Sales users are eligible only when user and assignment are active, role is `sales` or `admin`, `sales_enabled` and `accepts_appointments` are enabled.
- A user's existing meetings on **any project** block the same time globally.
- Confirming a slot does not rely only on the model's claimed approval: the demo validates that the current inbound message contains explicit confirmation language; the exact offered proposal and slot must match, not be expired and not be from the same turn.
- Every appointment remains in a signed, **not encrypted**, client-held state; no durable multi-user lock exists. Do not use real contact data.
- The server obtains Hermes conversation history only from the signed state token, not from a caller-provided JSON history array.
- The 14-turn counter and access code are not abuse-proof: a real public launch would require provider-side rate limits, logging/monitoring, durable sessions and abuse protection.

## 6. Follow-on work for BlackPenguin production

1. Define and execute additive migrations (do not treat the v2 fixture as already live).
2. Add database-backed availability exceptions, travel buffers and location capacity.
3. Use transaction/isolation controls to prevent overlapping bookings across different users/sessions and projects.
4. Separate round-robin cursors for lead routing versus appointment assignment.
5. Add verified inventory/price freshness policy and typed filter semantics with lineage.
6. Implement external calendar idempotency, retries, cancellation/rescheduling, and deletion compensations.
7. Preserve privacy by limiting PII in long-lived traces, tokens, backup and public frontend components.
8. Validate the Hermes runtime on Vercel or move it to a persistent Python host if serverless constraints block it.
