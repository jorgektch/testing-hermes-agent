# BlackPenguin design system adaptation for the Hermes lab

Reference: user-provided **BlackPenguin_Sistema_de_Diseno_Actual.md**, describing the actual Angular frontend implementation at repository commit `0ab19e793540737626c259d8a20883330fa65143` (20 November 2026).

This is **a style adaptation**, not an Angular-to-Next code copy. The Hermes demo remains Next.js/React for the previously chosen Vercel monorepo.

## Tokens

| Purpose | CSS variable | Value from documented current implementation |
|---|---|---|
| Global background | `--background` | `#0A0A0A` |
| Surface dim | `--surface-dim` | `#131313` |
| Raised surface | `--surface-level-1` | `#161616` |
| Alternate surface | `--surface-variant` | `#353535` |
| Main text | `--on-surface` | `#E4E2E1` |
| Secondary text | `--gray-custom` | `#999999` |
| Amber accent (official `secondary`) | `--secondary` | `#E99E10` |
| Accent contrast | `--on-secondary` | `#452B00` |
| Brighter amber | `--secondary-fixed-dim` | `#FFB94E` |
| Subtle border | `--border-level-2` | `#2E2E2E` |
| Contrast outline | `--outline-variant` | `#474746` |

Use **Space Grotesk 300–700** and Material Symbols Outlined. Body text is ~14px; section eyebrows are 10px uppercase with generous letter spacing, matching the existing Agent/Leads conventions.

## Composition

- A 16rem sidebar at wide desktop sizes (compact icon rail on smaller desktop, hidden on phones), referencing the original BlackPenguin shell.
- Inventory/chat/context are visible side-by-side on wide desktop, inspired by the **Agent** workspace and the **Leads** detail sidebar.
- Inventory is a scrollable card grid with restrained black/amber project hero, neutral unit cards and amber status/selection.
- AI messages use the dark gold `#201800` / `#614900` treatment observed in the existing Agent workspace; inbound messages use charcoal `#1A1A1A`.
- Appointment offers use amber cards and reservation confirmations use a green variant. No success badge claims external calendar synchronization.
- Detail modal is black glass, 1rem radius with subdued border, mobile responsiveness and accessible focus styling.
- Reduced motion preferences disable transitions/animations.

The source document notes that `secondary` (amber) is the primary *brand accent*, while `primary` is grey. The demo intentionally follows that naming and **does not substitute teal or blue as the principal accent**.

## Differences

- Components are authored in React/Next.js; the source UI uses Angular 21, Tailwind 3 and SCSS.
- Cards and styling are CSS utilities/classes rather than direct imports of Angular `app-button`, `app-input`, `app-glass-card`, `app-modal`.
- The responsive sidebar reduces to an icon rail instead of preserving the complete Angular layout on small screens.
- Lucide icons still appear inside several property/chat controls, while the nav uses the specified Material Symbols Outlined. Both are visually outlined; this is not an exact icon-for-icon reproduction.
- Source documentation explicitly noted gaps in focus-visible policy and responsive consistency. This demo adds a uniform focus-visible outline and reduced-motion styling.
