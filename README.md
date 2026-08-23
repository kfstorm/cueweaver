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

Job history is stored in SQLite at `jobs.sqlite3` in the Work root. Existing
JSON Job records are imported once automatically when the application starts,
then their snapshots are retired. The Work root also contains `.jobs.lease`,
which prevents multiple CueWeaver processes from using the same Work root.
Do not remove the Work volume while Jobs are active.

After an unclean process stop, the next startup marks Jobs that were in
`Extracting` or `Translating` as `Interrupted` and reconciles `Publishing`
Jobs against their durable output. The operating system releases the lease
after a process crash; do not delete `.jobs.lease` manually. If SQLite cannot
be opened, CueWeaver refuses startup; preserve the entire Work volume before
restoring or inspecting a backup.

## Translation Provider Configuration

CueWeaver supports these providers in the built-in image:

| Provider | `PROVIDER` value | Credentials |
| --- | --- | --- |
| DeepSeek | `DeepSeek` | `DEEPSEEK_API_KEY` |
| OpenRouter | `OpenRouter` | `OPENROUTER_API_KEY` |
| OpenAI-compatible remote server | `Custom Server` | `CUSTOM_SERVER_ADDRESS` or the built-in localhost default; `CUSTOM_API_KEY` if required |
| OpenAI | `OpenAI` | `OPENAI_API_KEY` |
| Azure OpenAI | `Azure` | `AZURE_API_KEY`, `AZURE_API_BASE`, `AZURE_API_VERSION`, `AZURE_DEPLOYMENT_NAME` |
| Google Gemini | `Gemini` | `GEMINI_API_KEY` |
| Anthropic Claude | `Claude` | `CLAUDE_API_KEY` |
| Mistral | `Mistral` | `MISTRAL_API_KEY` |
| Amazon Bedrock | `Bedrock` | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, `BEDROCK_MODEL` |

Set `PROVIDER` and the matching provider variables before starting the container. Restart CueWeaver after changing them. Credentials cannot be entered in the Web interface.

If no provider is configured, CueWeaver still starts so you can browse media, manage term maps, and view job history. New translations remain unavailable until `PROVIDER` and the matching credentials are configured.

The provider status performs a local preflight only. It confirms that the provider is bundled and its required variables are non-empty; it does not call the provider API or verify credentials, account access, region availability, or model access.

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

### OpenAI

Set `PROVIDER=OpenAI` and configure the OpenAI API:

| Variable | Default | Type | Purpose and notes |
| --- | --- | --- | --- |
| `OPENAI_API_KEY` | None | string | Required. |
| `OPENAI_API_BASE` | Provider default | string | Optional compatible API base URL. |
| `OPENAI_MODEL` | `gpt-5-mini` | string | Model ID sent to the API. |
| `OPENAI_TEMPERATURE` | `0.0` | float | Sampling temperature for non-reasoning models. |
| `OPENAI_RATE_LIMIT` | None | float | Maximum API requests per minute. |
| `OPENAI_REASONING_EFFORT` | `low` | string | Reasoning effort for supported reasoning models. |
| `OPENAI_STREAM_RESPONSES` | `False` | boolean | Stream reasoning responses. |
| `OPENAI_PROXY` | None | string | Optional proxy URL. |

### Azure OpenAI

Set `PROVIDER=Azure`. Azure uses the OpenAI SDK but requires an Azure deployment:

| Variable | Default | Type | Purpose and notes |
| --- | --- | --- | --- |
| `AZURE_API_KEY` | None | string | Required. |
| `AZURE_API_BASE` | None | string | Required Azure resource endpoint. |
| `AZURE_API_VERSION` | None | string | Required API version. |
| `AZURE_DEPLOYMENT_NAME` | None | string | Required deployment name. |
| `AZURE_PROXY` | None | string | Optional proxy URL. |

### Google Gemini

Set `PROVIDER=Gemini` and configure Google AI:

| Variable | Default | Type | Purpose and notes |
| --- | --- | --- | --- |
| `GEMINI_API_KEY` | None | string | Required. |
| `GEMINI_MODEL` | `gemini-3-flash-preview` | string | Model ID. |
| `GEMINI_STREAM_RESPONSES` | `True` | boolean | Stream partial responses. |
| `GEMINI_ENABLE_THINKING` | `False` | boolean | Enable model reasoning when supported. |
| `GEMINI_THINKING_BUDGET` | `100` | integer | Reasoning token budget. |
| `GEMINI_TEMPERATURE` | `0.0` | float | Sampling temperature. |
| `GEMINI_RATE_LIMIT` | `60.0` | float | Maximum API requests per minute. |
| `GEMINI_PROXY` | None | string | Optional proxy URL. |

Gemini model discovery and credential validation happen only when a translation request is made; the startup preflight does not call Google APIs.

### Anthropic Claude

Set `PROVIDER=Claude` and configure Anthropic:

| Variable | Default | Type | Purpose and notes |
| --- | --- | --- | --- |
| `CLAUDE_API_KEY` | None | string | Required. |
| `CLAUDE_MODEL` | `Claude Haiku 4.5` | string | Model ID or display name supported by the API. |
| `CLAUDE_STREAM_RESPONSES` | `True` | boolean | Stream partial responses. |
| `CLAUDE_THINKING` | `False` | boolean | Enable Claude thinking mode. |
| `CLAUDE_MAX_TOKENS` | `4096` | integer | Maximum output tokens. |
| `CLAUDE_MAX_THINKING_TOKENS` | `1024` | integer | Maximum thinking tokens when enabled. |
| `CLAUDE_TEMPERATURE` | `0.0` | float | Sampling temperature. |
| `CLAUDE_RATE_LIMIT` | `10.0` | float | Maximum API requests per minute. |
| `CLAUDE_PROXY` | None | string | Optional proxy URL. |

### Mistral

Set `PROVIDER=Mistral` and configure Mistral:

| Variable | Default | Type | Purpose and notes |
| --- | --- | --- | --- |
| `MISTRAL_API_KEY` | None | string | Required. |
| `MISTRAL_SERVER_URL` | Provider default | string | Optional compatible API base URL. |
| `MISTRAL_MODEL` | `mistral-small-latest` | string | Model ID. Larger models generally translate better. |
| `MISTRAL_TEMPERATURE` | `0.0` | float | Sampling temperature. |
| `MISTRAL_RATE_LIMIT` | None | float | Maximum API requests per minute. |
| `MISTRAL_PROXY` | None | string | Optional proxy URL. |

CueWeaver pins the Mistral SDK below version 2 because PySubtrans 1.6.0 uses its version 1 client API.

### Amazon Bedrock

Set `PROVIDER=Bedrock`. Bedrock requires static AWS credentials and an explicit model ID:

| Variable | Default | Type | Purpose and notes |
| --- | --- | --- | --- |
| `AWS_ACCESS_KEY_ID` | None | string | Required. |
| `AWS_SECRET_ACCESS_KEY` | None | string | Required. |
| `AWS_REGION` | None | string | Required by CueWeaver, for example `us-east-1`. |
| `BEDROCK_MODEL` | None | string | Required model ID with Bedrock access enabled. |
| `BEDROCK_MAX_TOKENS` | `8192` | integer | Maximum output tokens. |
| `BEDROCK_TEMPERATURE` | `0.0` | float | Sampling temperature. |
| `BEDROCK_RATE_LIMIT` | None | float | Maximum API requests per minute. |
| `BEDROCK_PROXY` | None | string | Optional proxy URL. |

The image currently supports access key and secret key authentication. It does not configure IAM roles or session tokens. Bedrock model access must be enabled in AWS, and some models may not follow subtitle translation instructions reliably.

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

- If the CueWeaver Web interface says that the translation provider is unavailable, use the message shown in the status panel to identify the missing variable. Provider names are case-sensitive and must exactly match the table above.
- If translation cannot start, check the provider's API key, endpoint, model, region, and account permissions.
- For a `Custom Server` 404, check `CUSTOM_SERVER_ADDRESS` and `CUSTOM_ENDPOINT`.
- For a `Custom Server` timeout or connection error, verify that the remote server accepts connections from the container and that its firewall allows the request.
- Set `CUSTOM_SUPPORTS_CONVERSATION=false` when the remote service uses a completion endpoint rather than a chat endpoint. Set `CUSTOM_SUPPORTS_SYSTEM_MESSAGES=false` when it does not support system messages.
- If a request is rejected for authentication, check the provider's API key and restart the container.
- Changing any provider environment variable requires a container restart. The Work volume can be kept across restarts; it contains job history and resumable translation state.
- Queued Jobs are restored in queue order after a restart. Jobs that were already extracting or translating are marked interrupted and can be retried from the Jobs page. Jobs interrupted during publishing are reconciled from their durable output; completed Jobs with leftover Work data retry cleanup on restart.

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
