# Hermes Agent Lab v9 — Progressive Sales SMS

## Objective

Lead qualification and house recommendations must proceed as a conversation, not a mass property listing. The initial welcome is a single message sent when the lead is created. Subsequent turns should acknowledge what the buyer said instead of repeating the agent's introduction.

## Architecture and Hermes integration

This version **retains Nous Research Hermes `AIAgent.run_conversation()`** with `conversation_history` persisted in Turso, a per-request agent, an allowlisted `blackpenguin_real_estate_demo` toolset, and `quiet_mode=True`. We did not replace Hermes with another reasoning engine or add a second LLM for extraction or rewriting. Consult the [official Python library guide](https://hermes-agent.nousresearch.com/docs/guides/python-library). The repository continues to pin the existing Hermes commit in `backend/requirements.txt`; new features here use APIs the repository already exercised.

Business and safety operations stay in deterministic domain tools, not prompt-only instructions. The backend does not directly choose an advisor, price, booking slot, or unit on behalf of the model.

## Improvements

1. **One introduction.** Newly created leads persist the exact initial welcome in both `messages` and `hermes_history_json`. Legacy conversations that stored only the visible greeting receive a deduplicated, read-only context repair on their next turn; stored messages are not rewritten. The Hermes prompt says the welcome was already sent. A small delivery guard strips an accidental *leading* repeated introduction.
2. **Compact property discovery.** `evaluate_properties_for_lead` still uses deterministic budget, bedroom, feature, and readiness filters. It now returns compact ranked objects (ID, title, bedrooms, price, availability/status), not full assets, long descriptions or links. The AI should offer at most three concise options and ask which to explore.
3. **Per-unit detail tool.** `get_unit_details(unit_id)` supplies factual attributes for one house requested by the buyer, without media. An explicitly mentioned unit or a previous conversational focus is needed. `exploring_unit_id` is distinct from `lead.interest_unit_id` (selected for a visit).
4. **Buyer-requested media only.** `get_unit_media(unit_id)` is gated by a buyer request for photos, gallery, floor plan, brochure or PDF. The result contains only requested asset categories and approved absolute URLs. A simple affirmative response can accept a single-media offer; an ambiguous “photos or visit?” does not automatically authorize either option.
5. **Outbound URL verification.** The delivery policy checks every HTTPS/HTTP URL against those issued by the media tool in the *same* turn. If Hermes produces fictional or unrelated links, they do not reach the lead. If it was a verbose/bad property search response, the backend replaces it with a one-SMS answer made exclusively from the verified matching result. For a non-search turn with no grounded fallback, it fails closed without saving the turn, so the operator can investigate.
6. **SMS semantics.** Hermes is prompted to prefer one GSM-7 SMS with one buyer question. The existing semantic SMS module handles multipart messages without cutting URLs or short factual phrases. The backend never silently truncates booking confirmations or approved URLs.
7. **Real-time project focus.** The UI distinguishes houses that match a search, the house being explored, and the house explicitly chosen for an onsite visit. Streaming state snapshots include the current explored unit.

## Example (synthetic demo)

**Initial automatic greeting:** Hi Jorge! I'm the AI Sales Agent assigned to help you at Residencial Jardines del Sol. What kind of house and budget do you have in mind?

**Buyer:** I'm looking for a two-bedroom house for up to $300k, this year.

**Hermes:** I found 2 houses: H-201 (2 beds, $245k) and H-204 (3 beds, $295k). Which one would you like to explore?

**Buyer:** Tell me about H-201.

**Hermes:** H-201 has 2 bedrooms, 2 bathrooms and a private backyard. Would you like photos or to discuss a visit?

**Buyer:** Send me the photos of H-201.

**Hermes:** Photos for H-201: https://testing-hermes-agent-alpha.vercel.app/properties/H-201/gallery

The examples illustrate target behavior; final replies in a live call remain model-generated and are subject to the deterministic guardrails.

## Deployment and compatibility

- No database reset or schema migration is required. Historic leads, messages and bookings remain intact.
- `DEMO_PUBLIC_BASE_URL` should point to the correct public deployment domain; media URLs are never created from arbitrary incoming `Host` headers.
- `DEMO_PERSISTENT_TURSO_URL` / `DEMO_PERSISTENT_TURSO_TOKEN` or the configured Turso pair remain unchanged.
- The admin-only login, advisor availability, one-hour slots, round-robin booking, SMS metrics, JSON export, and copying individual messages are unchanged.
- The v9 Python regression suite can be run with `PYTHONPATH=backend python -m pytest -q backend/tests`.
- Release through Preview first; test real OpenRouter turns for unwanted self-introduction, choice-first recommendations, media authorization and rescheduling. Tests using a mock `AIAgent` do not guarantee identical behavior from the remote model.

## Acceptance scenarios

- Welcome appears first and is present exactly once in Hermes history.
- Search output for house + 2 bedrooms + $300k lists H-201 and H-204, not H-101/H-104 or media links.
- Asking for more details of H-201 returns details about H-201 only and updates `exploring_unit_id`, without booking intent.
- “The second one” refers to the second shortlisted house, when shortlist exists.
- Requesting photos of H-201 yields the approved gallery URL, not `example.com` or an invented `/demo-assets` host.
- Asking for photos and then giving an unambiguous “yes” to a standalone gallery offer can authorize the gallery; “photos or visit?” requires clarification.
- Booking still requires explicit selection of one house, a day, an offered hour, and a subsequent confirmation; round robin continues in the database.
