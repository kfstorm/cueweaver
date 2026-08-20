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
- A supported translation provider and its credentials supplied through environment variables.

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

| Variable | Value | Required for |
| --- | --- | --- |
| `PROVIDER` | `DeepSeek` in the startup example | Translation |
| `DEEPSEEK_API_KEY` | Your DeepSeek API key | DeepSeek translation |
| `DEEPSEEK_API_BASE` | `https://api.deepseek.com` | Optional |
| `DEEPSEEK_MODEL` | `deepseek-chat` | Optional |
| `CUEWEAVER_MEDIA_ROOT` | Absolute path inside the container for the media library | Startup |
| `CUEWEAVER_WORK_ROOT` | Absolute, writable path inside the container for job data | Startup |

The selected media directory must be writable so CueWeaver can save translated subtitles. The Work volume must be writable and persistent so CueWeaver can keep job history and resumable work.

## Translation Provider Configuration

CueWeaver supports these providers in the built-in image:

| Provider | `PROVIDER` value | Credentials |
| --- | --- | --- |
| DeepSeek | `DeepSeek` | `DEEPSEEK_API_KEY` |
| OpenRouter | `OpenRouter` | `OPENROUTER_API_KEY` |
| OpenAI-compatible remote server | `Custom Server` | `CUSTOM_API_KEY` if required by the server |

Other provider integrations are not included in the built-in image.

Set `PROVIDER` and the matching provider variables before starting the container. Restart CueWeaver after changing them. Credentials cannot be entered in the Web interface.

If no provider is configured, CueWeaver still starts so you can browse media, manage term maps, and view job history. New translations remain unavailable until `PROVIDER` and the matching credentials are configured.

The provider status in the Web interface confirms that a provider has been selected; it does not verify the credentials. If translation fails, check the provider key and restart the container.

The defaults below match the provider integration bundled with CueWeaver.

### DeepSeek

The startup command above is a complete DeepSeek configuration. These optional settings let you adjust it:

| Variable | Default | Type | Purpose and notes |
| --- | --- | --- | --- |
| `DEEPSEEK_API_KEY` | None | string | Required. Create a key in the DeepSeek account that will pay for the requests. |
| `DEEPSEEK_API_BASE` | `https://api.deepseek.com` | string | Base URL for the DeepSeek API or a compatible DeepSeek deployment. |
| `DEEPSEEK_MODEL` | `deepseek-chat` | string | Model ID sent to the API. |
| `DEEPSEEK_MAX_TOKENS` | `8192` | integer | Maximum output tokens per request. |
| `DEEPSEEK_TEMPERATURE` | `1.3` | float | Sampling temperature. Lower values generally produce more consistent translations. |
| `DEEPSEEK_RATE_LIMIT` | None | float | Maximum API requests per minute. Leave unset unless the provider limits request frequency. |
| `DEEPSEEK_PROXY` | None | string | Optional HTTP proxy URL. |

You do not need to set an endpoint for DeepSeek.
CueWeaver explicitly disables thinking for the DeepSeek provider.

### OpenRouter

Set `PROVIDER=OpenRouter` and replace the DeepSeek credentials with these variables:

| Variable | Default | Type | Purpose and notes |
| --- | --- | --- | --- |
| `OPENROUTER_API_KEY` | None | string | Required. Create a key at [OpenRouter](https://openrouter.ai/keys). |
| `OPENROUTER_SERVER_ADDRESS` | `https://openrouter.ai/api/` | string | OpenRouter API base URL. |
| `OPENROUTER_MODEL` | `google/gemini-3-flash-preview` | string | OpenRouter model ID. |
| `OPENROUTER_MODEL_FAMILY` | `Google` | string | Optional model family filter. |
| `OPENROUTER_STREAM_RESPONSES` | `True` | boolean | Stream partial responses. Use `True` to enable streaming. |
| `OPENROUTER_MAX_TOKENS` | `0` | integer | Maximum output tokens. `0` means the provider default. |
| `OPENROUTER_TEMPERATURE` | `0.0` | float | Sampling temperature. |
| `OPENROUTER_RATE_LIMIT` | None | float | Maximum API requests per minute. |
| `OPENROUTER_PROXY` | None | string | Optional HTTP proxy URL. |

Use an OpenRouter model identifier for `OPENROUTER_MODEL`, not a display name from the OpenRouter Web site.

### Custom Server

`Custom Server` connects to a remote server that exposes an OpenAI-compatible API. Set `PROVIDER=Custom Server` and configure the following variables:

| Variable | Default | Type | Purpose and notes |
| --- | --- | --- | --- |
| `CUSTOM_SERVER_ADDRESS` | `http://localhost:1234` | string | Server base URL. Set this to the remote API address; the default points to the CueWeaver container itself and is usually not useful. |
| `CUSTOM_ENDPOINT` | `/v1/chat/completions` | string | Path used by the remote service. |
| `CUSTOM_SUPPORTS_CONVERSATION` | `True` | boolean | Send a chat-style request when true. Use a completion endpoint with `False`. |
| `CUSTOM_SUPPORTS_SYSTEM_MESSAGES` | `True` | boolean | Send instructions as system messages when true. Set false when the endpoint does not support them. |
| `CUSTOM_PROMPT_TEMPLATE` | Built-in (leave unset) | string | Advanced: change only if the remote service requires a different prompt format. |
| `CUSTOM_TEMPERATURE` | `0.0` | float | Sampling temperature. |
| `CUSTOM_MAX_TOKENS` | `0` | integer | Maximum output tokens. `0` disables this limit. |
| `CUSTOM_MAX_COMPLETION_TOKENS` | `0` | integer | Alternative token limit used by some OpenAI-compatible servers. `0` disables it. |
| `CUSTOM_TIMEOUT` | `300` | integer | Request timeout in seconds. |
| `CUSTOM_API_KEY` | None | string | Optional API key for the remote server. |
| `CUSTOM_MODEL` | None | string | Optional model ID. Some servers select the model on the server side. |
| `CUSTOM_SUPPORTS_PARALLEL_THREADS` | `False` | boolean | Allow parallel translation requests. Enable only when the remote server supports them. |
| `CUSTOM_REPETITION_PENALTY` | `0.0` | float | Repetition penalty for servers that support it. `0.0` disables it. |
| `CUSTOM_MIN_P` | `0.0` | float | Minimum-probability sampling threshold for servers that support it. `0.0` disables it. |

The remote server must be reachable from inside the container, not only from the host. Set the compatibility options to match the server's documented API.
CueWeaver also disables thinking when `CUSTOM_MODEL` starts with `deepseek-`, regardless of letter case.

Most users can leave the advanced settings at their defaults.

### Provider Troubleshooting

- If the CueWeaver Web interface says that the translation provider is unavailable, check that `PROVIDER` is exactly `DeepSeek`, `OpenRouter`, or `Custom Server`, that the required credentials are set, and then restart the container.
- If translation cannot start, check the spelling and capitalization of `PROVIDER` and the required API key.
- For a `Custom Server` 404, check `CUSTOM_SERVER_ADDRESS` and `CUSTOM_ENDPOINT`.
- For a `Custom Server` timeout or connection error, verify that the remote server accepts connections from the container and that its firewall allows the request.
- Set `CUSTOM_SUPPORTS_CONVERSATION=false` when the remote service uses a completion endpoint rather than a chat endpoint. Set `CUSTOM_SUPPORTS_SYSTEM_MESSAGES=false` when it does not support system messages.
- If a request is rejected for authentication, check the provider's API key and restart the container.
- Changing any provider environment variable requires a container restart. The Work volume can be kept across restarts; it contains job history and resumable translation state.

## Use CueWeaver

1. Put media and subtitle files in the mounted media directory.
2. Open **Translate** and browse to a media file.
3. Select an available subtitle. CueWeaver supports `.srt`, `.ass`, and `.vtt` files, plus text subtitles embedded in media containers.
4. Choose the target language and, if needed, select a saved term map.
5. Choose how to handle an existing output, then start the translation.
6. Follow progress and results from **Jobs**.

Translated subtitles use the media name, your chosen suffix, and the source subtitle format. Failed translations never replace an existing output.

## Security

CueWeaver has no authentication and is intended for trusted local use. The startup command binds the Web interface to the loopback address. Do not publish it to another network unless you put it behind an authenticated reverse proxy.
