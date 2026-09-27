# CueWeaver

Translate subtitles from a local media library and keep every translation in a durable job history.

## Features

- **Translate local media:** Choose subtitle files next to your media or text subtitles embedded in the media itself.
- **Keep terminology consistent:** Reuse named term maps across translations, or import them from JSON.
- **Protect your files:** Choose a numbered output when a translation already exists, or explicitly replace it after a successful translation.
- **Track the work:** See queued, active, completed, failed, and interrupted translations in one place.
- **Resume safely:** Keep the Work volume across restarts so job history and recoverable work are not lost.

## Getting Started

### Requirements

- Docker with permission to build and run containers.
- A media directory that the container can read and write when it publishes translated subtitles.
- A supported translation provider and its credentials, configured in Settings → Model Profiles after startup.

### Start CueWeaver

Run these commands from the project directory:

```bash
mkdir -p media
docker build -t cueweaver .
docker run --rm \
  --publish 127.0.0.1:8000:8000 \
  --env CUEWEAVER_MEDIA_ROOT=/media \
  --env CUEWEAVER_WORK_ROOT=/work \
  --volume "$PWD/media:/media" \
  --volume cueweaver-work:/work \
  cueweaver
```

Open [http://localhost:8000](http://localhost:8000) in your browser.

Open **Settings → Model Profiles**, create a selectable profile, and add literal settings such as `provider` = `DeepSeek`, `model` = `deepseek-chat`, and `api_key` = your key. Select that profile on **Translate** before creating a Job.

The `media` directory is the library shown in CueWeaver. Replace it with an existing directory if your media is stored elsewhere. Keep the `cueweaver-work` volume: it contains job history and in-progress translation state.

## Configuration

| Variable | Value | Required for |
| --- | --- | --- |
| `CUEWEAVER_MEDIA_ROOT` | Absolute path inside the container for the media library | Startup |
| `CUEWEAVER_WORK_ROOT` | Absolute, writable path inside the container for job data | Startup |

The selected media directory must be writable so CueWeaver can save translated subtitles. The Work volume must be writable and persistent so CueWeaver can keep job history and resumable work.

Application data is stored in the SQLite database at `cueweaver.sqlite3` in the
Work root. The database contains relational Job lifecycle/request fields,
status history, immutable Job Term map snapshots, Model Profiles and their settings,
Term map metadata and ordered entries, and directory bindings. Schema upgrades run automatically through the
versioned migrations shipped with CueWeaver. The Work root also contains
`.cueweaver.lease`, which prevents
multiple CueWeaver processes from using the same Work root. Do not remove the
Work volume while Jobs are active.

After an unclean process stop, the next startup restores `Queued` Jobs in
queue order and marks Jobs that were in `Extracting` or `Translating` as
`Interrupted`. A subtitle is written to a same-directory temporary file before
it is atomically published, so readers never observe a partial output. A
completed Job is persisted before its Work directory is removed; cleanup
failure leaves the Job `Completed` and logs the leftover directory. The
operating system releases the lease after a process crash; do not delete
`.cueweaver.lease` manually. If SQLite cannot be opened, CueWeaver refuses startup;
preserve the entire Work volume before restoring or inspecting a backup.

## Model Profiles

Create Model Profiles in **Settings → Model Profiles**. A selectable profile can be chosen for a new Job and must resolve to a `provider` from the local PySubtrans provider registry; `model` is optional. A non-selectable profile may be incomplete and used as a base for inheritance. CueWeaver starts without a selectable profile, but new translations require one.

Use **Create derived** to inherit from one parent. Settings merge by top-level key: a local literal overrides the inherited value, including a nested JSON object as a whole. **Unset** removes a key from the effective profile without suppressing PySubtrans defaults or environment fallbacks. **Remove local** removes a local override or unset and restores inheritance.

Literal values retain their types: string, integer, number, boolean, string list, or nested JSON. For example, a non-selectable profile can define `provider` = `OpenRouter` and `api_key` = your key, while a selectable child defines `model` = `deepseek/deepseek-v3.2` and `max_threads` = `2` (integer). CueWeaver owns `target_language`, `prompt`, subtitle processing, terminology, error behavior, and project checkpoint settings; these cannot be profile entries.

Model Profile values, including API keys and tokens, are stored in SQLite as ordinary values and are readable through the UI and API. CueWeaver does not mask or encrypt them as secrets. Limit access to the deployment accordingly.

For its Model Profile configuration, a Job stores only `model_profile_id`. CueWeaver resolves the current profile once at the start of each attempt and uses those settings throughout that attempt. Editing a profile or ancestor affects queued Jobs and future retries, including retries of an existing PySubtrans project, but not an active attempt. Making a profile non-selectable prevents new Jobs from selecting it without blocking existing Jobs or retries. Profiles referenced by a Job or child cannot be deleted.

PySubtrans may still read its own environment defaults and fallbacks. Model Profiles are the recommended way to configure CueWeaver; mixing both approaches makes the effective configuration harder to understand.

## Use CueWeaver

1. Put media and subtitle files in the mounted media directory.
2. Open **Translate** and browse to a media file.
3. Select an available subtitle. CueWeaver supports `.srt`, `.ass`, and `.vtt` files, plus text subtitles embedded in media containers.
4. Select a Model Profile, choose the target language and, if needed, select a saved Term map.
5. Choose how to handle an existing output, then start the translation.
6. Follow progress and results from **Jobs**.

Translated subtitles use the media name, your chosen suffix, and the source subtitle format. Failed translations never replace an existing output.

## Security

CueWeaver has no authentication and is intended for trusted local use. The startup command binds the Web interface to the loopback address. Do not publish it to another network unless you put it behind an authenticated reverse proxy.
