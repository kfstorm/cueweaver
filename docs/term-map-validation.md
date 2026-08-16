# Term map validation

Term map content has one semantic validation contract in
`cueweaver.application.term_maps`:

- The value is a non-empty JSON object.
- Every source key and target value is a non-empty string.
- Source keys are unique after Unicode `casefold()`.
- The compact JSON representation, encoded as UTF-8 with `ensure_ascii=False`,
  is at most 1 MiB (`MAX_TERM_MAP_BYTES`).

The HTTP adapter also applies raw-byte limits before semantic validation:

- The nested uploaded `content` JSON cannot exceed 1 MiB
  (`MAX_TERM_MAP_UPLOAD_BYTES`).
- The complete request body cannot exceed 2 MiB
  (`MAX_TERM_MAP_REQUEST_BYTES`).

Raw limits protect request parsing and preserve the upload contract. The
canonical limit protects the stored and translated Term map value. The shared
vectors in `contracts/term-map-validation.json` cover both layers and are used
by Python and TypeScript tests. The Web layer provides best-effort
pre-validation with platform-native behavior; the backend remains authoritative
for full Python `str.casefold()` semantics.
