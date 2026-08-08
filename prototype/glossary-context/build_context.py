#!/usr/bin/env python
"""Build the Context block for prototype #10 from the TMDB API directly.

Throwaway companion to run_arm.py. Never prints the API key. Outputs
context.txt deterministically from TMDB (series 38852, "Jewel in the Crown").

Only the zh-CN episode synopsis carries official Chinese names; the zh-CN
credits translate actor names, not roles, so role names are pulled from the
en-US credits (English) and the san's official names come from the zh-CN synopsis.
"""

import json
import sys
import urllib.request

TMDB_TV = "https://api.themoviedb.org/3/tv/38852"
EPISODE = "season/1/episode/11"
KEY_FILE = "/tmp/tmdb_api_key.txt"


def _fetch(path: str, lang: str) -> dict:
    key = open(KEY_FILE).read().strip()
    base = TMDB_TV if not path else f"{TMDB_TV}/{path}"
    url = f"{base}?language={lang}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def main() -> int:
    show_en = _fetch("", "en-US")
    show_zh = _fetch("", "zh-CN")
    ep_en = _fetch(EPISODE, "en-US")
    ep_zh = _fetch(EPISODE, "zh-CN")

    block = (
        "Narrative background for translating this episode's English subtitles "
        "to Simplified Chinese. Sourced programmatically from TMDB (series id "
        f"{show_en['id']}, {show_en.get('original_name')}):\n\n"
        f'--- Series (TMDB en-US overview: "{show_en.get("name")}") ---\n'
        f"{show_en.get('overview', '')}\n\n"
        f"--- 电视剧（TMDB 中文简介）---\n"
        f"{show_zh.get('overview', '')}\n\n"
        f"--- Episode {ep_en.get('season_number')}x{ep_en.get('episode_number')} "
        f"(en-US): {ep_en.get('name')!r} ---\n"
        f"{ep_en.get('overview', '')}\n\n"
        f"--- 该集剧情简介（TMDB 官方中文）---\n"
        f"{ep_zh.get('overview', '')}\n"
    )
    with open(
        "/tmp/cw-glossary/prototype/glossary-context/workdir/context.txt", "w"
    ) as fh:
        fh.write(block)
    print(f"context.txt written ({len(block)} chars)")


if __name__ == "__main__":
    raise SystemExit(main())
