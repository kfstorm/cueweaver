# CueWeaver

CueWeaver is a library for a locally deployed HTTP subtitle service. An
embedding ASGI server exposes discovery, extraction, and translation over
explicit container-local paths.

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
An explicit JSON object mapping non-empty source terms to non-empty target
terms for one translation request.
_Avoid_: glossary

**Work directory**:
The explicit per-request directory used for PySubtrans translation state.
_Avoid_: job workspace
