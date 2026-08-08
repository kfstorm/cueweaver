#!/usr/bin/env python
"""Prototype: DeepSeek V4 thinking on/off — same batch, both quality & cost.

The same 12-line batch is posted twice via PySubtrans's own DeepSeekClient:
    arm A (default, as PySubtrans sends it): no `thinking` field.
    arm B: `thinking: {"type": "disabled"}` (what seconv sends).
Reports latency, token usage (incl. reasoning tokens that prove thinking ran),
and the FULL translated text, so quality and cost can be weighed together.

Throwaway code for issue #8.
"""

import json
import os
import sys
import time
import copy

from PySubtrans import init_options, init_translator, init_subtitles
from PySubtrans.TranslationPrompt import TranslationPrompt
from PySubtrans.TranslationRequest import TranslationRequest

SAMPLE = "/tmp/opencode/pysubtrans-proto/workdir/jitc-e11.sample.srt"


def build_client():
    opts = init_options(
        provider="DeepSeek",
        api_key=os.environ["DEEPSEEK_API_KEY"],
        model="deepseek-v4-flash",
        target_language="Simplified Chinese (zh-CN)",
        prompt="Translate these subtitles to Simplified Chinese (zh-CN)",
    )
    translator = init_translator(opts)
    subs = init_subtitles(filepath=SAMPLE, options=opts)
    lines = [line for batch in subs.scenes[0].batches[:2] for line in batch.originals]
    return translator.client, lines


def run_arm(client, body, label):
    import httpx

    hc = httpx.Client(
        base_url=client.server_address,
        follow_redirects=True,
        timeout=client.timeout,
        headers=client.headers,
        proxy=client.proxy_url,
    )
    start = time.monotonic()
    resp = hc.post(client.endpoint, json=body)
    hc.close()
    elapsed = time.monotonic() - start
    data = resp.json()
    usage = data.get("usage") or {}
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    content = message.get("content") or ""
    reasoning = message.get("reasoning_content") or ""
    tok_detail = usage.get("completion_tokens_details") or {}
    print(f"\n===== ARM {label} =====", flush=True)
    print(f"status={resp.status_code} elapsed={elapsed:.2f}s", flush=True)
    print(
        "usage: prompt={} completion={} total={} reasoning_completion={}".format(
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
            usage.get("total_tokens"),
            tok_detail.get("reasoning_tokens")
            if tok_detail.get("reasoning_tokens") is not None
            else "n/a",
        ),
        flush=True,
    )
    if reasoning.strip():
        print(
            f"[reasoning length={len(reasoning)} chars, preview: {reasoning[:100].strip()!r}]",
            flush=True,
        )
    print("--- translation ---", flush=True)
    print(content, flush=True)
    return elapsed, usage


def main() -> int:
    client, lines = build_client()
    assert lines, "no lines"

    prompt = TranslationPrompt(
        "Translate these subtitles to Simplified Chinese (zh-CN)",
        conversation=True,
    )
    prompt.supports_system_prompt = True
    prompt.supports_system_messages = True
    prompt.system_role = "system"
    prompt.GenerateMessages(
        client.instructions,
        lines,
        {
            "scene": 1,
            "batch": 1,
            "summary": "A guard orders a crowd to stop and identifies himself.",
            "model": "deepseek-v4-flash",
        },
    )
    request = TranslationRequest(prompt)

    base = client._generate_request_body(request, temperature=1.3)
    body_default = copy.deepcopy(base)
    body_disabled = copy.deepcopy(base)
    body_disabled["thinking"] = {"type": "disabled"}

    run_arm(client, body_default, "A: PySubtrans default (no thinking field)")
    run_arm(client, body_disabled, "B: thinking disabled (seconv-style)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
