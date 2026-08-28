# Design read — Vigory.ai

## Surface & archetype
Primarily **Application UI**: a repeated investigative task (pick a trigger
entity, scope a subgraph, inspect nodes) performed by a trained analyst under
real time pressure. The left rail (schema/filters) and right drawer
(inspector) also read as **dashboard/data-dense tooling**.

**Evidence**: aligned with `linear.app` and `superhuman` (stable nav, fast
state recognition, keyboard-first) and `airtable`/`sentry` (dense
scanability, visible filters, severity/status encoding). Explicit contrast
with `miro`: this is an investigation tool, not a collaborative whiteboard —
no sticky-note aesthetic, no playful brainstorm framing.

## Audience & task
Analysts scanning a scoped subgraph to answer "what's connected to this
trigger entity, and how confident are we." Frequent, low-error-tolerance,
sometimes time-pressured. Needs stable spatial layout (nodes should not jump
around while scanning) and legible confidence/status encoding.

## Single job per screen
- **Canvas**: make the scoped subgraph around a trigger entity scannable and
  trustworthy in seconds.
- **Inspector drawer**: show one entity/link's full record without leaving
  graph context.
- **Left rail**: pick a trigger, set hop depth, filter by link type, browse
  the class taxonomy.

## Design thesis

**Color**: neutral dark-surface canvas (`--surface-0/1/2`), one accent hue
per root entity class (Person, Organization, Location, Facility, Vehicle,
Equipment, Event, InformationObject, Identifier), confidence/status shown as
a solid coded chip (never a tint-on-tint badge).

**Typography**: Inter (UI chrome/labels) + JetBrains Mono (entity_id,
link_type, and other technical values) — functional pairing, not decorative.

**Layout**: three-pane app shell, one shared content rail/gutter:
left rail (schema browser + scope controls) — canvas (React Flow) — right
inspector drawer (slides in, doesn't cover canvas).

**Icons**: Lucide, one icon per root entity class, no emoji.

**Motion**: restrained — selection highlight, drawer slide, chip toggle.
No ambient motion, no hover-lift on nodes (border/glow only — nodes must
stay spatially stable while scanning).

**States**: empty (no trigger picked), loading (skeleton, not spinner-only),
error (inline retry), populated, and a distinct dashed/outline treatment for
"proposed/unconfirmed" LLM-extracted entities/links pending commit.

**Accessibility**: WCAG 2.2 AA. A keyboard-operable list/table view mirrors
the same scoped nodes+edges, since raw canvas drag/zoom is not reliably
keyboard-operable. Visible focus rings. Color never the sole encoder of
class or confidence — always paired with icon/label/chip text.

## Tokens (Tailwind v4 `@theme`, see `src/styles/tokens.css`)
- Surfaces: `--color-surface-0` (app bg), `-1` (panel), `-2` (raised/hover)
- Text: `--color-text-primary`, `-muted`, `-inverse`
- Border: `--color-border`, `--color-border-strong`
- Entity-class accents: one `--color-class-*` per root class
- Status: `--color-status-active/inactive/destroyed/unknown`
- Confidence scale: `--color-confidence-high/medium/low`
- Type scale: `--text-xs` … `--text-xl`, `--font-sans`, `--font-mono`
- Spacing/radius/shadow: Tailwind defaults, reused (no new scale)
- Motion: `--duration-fast` (120ms), `--duration-base` (200ms), standard easing

## Reused primitives
`components/ui/`: Button, Chip (status/confidence), Drawer (Radix Dialog),
Select (Radix), Stepper. `components/graph/`: EntityNode, RelationEdge.
`components/layout/`: AppShell (left rail / canvas / drawer).

## Per-state plan
- **Empty**: canvas shows a centered prompt + trigger-entity picker CTA.
- **Loading**: skeleton nodes/edges shape, not a spinner overlay.
- **Error**: inline banner with retry, canvas keeps last good state if any.
- **Populated**: nodes/edges render, list-view fallback available via tab.
- **Proposed/unconfirmed**: dashed border + outline fill, "proposed" chip.
