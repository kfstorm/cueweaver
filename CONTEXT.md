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
disable terminology mapping.
_Avoid_: glossary

**Directory default**:
The Term map associated with a Media directory for translations beneath it,
unless a Job explicitly selects or disables its Term map.

**Term map for this translation**:
The Term map policy selected for one Job: follow the Directory default, use a
specific Term map, or use none.

**Work directory**:
The explicit per-request directory used for PySubtrans translation state.
_Avoid_: job workspace

**Work root**:
The configured writable root owned by CueWeaver. It contains the observable
`jobs/` and `term-maps/` directories; each Job owns one `jobs/<job-id>/` Work
directory, including its assigned `translation/` directory.
_Avoid_: temporary root

**Job**:
A durable product task that will orchestrate optional Extraction and
Translation.
_Avoid_: request, task

**Job persistence**:
SQLite at `<work-root>/jobs.sqlite3` is the authoritative Job record store.
Legacy JSON records are imported during startup and retained as compatibility
snapshots during the storage migration. Queued Jobs are restored in queue
order; Jobs already in Extracting or Translating are marked Interrupted.
Publishing Jobs are reconciled from their durable output, and completed Jobs
with leftover Work data retain a cleanup_pending marker for retry on restart.
