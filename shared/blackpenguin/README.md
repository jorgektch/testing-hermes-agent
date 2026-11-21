# Canonical synthetic BlackPenguin-inspired data — Hermes Lab v3

All files are **fictional test data** adapted from the user's BlackPenguin Project/Lead/Meetings/Availability dictionaries and sample JSONs. They are **not** a database dump, the actual current API payload or proof that proposed v2 columns are already migrated.

## One owner per entity

- `project.json`: Company/Project identity, profile publication, phase references, commercial profile and demo provenance.
- `property_types.json`: four confirmed-for-demo sellable **models**.
- `units.json`: five **physical units**, including reserved H-102 (never proposed as available), with inventory events.
- `features.json`: typed feature dictionary and project-level feature values; type and unit overrides live under their respective owners.
- `assets.json`: source files, approval status, coverage, explicit simulation-channel permission, and gallery link bindings.
- `team.json`: three fictitious users, project assignments and an additional project reference for cross-project busy checks.
- `availability.json`: weekly rules and **synthetic proposed** positive/negative date exceptions.
- `meetings.json`: **preexisting demo busy** meetings. The lead's actual demo booking is stored in the **session state**, not written into this file.
- `scheduling_policy.json`: 45m visit, 15m buffer, 30m slot step, 14-day horizon, 30m offer expiry and explicit confirmation requirements.
- `lead.json`: blank BlackPenguin-shaped lead/dialogue session template (an anonymous demo draft, not a valid persisted production Lead).
- `lead_example.json`: populated synthetic lead with message-level evidence, for documentation only.
- `references/`: original synthetic source examples and the previous `advisors.json` compatibility fixture, all non-canonical.

## Derived outputs

`python scripts/build_catalog_from_blackpenguin.py` validates basic identity and asset permissions, then produces `shared/catalog.json` and its backend mirror. The published catalog contains only synthetic **available & publishable** units with confirmed demo property types and approved media. It intentionally omits internal sources, unapproved assets, financing claims, and the reserved unit H-102.

**Do not manually edit generated catalogs**. Regenerate after editing the canonical JSONs.

The `backend/app/{team,availability,meetings,scheduling_policy,lead}.json` copies are mirrors needed because Vercel may package the Python service separately from sibling `shared/` files. These mirrors are not independent sources of truth.

## Scheduling semantics

Unlike BlackPenguin's currently documented availability tables, the demo introduces `sales_availability_exceptions` with `kind: unavailable` and `kind: extra_availability`. The synthetic `day_offset` in fixtures is relative to the **current local day** (America/Lima). It is used to make the demo useful regardless of deployment date; **production appointments should always use absolute UTC instants**.

`available_slots` is never stored as a table. A proposed option has a 30-minute expiry but does **not** hold capacity. The browser session can create one isolated meeting after confirmation. No calendar synchronization, concurrency protection between browsers, or real lead contact takes place.

## v7 house-only sale scenario

This active demo uses `transaction_type=sale` and a single published `house` category, while retaining a flexible BlackPenguin-inspired type/unit data model for future projects. Four independent fictional houses (H-201, H-204, H-101, H-104) are offered; reserved H-102 is not publishable. Apartment references in `references/` are original archived examples, not live listings. Old lead conversations and citations remain historical; do not remap their property IDs. Every published house has two synthetic illustrations and an individual PDF fact sheet.
