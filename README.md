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

Open **Settings → Model Profiles**, create a profile, and choose a provider. CueWeaver loads that provider's settings from PySubtrans. Enter the credentials and model settings you need, save the profile, then select it on **Translate** before creating a Job.

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

Create Model Profiles in **Settings → Model Profiles**. Each profile is a standalone configuration with a name, a provider from the installed PySubtrans registry, and settings for that provider. CueWeaver starts without a profile, and new translations require one.

The editor calls the selected provider's PySubtrans runtime APIs to load the available fields, descriptions, types, and choices. Providers can refresh these options after fields such as an API key or model change, including through network-backed model discovery. For choice settings, the editor offers the provider's current choices. A saved value remains available in the profile if it no longer appears in that list.

When a profile has no explicit setting, PySubtrans determines the effective value from its runtime default or environment fallback. CueWeaver does not show that value in the form. It saves explicit profile settings and runtime normalization, such as an automatically selected model. Clear a value or choose **Use default** to remove that setting from the profile and return control to PySubtrans.

Model Profile values, including provider API keys, are stored in SQLite as ordinary values and are readable through the UI and API. CueWeaver does not mask or encrypt them as secrets. ChatGPT OAuth credentials are stored separately and are never profile settings. Limit access to the deployment accordingly.

For its Model Profile configuration, a Job stores only `model_profile_id`. CueWeaver reads the current standalone profile once at the start of each attempt and uses those settings throughout that attempt. Editing a profile affects queued Jobs and future retries, including retries of an existing PySubtrans project, but not an active attempt. A profile referenced by a Job cannot be deleted.

PySubtrans may still read its own environment defaults and fallbacks. Model Profiles are the recommended way to configure CueWeaver; mixing both approaches makes the effective configuration harder to understand.

### Translate with a ChatGPT subscription

CueWeaver uses PySubtrans 1.7.4's official Sign in with ChatGPT integration.
Eligible accounts can use their ChatGPT plan allowance without a separately
billed OpenAI API key. API key authentication remains the default. To use your
subscription, authenticate first, then create an **OpenAI** Model Profile, set
`use_chatgpt_plan` to **Enabled**, and choose a model from the refreshed list.
Select that profile when creating a Job. Set the option to **Disabled** to return
to API key mode and enter your API key again.

Authentication is an offline administrator operation. Stop CueWeaver before
running the management commands; they use the same exclusive Work-root lease
as the server. There is one shared ChatGPT account per deployment. Anyone who
can use the deployment can submit Jobs against that account's allowance.

#### Local browser authentication

On the computer running your browser, install this checkout's dependencies and
sign in using a dedicated bootstrap Work root:

```bash
uv sync
uv run cueweaver-chatgpt \
  --work-root "$HOME/.local/share/cueweaver-chatgpt-bootstrap" sign-in
```

The command opens your browser and waits up to five minutes. Allow the browser
to reach the local loopback callback. Running this command inside Docker won't
reach a browser on another machine. For a source deployment on the browser's
machine, you can instead specify the server's stopped Work root directly.

#### Docker or remote server bootstrap

Follow OpenAI's [self-hosted VM procedure](https://developers.openai.com/siwc/token-sharing-open-source/self-hosted-vms).
Use the same CueWeaver version, ChatGPT account, and workspace on both machines.
First, stop the server and initialize its own persistent host identity:

```bash
docker run --rm --env CUEWEAVER_WORK_ROOT=/work \
  --volume cueweaver-work:/work cueweaver cueweaver-chatgpt init
```

Complete local browser authentication as above. Transfer its protected credential
file over SSH to the server, replacing `server.example.com` with your SSH host:

```bash
scp "$HOME/.local/share/cueweaver-chatgpt-bootstrap/pysubtrans/chatgpt-auth.json" \
  server.example.com:cueweaver-chatgpt-transfer.json
```

On the server, import the session into the stopped deployment:

```bash
chmod 600 "$HOME/cueweaver-chatgpt-transfer.json"
docker run --rm --env CUEWEAVER_WORK_ROOT=/work \
  --volume cueweaver-work:/work \
  --volume "$HOME/cueweaver-chatgpt-transfer.json:/run/chatgpt-transfer.json:ro" \
  cueweaver cueweaver-chatgpt import /run/chatgpt-transfer.json
rm "$HOME/cueweaver-chatgpt-transfer.json"
```

Use the same Work volume and container user as your normal deployment. The
import preserves the server's host ID and transfers the issued client ID and
tokens. Restart CueWeaver using the startup command above. Delete the bootstrap
credential file on the browser computer and let the server own later token
refreshes. Don't run `sign-out` on the bootstrap copy: it would revoke the
transferred session too.

#### Persistence, status, and sign-out

Open **Settings → ChatGPT** for setup guidance and a read-only credential status.
You can refresh this status while CueWeaver runs. **Credentials present** means
credentials are stored locally; it doesn't verify remote authorization, account
eligibility, or remaining allowance. An unreadable file appears as unavailable;
refresh again before asking the administrator to check it. The Web UI cannot
sign in, refresh tokens, import credentials, switch accounts, or sign out.

The Work volume contains `pysubtrans/chatgpt-auth.json`, with owner-only file
permissions (`0600`) inside an owner-only directory (`0700`). It holds the stable
host ID, client registration, and OAuth credentials. Keep the volume across
container recreation. Treat its backups as secrets; the credentials are not
encrypted. They are separate from SQLite, Model Profiles, and Job checkpoints.

While CueWeaver is stopped, check whether credentials are present or sign out:

```bash
docker run --rm --env CUEWEAVER_WORK_ROOT=/work \
  --volume cueweaver-work:/work cueweaver cueweaver-chatgpt status
docker run --rm --env CUEWEAVER_WORK_ROOT=/work \
  --volume cueweaver-work:/work cueweaver cueweaver-chatgpt sign-out
```

Status checks local credential presence, not remote validity. Sign-out revokes
the refresh token and removes local tokens while keeping the host identity and
registration. If revocation cannot be confirmed, revoke CueWeaver access in
ChatGPT settings. If credentials are damaged, stop CueWeaver, revoke access in
ChatGPT, and restore a protected backup or remove the credential file before
bootstrapping again. Removing the whole file also resets the host ID.

Access tokens refresh automatically when a profile is loaded or an attempt
starts. If the session can no longer refresh, authenticate again; Jobs don't
fall back to separately billed API keys. After reconnecting, reload the Model
Profile editor to discover available models and retry the failed Job.
PySubtrans holds one access token per translation client, so an attempt that
outlasts that token may need a retry to refresh it and resume its checkpoint.

#### Plan limits

This integration is an OpenAI preview and uses your plan's shared allowance;
it does not provide unlimited usage. Available models and access depend on
your account and workspace. Manage limits in [ChatGPT usage settings](https://chatgpt.com/settings/usage).
PySubtrans uses the public Responses API with streaming enabled and response
storage disabled. Audio/video input, transcription, and the Files upload API
are not covered. See OpenAI's [preview restrictions](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations).
Transferred sessions currently lack host-specific usage attribution and
revocation of plan access, as described in the VM guidance.

### PySubtrans upgrade notes

The pinned PyPI release is 1.7.4, the first release with ChatGPT plan support.
CueWeaver keeps strict subtitle validation by treating upstream warnings as
errors. Resume and progress use upstream's error-aware completion checks, so
batches with saved validation errors are retried. Provider discovery includes
the upstream gateway additions. The Mistral SDK remains constrained to v1.

PySubtrans now uses `thinking_level` for Gemini instead of `thinking_budget`
and omits Gemini temperature. If you have an existing Gemini profile, review
and save it in the editor to remove obsolete settings. PySubtrans configuration
is isolated under the Work root; files in its former user-config directory
are not imported.

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
