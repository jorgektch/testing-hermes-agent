# V4 UI — Minimalist BlackPenguin Evaluation Workspace

BlackPenguin design tokens are retained without ornamental dashboard patterns:

- Dark background `#0A0A0A`; panels `#131313` and `#181818`.
- Amber `#E99E10` only for a primary action, active navigation, or a relevant interaction.
- Space Grotesk; compact readable 11–14 px information density, larger section headings.
- Thin neutral borders, modest radii, no glass blur/gradients as decorative layout.
- Only **Hermes agent** in the sidebar. No hero, numbered section headings or duplicate metrics.
- Desktop viewport: three independent scrollable areas — 34% inventory/calendar, 40% chat, 26% lead context. `min-height:0` Flex/Grid sizing keeps chat composer visible with long model responses.
- The calendar is an **interactive time grid**, not a list of time chips: 30-minute row ticks, 45-minute meeting events, 15-minute buffer, selected advisors in columns, real-time now line, day/week and local/project timezone display. The calendar can expand over the workspace without a route change.
- Chat uses the browser-independent persistence API, renders safe Markdown and links only to approved local demo assets.
- Property modal is data-dense for **fact verification**, not a marketing conversion panel. No 'Ask Ava' secondary action.
- Lead panel prioritizes contact/qualification/property interest/appointment; evidence and JSON details collapse.
- Login and 'New lead' dialogs use the same plain component treatment. Admin-only hard delete requires typing the exact fictional name.
- Responses, history, and bookings are **synthetic**. Other visitors' names are hidden in non-admin calendar views.
