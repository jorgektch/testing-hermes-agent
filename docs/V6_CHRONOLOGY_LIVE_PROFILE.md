# Hermes Agent Lab v6 — Message chronology and live, source-backed lead facts

## Verified integration approach

The application continues using NousResearch Hermes Agent (`AIAgent.run_conversation`) and the existing registered domain tools. The pinned library revision in `backend/requirements.txt` is the integration contract. Nothing here replaces Hermes with a separate LLM or patches its agent loop.

- FastAPI owns authentication, CSRF, request coordination and atomic persistence.
- Hermes selects and invokes approved domain tools (the same nine as v5).
- `update_lead_profile` and `track_property_interest` produce **provisional, observable** tool-state updates through an optional callback in the existing registry handlers.
- A narrowly scoped deterministic validation of *explicit* English requests such as `I'm looking for a 2-bedroom house` updates the established `domain.update_lead` path, not a second extractor model. Questions, alternatives and negated requests are ignored. An audit observation distinguishes this validation from actual Hermes tool calls.
- Turso commits the complete conversation turn and profile changes together. Streaming previews are not durable until the `committed` event.

## Chronology and migrations

The original greeting was stored with `Z`/UTC and subsequent messages with `-05:00` local offsets. `ORDER BY created_at, rowid` on `TEXT` could therefore place a greeting *after* subsequent messages.

- All new message `created_at` timestamps are normalized to ISO 8601 UTC at millisecond precision.
- `messages.sequence_number` is the authoritative per-conversation display order, and the greeting starts at 1. A pair of lead/Hermes messages increments by two.
- `schema_meta['message_sequence_v1']` guards a one-time, **non-destructive** backfill. Historical timestamps are parsed with offsets, ordered by true UTC instant and legacy insertion `rowid` for ties, then normalized to UTC. No lead or message is deleted.
- `list_leads` gets the latest message by sequence; `lead_get` sorts by sequence. Chat labels show date, hour, minute and second in the browser's local timezone (fallback: America/Lima), with an additional divider for each date.
- The migration executes automatically in `db.initialize()` on the first v6 request. Turso credentials, data location and the existing database tables stay in place. This migrates the primary database; no separate database should be created.

## Secure live event transport

`POST /api/leads/{lead_id}/chat/stream` uses the same authenticated administrator session and `X-Demo-Csrf` header as the existing POST endpoint. `StreamingResponse` emits UTF-8 Server-Sent Events. Vercel's Python runtime supports StreamingResponse.

| Event | Meaning | Durability |
| --- | --- | --- |
| `received` | Server accepted an inbound message and assigned ID and UTC time | Provisional |
| `profile_preview` | Validated explicit facts or Hermes profile/interest tool results; source fields included | Provisional |
| `committed` | Turn, messages, facts and appointments saved atomically in Turso; includes the canonical updated lead | Durable |
| `error` | Hermes/DB failure; UI discards all previews, restores the prior conversation | No turn saved |
| `: keep-alive` | Maintains HTTP stream during a slow model call | N/A |

The original non-streaming endpoint remains available for API compatibility. Browser `fetch` (not a public GET EventSource) streams authenticated POST events. Request-scoped threads allow the blocking Hermes library to execute while domain tool updates are emitted. This is not an asynchronous detached background task.

## Panel and evidence

- Qualification: budget, timeframe, property category and number of bedrooms, with source-message timestamp links when captured.
- Contact: updates to registered name, phone or email are reflected in the primary `leads` table during the same transaction.
- Additional facts: all other present BlackPenguin commercial facts from `sales_profile.current`, without duplicating the main qualification fields.
- Interest and appointment remain separate, avoiding confusion between a project inquiry and a particular selected unit.
- `ui_fact_evidence` adds provenance for UI-only fields such as House/Apartment; canonical BP facts retain their `source_ref.message_id` and evidence.
- Source buttons navigate and highlight the exact message. The panel marks tool-event previews as **pending save**.
- Periodic synchronization and focus refresh reconcile updates by another administrator. Data snapshots never include the private Hermes continuation history.

## Quality checks

Run `cd backend && PYTHONPATH=. python -m pytest tests -q` and check TypeScript JSX parsing or `cd frontend && npm run lint` (after installing frontend dependencies). The added regression suite tests legacy timezone migration, true ordering, source message references, explicit/negated phrases, streaming provisional and committed events, stream failure rollback, CSRF, contact updates and the Hermes tool integration callback.

## Remaining production considerations

- Verify streaming behavior with a real Hermes/OpenRouter turn in Vercel (mock tests do not exercise a paid model).
- A disconnect after the server has started a turn can still allow it to finish and commit. Reload the conversation before retrying; duplicate-message idempotency for network retries would be a separate feature.
- Streaming cannot exceed Vercel's function duration. Enabling streaming does not remove that limit.
- Do not use `DEMO_ADMIN_PASSWORD=demo` on a public URL with real lead information; it grants permanent deletion permissions.

References: https://hermes-agent.nousresearch.com/docs/guides/python-library ; https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/plugins.md ; https://vercel.com/kb/guide/ship-a-fastapi-app-on-vercel
