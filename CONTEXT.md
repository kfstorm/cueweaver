# CueWeaver

A self-hosted media subtitle translation tool: the user picks a video, the system discovers candidate subtitles, a Source is picked (or chosen by the user when ambiguous), translated to a Target language, validated, and atomically published next to the Media. Zero-configuration is the default UX.

## Language

**Media**:
The video file the user selected as the subject of a translation Job.
_Avoid_: movie, file, video

**External subtitle**:
A subtitle file (SRT/ASS/VTT) stored alongside the Media.
_Avoid_: sidecar, subtitle file

**Embedded subtitle**:
A subtitle track inside the Media's container (MKV/MP4); may require Extraction before use.
_Avoid_: track, stream

**Bitmap subtitle**:
An Embedded subtitle that exists as images (PGS/VobSub); requires Subtitle OCR to become text.
_Avoid_: image subtitle

**Discovery**:
Enumerating a Media's candidate subtitles without reading the full video and without needless Extraction.
_Avoid_: scan

**Source**:
The single subtitle chosen from the candidates as input to translation.
_Avoid_: input, source subtitle

**Extraction**:
Materializing an Embedded subtitle track into a processable file.
_Avoid_: demux

**Subtitle OCR**:
Converting a Bitmap subtitle into text.
_Avoid_: OCR (unqualified)

**Target language**:
The language a Job translates into. A required global setting, user-configurable, with no fixed default; a Job cannot start without it being set.
_Avoid_: output language, destination

**Term**:
A fixed mapping (character, place, organization names, etc.) with provenance and confidence.
_Avoid_: glossary item

**Glossary**:
The set of Terms; User override always takes precedence.
_Avoid_: terminology map, context glossary

**User override**:
A translation mapping specified manually by the user, always taking precedence over the automatic Glossary.
_Avoid_: user correction, user glossary

**Validation**:
Automatic checks on a translation result before Publishing.
_Avoid_: review

**Publishing**:
Atomically writing a validated translation result next to the Media (e.g. `Movie.zh.srt`).
_Avoid_: export, write

**Job**:
A work unit that translates one Media to a Target language, with its own work directory.
_Avoid_: task, translation run

**Context**:
Narrative/narrative-like metadata (e.g. plot synopsis) injected to aid translation quality; not a Glossary.
_Avoid_: background, reference material
