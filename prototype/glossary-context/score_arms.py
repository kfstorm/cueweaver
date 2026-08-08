#!/usr/bin/env python
"""Score the final A/B/C outputs on source-aligned, deterministic checks.

This is deliberately not English/Chinese string similarity. It aligns output
blocks by timestamp, checks structural completeness, and evaluates a small
gold set of terms whose target forms come from the TMDB-derived Context.
"""

import json
import re
from pathlib import Path

import srt


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "sample" / "jitc-e11.sample.srt"
WORKDIR = ROOT / "workdir"
ARMS = ("A", "B", "C", "D")

# Gold mappings are limited to terms with an explicit authoritative rendering
# in the TMDB zh-CN episode/show material used to build Context.
GOLD = {
    "Lady Jang": "张玉贞",
    "Dongyi": "同伊",
    "pinellia": "半夏",
    "Police Bureau": "监察部",
    "administrative offices": "监察部",
    "Officer Kang": "姜监察",
}


def load(path: Path) -> dict[float, srt.Subtitle]:
    return {
        round(item.start.total_seconds(), 3): item
        for item in srt.parse(path.read_text(encoding="utf-8"))
    }


def source_hits(source: dict[float, srt.Subtitle], term: str) -> list[float]:
    pattern = re.compile(r"\b" + re.escape(term) + r"\b", re.IGNORECASE)
    return [
        timestamp
        for timestamp, item in source.items()
        if pattern.search(item.content.replace("\n", " "))
    ]


def prompt_char_proxy(arm: str) -> list[int]:
    """Return serialized prompt-message character counts, not provider tokens."""
    project = WORKDIR.parent / f"run.{arm}" / "jitc-e11.sample.subtrans"
    data = json.loads(project.read_text(encoding="utf-8"))
    return [
        sum(
            len(message.get("content") or "")
            for message in (batch.get("prompt") or {}).get("messages", [])
        )
        for batch in data["scenes"][0]["batches"]
    ]


def main() -> int:
    source = load(SOURCE)
    outputs = {arm: load(WORKDIR / f"out-arm{arm}.srt") for arm in ARMS}

    print("## Structural completeness")
    for arm in ARMS:
        missing = sorted(set(source) - set(outputs[arm]))
        extra = sorted(set(outputs[arm]) - set(source))
        print(
            f"ARM {arm}: {len(outputs[arm])}/{len(source)} blocks, "
            f"missing={len(missing)}, extra={len(extra)}"
        )

    print("\n## Prompt-size proxies")
    print("Serialized message characters; not provider token usage.")
    for arm in ARMS:
        counts = prompt_char_proxy(arm)
        print(
            f"ARM {arm}: batches={counts}, total={sum(counts)}, average={sum(counts) / len(counts):.0f}"
        )

    print("\n## Gold-term checks")
    totals = {arm: [0, 0] for arm in ARMS}
    for term, gold in GOLD.items():
        hits = source_hits(source, term)
        if not hits:
            continue
        print(f"\n### {term!r} -> {gold} (source blocks: {len(hits)})")
        for arm in ARMS:
            rendered = [
                outputs[arm][timestamp].content
                for timestamp in hits
                if timestamp in outputs[arm]
            ]
            matches = sum(gold in text for text in rendered)
            variants = sorted(
                {
                    re.sub(r"\s+", " ", text.replace("\n", " ")).strip()
                    for text in rendered
                }
            )
            totals[arm][0] += matches
            totals[arm][1] += len(hits)
            print(f"ARM {arm}: gold={matches}/{len(hits)} variants={variants}")

    print("\n## Gold-term totals")
    for arm in ARMS:
        matched, total = totals[arm]
        print(f"ARM {arm}: {matched}/{total} gold occurrences")

    print("\n## Dynamic terminology snapshots")
    for arm in ("C", "D"):
        path = WORKDIR / f"learned-terminology-arm.{arm}.json"
        if not path.exists():
            continue
        snapshots = json.loads(path.read_text(encoding="utf-8"))
        learned = snapshots[-1] if snapshots else {}
        authoritative = {term: value for term, value in learned.items() if term in GOLD}
        correct = sum(GOLD[term] == value for term, value in authoritative.items())
        print(
            f"ARM {arm}: {len(learned)} learned entries; "
            f"authoritative overlap={len(authoritative)}, correct={correct}"
        )
        print(f"ARM {arm} authoritative entries: {authoritative}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
