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
- A translation provider and its credentials available as environment variables. CueWeaver uses [PySubtrans](https://pypi.org/project/pysubtrans/) providers.

### Start CueWeaver

Run these commands from the project directory:

```bash
mkdir -p media
read -r -s -p "DeepSeek API key: " DEEPSEEK_API_KEY
printf '\n'
docker build -t cueweaver .
docker run --rm \
  --publish 127.0.0.1:8000:8000 \
  --env PROVIDER=DeepSeek \
  --env DEEPSEEK_API_KEY="$DEEPSEEK_API_KEY" \
  --env DEEPSEEK_API_BASE=https://api.deepseek.com \
  --env DEEPSEEK_MODEL=deepseek-chat \
  --env CUEWEAVER_MEDIA_ROOT=/media \
  --env CUEWEAVER_WORK_ROOT=/work \
  --volume "$PWD/media:/media" \
  --volume cueweaver-work:/work \
  cueweaver
```

Open [http://localhost:8000](http://localhost:8000) in your browser.

The `media` directory is the library shown in CueWeaver. Replace it with an existing directory if your media is stored elsewhere. Keep the `cueweaver-work` volume: it contains job history and in-progress translation state.

## Configuration

| Variable | Value | Required |
| --- | --- | --- |
| `PROVIDER` | `DeepSeek` in the startup example | Yes |
| `DEEPSEEK_API_KEY` | Your DeepSeek API key | Yes for DeepSeek |
| `DEEPSEEK_API_BASE` | `https://api.deepseek.com` | No |
| `DEEPSEEK_MODEL` | `deepseek-chat` | No |
| `CUEWEAVER_MEDIA_ROOT` | Absolute path inside the container for the media library | Yes |
| `CUEWEAVER_WORK_ROOT` | Absolute, writable path inside the container for job data | Yes |

The selected media directory must be writable when CueWeaver publishes a translated subtitle beside the source media. The Work volume must support reading, writing, directory creation, and atomic file replacement.

For another PySubtrans provider, set `PROVIDER` to that provider's name and pass the provider-specific environment variables documented by PySubtrans. CueWeaver reads these variables when it starts; changing them requires a restart.

For the `DeepSeek` provider, or for `Custom Server` when the configured model starts with `deepseek-`, CueWeaver explicitly sends `thinking: {"type": "disabled"}` so DeepSeek V4 Flash uses non-thinking mode.

## Use CueWeaver

1. Put media and subtitle files in the mounted media directory.
2. Open **Translate** and browse to a media file.
3. Select an available subtitle. CueWeaver supports `.srt`, `.ass`, and `.vtt` files, plus text subtitles embedded in media containers.
4. Choose the target language and, if needed, select a saved term map.
5. Choose how to handle an existing output, then start the translation.
6. Follow progress and results from **Jobs**.

Translated subtitles use the media name, your chosen suffix, and the source subtitle format. Failed translations never replace an existing output.

## Translation Provider

CueWeaver reads provider configuration from the container's environment. If no provider is configured, the application still starts and you can browse media, manage term maps, and view job history, but new translations remain unavailable.

CueWeaver does not load PySubtrans's saved desktop service settings, and the Docker image does not include the PySubtrans GUI or a settings volume. Provider credentials are not entered into the CueWeaver Web interface.

## Security

CueWeaver has no authentication and is intended for trusted local use. The startup command binds the Web interface to the loopback address. Do not publish it to another network unless you put it behind an authenticated reverse proxy.
