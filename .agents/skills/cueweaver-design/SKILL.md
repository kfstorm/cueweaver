---
name: cueweaver-design
description: Apply CueWeaver's product design system whenever creating or changing its React shell, Translate flow, Jobs UI, Term maps UI, runtime states, navigation, typography, spacing, or responsive behavior. Treat the rules below as an implementation contract, not optional visual advice.
---

# CueWeaver Design

Build CueWeaver as a compact local workflow product. Preserve the visual and
interaction rules below across every product route.

## Visual Language

- Use a light, cool-neutral canvas with one indigo accent. Color communicates
  selection, focus, and runtime state rather than decoration.
- Prefer dividers, spacing, and typography to card grids. Add elevation only
  for controls that must sit above workflow content.
- Keep controls compact: 36px desktop control height, 44px minimum touch
  targets on mobile, 6-10px radii, and no decorative gradients or glows.
- Use sentence case and concise English copy. Call domain concepts Media,
  External subtitle, Embedded subtitle, Job, Term map, and Work directory.

## Density Contract

Use the existing CSS design tokens as the only source for typography and
primary layout spacing. If a needed token does not exist, add it at the root
token list before using it; do not introduce a one-off pixel value at a page
layout boundary. Small internal icon, badge, or text-group gaps may remain
component-local when they do not define page rhythm; promote them to a token
when reused.

- Body and control text use 14px; mobile text inputs use 16px.
- Field labels use 13px, help and validation text use 12px, and metadata uses
  11px. Functional text never uses 9px or 10px.
- Headings use the established scale: page title 22px, section title 15-16px,
  detail title 18px, and subsection title 13-14px.
- Use the 4px spacing rhythm for page layout: 4px, 8px, 12px, 16px, 24px,
  and 32px. Labels sit 8px from controls, help text sits 6px from its control,
  fields are separated by 16px, and sections are separated by 24px.
- Keep line-height explicit for every custom text size. Do not use a negative
  margin to pull readable text into a control or to compensate for an
  inconsistent field layout.
- Do not reserve a large fixed height for a conditionally absent loading,
  empty, or error child. Stable state footprints must be intentional and
  visible, not blank space left by a hidden state.

## Component Boundaries

- Button, Input, Textarea, and Select primitives own their font size, line
  height, control height, focus ring, and touch target. Page CSS may control
  their width and layout, but must not override their typography.
- Give native selects the same primitive treatment as inputs; never let their
  font size depend on the parent label's inherited size.
- Keep one authoritative selector for each shared class. Merge duplicate rules
  instead of relying on later CSS to override earlier declarations.
- A smaller type size is allowed only for non-functional decoration such as a
  brand mark or step number, and the exception must remain local and explicit.

## Product States

- Design loading, empty, error, disabled, and ready states with the same layout
  footprint so status changes do not shift the workflow.
- Show coarse Job states only. Never invent percentages or imply provider
  progress that CueWeaver does not know.
- Provider unavailability must preserve browsing and management actions while
  disabling Translation submission with a nearby actionable message.
- Keep errors friendly and expose approved structured context on demand. Never
  render tracebacks, credentials, provider details, or absolute roots.

## Accessibility

- Use shadcn/ui or Radix-backed controls with visible labels and semantic HTML.
- Maintain WCAG AA contrast and a visible two-pixel focus ring. Do not rely on
  color alone for selection, errors, or readiness.
- Announce asynchronous state changes with `status` or `alert` semantics.
- Preserve logical focus order and full keyboard operation at every viewport.
- Honor `prefers-reduced-motion`; motion is limited to direct interaction and
  state feedback.

## Responsive Behavior

- Desktop uses a persistent left navigation and a bounded workflow canvas.
- Below 768px, replace the sidebar with a safe-area-aware bottom navigation.
- Mobile supports every workflow rather than hiding advanced or management
  actions. Stack action bars and keep primary actions full-width when needed.
- Avoid horizontal scrolling for page structure. Long filenames and technical
  values wrap or truncate with an accessible full-value affordance.

## Visual Verification

For every UI change, verify the rendered result rather than relying on source
inspection alone.

- Use the repository's `agent-browser` skill and Agent Browser tool as the only
  browser inspection path. Before running a browser command, load its current
  workflow with `agent-browser skills get core`.
- Check 1280x800 and 390x844 viewports with Agent Browser.
- Check Translate, Jobs, and Term maps, including at least one populated and
  one empty/loading/error state affected by the change.
- Inspect computed font size, line height, control height, gap, and margins for
  the changed controls. Same-kind controls must not silently diverge.
- Confirm there is no horizontal overflow, fixed-navigation overlap, clipped
  focus ring, or unexplained blank vertical region.
- Capture screenshots for meaningful layout changes and treat unexpected
  screenshot differences as a failure to investigate, not as noise.

Do not use Playwright, Puppeteer, browser DevTools, generic browser automation,
or HTTP/source inspection to inspect webpage content, rendered layout, browser
state, screenshots, or computed styles. Existing automated E2E suites may still
be run as regression checks, but their DOM assertions never replace Agent
Browser inspection.

## Completion Check

Before finishing a UI change, verify all affected routes at desktop and mobile
widths, keyboard focus, provider unavailable behavior, loading/error states,
and every visible string. The change is incomplete if the density contract or
visual verification checklist has not been checked.
