---
name: cueweaver-design
description: Apply CueWeaver's product design system when creating or changing its React shell, Translate flow, Jobs UI, Term maps UI, runtime states, navigation, or responsive behavior.
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

## Completion Check

Before finishing a UI change, verify all affected routes at desktop and mobile
widths, keyboard focus, provider unavailable behavior, loading/error states,
and every visible string.
