# Hermes Agent Lab v8 — House visits, SMS, lead focus and persistence

## Scope

This is a **synthetic sale-only house catalog**, **not a messaging provider**, real estate offer or physical appointment service. The project and showroom address in the fixture are fictional; Google Maps URLs remain `null` until the operator supplies a verified destination. Do not suggest navigation to the synthetic coordinates.

## Authoritative storage and deployment continuity

- Production leads, conversations, messages, commercial facts, offers, occupancy and confirmed appointments belong in **one remote Turso database**. Vercel deployments must not reset that database. The backend uses `CREATE TABLE IF NOT EXISTS` and adds only `advisor_rotation(project_id, next_index)`; it does not drop or truncate historical tables.
- The currently configured Marketplace pair is `TURSO_DATABASE_URL` + `TURSO_DATABASE_TURSO_AUTH_TOKEN`. Optional `DEMO_PERSISTENT_TURSO_URL` + `DEMO_PERSISTENT_TURSO_TOKEN` override the pair *together*, never one alone. Both new variables must be set to the credentials of the SAME stable main Turso database. Use Production scope, then deploy.
- Authenticate as administrator and visit `GET /api/admin/storage` or the **Database persistence** inspector. It reports the current environment, a SHA-256-based *fingerprint of the database URL* and aggregate lead/conversation/message/meeting counts. It never returns credentials or private lead data. Compare these between deployments. A change of fingerprint indicates that the backend is using a different URL, not that a deployment deleted records.
- The historical apartment unit codes from v6 remain in stored lead dialogues and meetings. Do not rewrite those records to point at newly published houses.
- The roster and policy live in versioned fixtures, whereas actual booked visits and round-robin cursor live in Turso.

## Scheduling protocol (server-owned)

1. **Select one house**: Hermes must confirm a published house code (`H-201`, etc.) explicitly mentioned by the lead and call `track_property_interest(status="interested")`. A shortlist is NOT a unit selection. A general project visit is no longer allowed for new bookings.
2. **Ask for a day**: `get_available_visit_slots(date_str="YYYY-MM-DD")` requires an exact day. It returns unique starts on whole hours and grouped time ranges. No names, `advisor_id` or `sales_user_id` are returned. Slots are projected availability, not holds. Repeated queries on the same house/day reuse the still-valid offer.
3. **Record the hour chosen by the lead**: `select_visit_time(proposal_id, date_str, start_time)` validates the offered slot and verifies that the lead's inbound message explicitly mentioned that clock time. It stores a source message ID and does NOT book anything.
4. **Ask for confirmation**: Hermes repeats the **unit, date and hour** and waits for affirmative consent in a *later* user turn. `book_onsite_visit` then returns a **pending commit**, with no advisor or confirmation message. This is intentional.
5. **Commit atomically**: `persistence.save_turn()` rechecks the selected hour using shared occupancy; computes available project-eligible staff; chooses the next eligible staff member from the persisted `advisor_rotation` cursor; inserts the meeting and 15-minute occupancy buckets under a uniqueness constraint; advances the cursor; and persists the chat/state. A conflict rolls the transaction back and the UI requests fresh availability. No advisor is announced before this succeeds.
6. **Send a receipt**: the backend replaces the model's provisional booking response with an SMS-safe confirmation containing house, local day/time and advisor name, only after successful assignment. The frontend shows the confirmed appointment and fictional visit location.
7. **Cancellation/rescheduling**: existing meetings remain historical rows; cancelled/replaced bookings release occupancy. Rescheduling creates a new pending reservation and runs the same round-robin process. The original meeting is not rewritten to another property.

### Booking constraints

| Attribute | Value |
| --- | --- |
| Visit duration | 45 minutes |
| Buffer after visit | 15 minutes |
| Blocked capacity | 60 minutes per advisor |
| Start alignment | `HH:00`, never `HH:30` |
| Booking horizon | 14 days (project-local dates) |
| Availability timezone | `America/Lima` |
| Advisor assignment | Transactional round robin per project |
| Advisor identity before confirmation | Hidden from Hermes and buyer |
| Max simultaneously confirmed appointments per demo lead | 1 (rescheduling/cancellation supported) |
| Demo location / Maps | Fictional; no Maps URL |

### Honest calendar semantics

The administrative calendar still displays all eligible advisors, their availability, occupied time and simulated booked appointments. The buyer sees **hour choices independent of advisor names**. No real Google Calendar synchronization or SMS sends take place in the demo.

## SMS format and provenance

- Hermes still runs **`AIAgent.run_conversation()` with `conversation_history`** and the project toolset in the registry. The toolset gains `select_visit_time`. FastAPI/domain tools enforce booking invariants, not a secondary LLM. This follows the [official Python integration guide](https://hermes-agent.nousresearch.com/docs/guides/python-library) and [tool registry documentation](https://hermes-agent.nousresearch.com/docs/developer-guide/adding-tools).
- Prompt requests plain-text SMS: no Markdown, emojis, tables or Markdown-formatted links; prefer one GSM-7 message and at most two segments when possible. `app/sms.py` normalizes common Markdown formatting, retains approved **full HTTPS URLs**, counts GSM-7 (including extension double-septets) or UCS-2 UTF-16 units, and splits long responses into separate SMS-sized **chat bubbles without truncating**. When a single approved URL itself exceeds an SMS segment, the backend rejects it instead of sending a broken link.
- Each bubble exposes SMS length, encoding and segment estimate. The demo does not contact Telnyx or other providers, and segmentation alone does not guarantee real-world provider behavior or delivery.
- Historical messages keep their original format and timestamps; only new assistant messages are normalized/segmented. The Hermes continuation history stays server-side.

## Lead state and export

- The side inspector shows the selected synthetic project, operation (house sale), matched houses and explicitly selected house; unit selection remains linked to message evidence.
- Show a visit address only as *fictional*, never as a real Google Maps pin without verified coordinates. Property location and visit location are separate concepts in the booking receipt.
- A `Copy` button on each message writes that message's **plain content** to the clipboard. A `JSON` button exports the authorized administrator's current lead, chronological messages and saved state as one versioned JSON document. Both actions are local browser operations; no export backend route, cookies or Hermes private continuation transcript are embedded in the JSON.
- Deletion permissions remain administrator-only; the deployment does not delete any leads.

## Tests and deployment verification

```bash
PYTHONPATH=backend python -m pytest -q backend/tests
cd frontend && npm install && npm run build
```

The Python tests use SQLite and a positional-only emulation for libSQL; they do **not** prove Vercel/Turso cross-region concurrency, SMS gateway deliverability or LLM behavior against OpenRouter. After a Preview build, exercise the complete 4-turn booking flow, two concurrent leads selecting the same hour, a reschedule and cancellation, the admin calendar, JSON export, message copying, and compare the database fingerprint/counts against Production.

**Do not promote to Production until a real Turso test confirms the transactional assignment behavior and existing data counts.**
