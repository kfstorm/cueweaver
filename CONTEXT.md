# CueWeaver

CueWeaver is a locally deployed Web subtitle product. Its official
single-worker ASGI server hosts the product shell and constrained product API.
Media and working directories are mounted into the server container. The Web
workflow is the supported public interface; dependency-injection and
application-operation seams remain available for embedding and tests.

## Language

**Media**:
The video file named by a discovery or extraction request.
_Avoid_: movie, file, video

**External subtitle**:
A same-stem SRT, ASS, or VTT subtitle file alongside the Media.
_Avoid_: sidecar, subtitle file

**Embedded subtitle**:
A text subtitle stream inside the Media's container, identified by its ffprobe
stream index.
_Avoid_: track

**Discovery**:
Enumerating usable External and text Embedded subtitles, plus unsupported
subtitle candidates, without selecting one.
_Avoid_: scan

**Extraction**:
Writing a selected Embedded text subtitle stream losslessly to an explicit path.
_Avoid_: demux

**Term map**:
An explicit, reusable JSON object mapping non-empty source terms to non-empty
target terms. A Job can select one, follow a Directory default, or explicitly
disable terminology mapping. Each Job uses at most one complete Term map;
directory inheritance selects one map rather than merging maps. A map can
include character names, places, titles, and other terms for the same work.
_Avoid_: glossary

**Directory default**:
The Term map associated with a Media directory for translations beneath it,
unless a Job explicitly selects or disables its Term map.

**Directory rule**:
An explicit Directory default managed in Settings under Term maps → Automatic
use. A subdirectory's rule takes priority over its ancestors' rules.

**Term map for this translation**:
The Term map policy selected for one Job: follow the Directory default, use a
specific Term map, or use none.

**Work directory**:
The explicit per-request directory used for PySubtrans translation state.
_Avoid_: job workspace

**Work root**:
The configured writable root owned by CueWeaver. It contains the SQLite
database and the observable `jobs/` directory; each Job owns one
`jobs/<job-id>/` Work directory, including its assigned `translation/`
directory.
_Avoid_: temporary root

**Job**:
A durable product task that will orchestrate optional Extraction and
Translation.
_Avoid_: request, task

**Model Profile**:
An independently named PySubtrans provider configuration. A Job stores its
selected Model Profile ID and resolves the current provider and settings when
each translation attempt starts.

The Model Profile editor obtains provider names from the installed PySubtrans
registry and loads settings by running the selected provider's option discovery.

**Job persistence**:
The application composition owns the SQLite database at
`<work-root>/cueweaver.sqlite3` and the Work-root lease at
`<work-root>/.cueweaver.lease`. SQLAlchemy ORM models and versioned Alembic
migrations define a relational schema. Jobs use scalar request and lifecycle
columns with relational status history, a required Model Profile reference,
and immutable Term map snapshot entries. Model Profiles contain a provider and
non-null JSON setting values.
Term maps use metadata and ordered relational entries; directory bindings remain
relational records. Queued Jobs are
restored in queue order; Jobs already in Extracting or Translating are marked
Interrupted. Subtitle output is published through a same-directory temporary
file and atomic rename/link. A completed Job is persisted before best-effort
Work-directory cleanup; cleanup failure leaves the Job Completed and logs an
orphaned Work directory for later removal. Job Work directories and
PySubtrans checkpoint files remain filesystem state rather than ORM data.
