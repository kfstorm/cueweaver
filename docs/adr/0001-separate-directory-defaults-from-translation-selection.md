# Separate Directory Defaults from Translation Selection

CueWeaver keeps a reusable Directory default separate from the Term map policy
for an individual translation. The Translate flow must expose both concepts:
the Directory default can be inherited, while a Job can explicitly select a
specific Term map or disable terminology mapping. This preserves reusable
directory configuration without removing per-Job control, and the UI must name
and explain the two scopes distinctly.

## Considered Options

- Hide the Directory default from Translate and keep only per-Job selection.
- Always follow the Directory default and remove per-Job override choices.
- Keep both scopes with explicit inheritance, selection, and disable states.

The third option was accepted because the first two discard existing control
over directory-wide behavior or individual translation behavior.
