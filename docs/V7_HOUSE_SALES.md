# v7 — House-only residential sales evaluation

## Scope

The active fictional Residencial Jardines del Sol inventory is exclusively **new houses for sale**. The BlackPenguin-style entity model still allows other property categories and commercial transaction types for later projects; this demo's `project.transaction_type` is `sale` and `project.active_property_categories` contains only `house`.

| Unit | Property type | Bedrooms | Price (USD) | Available from | Public material |
|---|---|---:|---:|---|---|
| H-201 | Two-Bedroom Garden House | 2 | 245,000 | Immediate | Gallery (2 illustrations) + own PDF fact sheet |
| H-204 | Three-Bedroom Courtyard House | 3 | 295,000 | 2026-12-15 | Gallery (2 illustrations) + own PDF fact sheet |
| H-101 | Three-Bedroom Family House | 3 | 320,000 | Immediate | Gallery (2 illustrations) + own PDF fact sheet |
| H-104 | Four-Bedroom Premium House | 4 | 395,000 | 2027-03-01 | Gallery (2 illustrations) + own PDF fact sheet |

H-102 is reserved, not in the public catalog and not offered by the agent. All data and illustration/PDF assets are synthetic; the PDFs are fact sheets **not floor plans or construction drawings**.

## Canonical business data

- `shared/blackpenguin/project.json`: commercial sale-only scope and house-only publication categories.
- `shared/blackpenguin/property_types.json`: four house models (`CASA-2D`, `CASA-3D-COMPACT`, `CASA-3D`, `CASA-4D`), each associated with its own model ID.
- `shared/blackpenguin/units.json`: H-201, H-204, H-101, H-102 (reserved), H-104. H-201 and H-204 have newly generated **independent unit IDs**, not repurposed apartment IDs.
- `shared/blackpenguin/assets.json`: house gallery assets and PDF factsheets approved only for this synthetic simulation; each unit document has exact `unit_id` coverage. Project brochure remains at project scope.
- `scripts/build_catalog_from_blackpenguin.py`: publishes ONLY available, publishable property types in the project's active categories. It fails if the active demo deviates from four published houses.
- `shared/catalog.json` and `backend/app/catalog.json`: read-only generated projections, **not database tables**. Regenerate them after changing canonical assets.

## Agent behavior

- `backend/app/persistence.py`: new lead welcome text says exactly **"I'm the AI Sales Agent assigned to help you"**. No person name is assigned to the agent.
- `backend/app/hermes_runtime.py`: identity and scope stated in its Hermes `AIAgent` system prompt. No framework replacement or second LLM extractor; `run_conversation()` and the registered business tools remain unchanged.
- `backend/app/domain.py`: `evaluate_properties()` always matches against the approved catalog. It respects a previously verified lead's maximum budget even when a model-generated tool call contains a larger amount, respects a declared minimum bedroom count, and orders valid houses by closest bedroom count, then price.
- Budget examples: `House`, two bedrooms minimum, maximum USD 300,000 gives H-201 **first**, followed by H-204 as a three-bedroom alternative within budget; H-101 and H-104 are explicitly out of budget, not matches.
- If the lead wants an apartment or a rental, the agent explains the limitation; it must not invent a listing or silently reinterpret a property category.

## Deployment / existing data

No schema migration or Turso token change is needed. New code loads the new published JSON catalog. Existing Turso leads, transcript messages, factual observations, booked visits and historical references to A-201/A-204 are **not rewritten or deleted**. Those retired units are no longer part of the available inventory and cannot be booked as active units. The old synthetic static assets remain on disk only to allow historic explicitly shared file URLs; they're not listed in the active catalog.

The old `A-201`/`A-204` gallery **pages** intentionally return "no published gallery" because the current public catalog omits retired units. The historical transcript itself remains intact.

## Build and checks

```bash
python scripts/build_catalog_from_blackpenguin.py
python -m pip install reportlab  # only if regenerating the committed PDF assets
python scripts/generate_demo_assets.py
PYTHONPATH=backend python -m pytest -q backend/tests
cd frontend && npm install && npm run lint && npm run build
```

When deployed on Vercel, check the new deployment's runtime logs and run a fresh lead through an actual OpenRouter-backed conversation. Only local Python tests and TSX syntax checks are automated in the supplied patch; the production deployment and model behavioral evaluation are not claimed as completed.

**Security:** configure a strong distinct `DEMO_ADMIN_PASSWORD` and `DEMO_SIGNING_SECRET` on a public URL. With `DEMO_ADMIN_PASSWORD=demo`, anyone knowing the password can delete every lead. Use fictional leads only.
