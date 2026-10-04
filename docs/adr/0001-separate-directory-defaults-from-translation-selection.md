# Separate Directory Defaults from Translation Selection

CueWeaver manages reusable Directory defaults in Settings under Term maps →
Automatic use. Translate exposes only the Term map choice for the current
translation: automatic selection, one specific map, or no map.

Automatic selection shows the resolved map name without an inheritance path.
It resolves each Media item's actual directory, including in batch mode. Source
directories are available in the settings editor when needed. Directory rules
are listed explicitly; inherited child directories do not create extra rows.
Batch translation retains its same-directory constraint and selects one map
for the batch. Discovery reports the resolved directory so file links use the
same default in the preview and in the created Job.

Each Job uses at most one complete map. A more specific Directory rule replaces
the ancestor's choice rather than merging its content. Jobs already created
retain their captured Term map content. Translate drafts survive navigation to
Settings without writing Media paths to browser storage.

## Considered Options

- Manage Directory defaults in Settings and show the resolved automatic choice
  in Translate.
- Always follow the Directory default and remove per-Job override choices.
- Keep editable directory and per-Job settings together in Translate.

The first option is accepted. Putting long-lived directory edits beside a
single translation made the scope of changes unclear. Settings keeps directory
control available while Translate focuses on the result for the current Job.
