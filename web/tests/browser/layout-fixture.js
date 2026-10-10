// Synthetic responses keep the layout matrix independent of local Media and providers.
localStorage.setItem(
  "cueweaver.ui-locale",
  sessionStorage.getItem("layout-locale") ?? "zh-CN",
);
const theme = sessionStorage.getItem("layout-theme") ?? "light";
if (theme === "system") localStorage.removeItem("cueweaver.theme");
else localStorage.setItem("cueweaver.theme", theme);

const originalFetch = window.fetch.bind(window);
const timestamp = "2026-10-01T12:00:00Z";
const content = Object.fromEntries(
  Array.from({ length: 152 }, (_, index) => [
    `Source term ${String(index + 1).padStart(3, "0")}`,
    `示例术语 ${index + 1}`,
  ]),
);
const map = {
  id: "example-map",
  name: "示例术语表",
  entry_count: 152,
  updated_at: timestamp,
};
const mediaPath =
  "Example Series - S01E01 - A considerably long episode title WEBRip-1080p.mkv";
const profile = {
  id: "example-profile",
  name: "示例模型配置",
  provider: "OpenAI",
  settings: [],
  deletable: true,
  created_at: timestamp,
  updated_at: timestamp,
};
const job = {
  id: "example-job-0123456789abcdef0123456789abcdef",
  attempt: 1,
  status: "Failed",
  created_at: timestamp,
  started_at: timestamp,
  finished_at: timestamp,
  queue_position: null,
  request: {
    media_path: mediaPath,
    stream_index: 2,
    target_language_code: "zh-Hans",
    model_profile_id: profile.id,
    term_map_mode: "follow",
    term_map: map,
    output_path: "Example.zh-Hans.srt",
    source_format: "srt",
    dynamic_terminology_enabled: true,
    subtitle_terminology_filter_enabled: true,
  },
  error: {
    code: "translation_failed",
    message: "Example provider timed out.\nTry again after checking the connection.",
  },
};
window.fetch = async (input, init) => {
  const url = new URL(typeof input === "string" ? input : input.url, location.origin);
  if (!url.pathname.startsWith("/api/")) return originalFetch(input, init);
  const mode = sessionStorage.getItem("layout-state") ?? "populated";
  const profileMode = sessionStorage.getItem("layout-profiles") ?? mode;
  const state = url.pathname === "/api/model-profiles" ? profileMode : mode;
  if (state === "error")
    return new Response(JSON.stringify({ message: "Example service unavailable" }), {
      status: 503,
      headers: { "content-type": "application/json" },
    });
  if (state === "loading") return new Promise(() => {});
  const empty = state === "empty";
  let data;
  switch (url.pathname) {
    case "/api/model-profiles":
      data = { model_profiles: empty ? [] : [profile] };
      break;
    case "/api/model-profile-reference":
      data = { pysubtrans_version: "1.0", providers: ["OpenAI", "DeepSeek"] };
      break;
    case "/api/model-profile-options":
      data = {
        provider: JSON.parse(init.body).provider,
        options: [
          {
            key: "api_key",
            type: "string",
            description: "Example provider API key",
            choices: null,
          },
          {
            key: "api_base",
            type: "string",
            description: "Example provider base URL",
            choices: null,
          },
        ],
        refresh_when_changed: [],
        setting_updates: {},
      };
      break;
    case "/api/media/browse":
      data = {
        path: JSON.parse(init.body).path,
        entries: empty
          ? []
          : Array.from({ length: 80 }, (_, index) => {
              const path =
                index === 0
                  ? mediaPath
                  : `Example Series - S01E${String(index + 1).padStart(2, "0")} - Episode title.mkv`;
              return {
                kind: "media",
                name: path,
                path,
                title: "Example episode",
                season: 1,
                episode: index + 1,
              };
            }),
      };
      break;
    case "/api/media/discover":
      data = {
        path: JSON.parse(init.body).path,
        directory: "",
        candidates: [
          {
            kind: "embedded",
            stream_index: 2,
            format: "srt",
            tags: { language: "eng" },
          },
        ],
        unsupported_candidates: [],
      };
      break;
    case "/api/term-maps/directory-rules":
      data = {
        rules: empty ? [] : [{ directory: "Example Series/Season 1", term_map: map }],
      };
      break;
    case "/api/term-maps/directory":
      data = {
        directory: url.searchParams.get("path") ?? "",
        local: empty ? null : map,
        effective: empty ? null : map,
        source_directory: "",
      };
      break;
    case "/api/term-maps":
      data = { term_maps: empty ? [] : [map] };
      break;
    case "/api/jobs":
      data = {
        active_jobs: [],
        history_jobs: empty ? [] : [job],
        next_cursor: null,
        matching_count: empty ? 0 : 1,
        completed_count: 0,
      };
      break;
    default:
      if (url.pathname.startsWith("/api/term-maps/")) data = { ...map, content };
      else if (url.pathname.startsWith("/api/jobs/")) data = job;
      else
        return new Response(JSON.stringify({ message: "Unknown example endpoint" }), {
          status: 404,
        });
  }
  return new Response(JSON.stringify(data), {
    headers: { "content-type": "application/json" },
  });
};
