## Summary

- Focus the fictional Hermes Agent Lab exclusively on selling new single-family houses.
- Replace the published apartment records with independent synthetic H-201 and H-204 house models, IDs, attributes and own sales assets; keep H-101, H-104 and reserved H-102.
- Update project sale scope, catalog generation, website inventory and Hermes tool prompt.
- Replace Ava's new lead greeting with: "I'm the AI Sales Agent assigned to help you".
- Apply the lead's verified budget cap during deterministic property matching, prioritizing closest bedroom count without ever classifying more expensive houses as in-budget.
- Preserve all historical Turso leads, messages and appointments; no schema migrations or secret changes.

## Verification

- 66 backend tests passed.
- Full binary patch applied cleanly to the v6 baseline and all 66 tests passed again.
- TSX syntax validated; a unit PDF was visually inspected.
- Remaining: run npm build and a real Hermes conversation on Vercel Preview.

## Security

Only fictional leads. Use a strong admin password and signing secret; do not expose `DEMO_ADMIN_PASSWORD=demo` to real clients.
