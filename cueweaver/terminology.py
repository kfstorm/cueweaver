"""Episode-level lexical filtering for series terminology."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass

TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_ASS_OVERRIDE_RE = re.compile(r"\{\\[^}]*\}")


class TerminologyConflictError(ValueError):
    """Raised when normalized terminology sources have conflicting targets."""


@dataclass(frozen=True)
class TerminologyFilterResult:
    """Terminology selected for one episode and its lexical hit counts."""

    terminology: dict[str, str]
    occurrences: dict[str, int]


@dataclass(frozen=True)
class _TermEntry:
    source: str
    target: str
    tokens: tuple[str, ...]


class _TrieNode:
    def __init__(self) -> None:
        self.children: dict[str, _TrieNode] = {}
        self.entries: tuple[_TermEntry, ...] = ()


def filter_terminology_for_text(
    glossary: Mapping[str, str],
    source_text: str,
) -> TerminologyFilterResult:
    """Select terminology whose normalized source phrases occur in *source_text*."""

    entries_by_tokens: dict[tuple[str, ...], list[_TermEntry]] = {}
    for source, target in glossary.items():
        tokens = tuple(tokenize(source))
        if not tokens:
            continue
        entry = _TermEntry(source, target, tokens)
        entries = entries_by_tokens.setdefault(tokens, [])
        if entries and any(existing.target != target for existing in entries):
            conflicting = entries[0]
            raise TerminologyConflictError(
                "Terminology sources normalize to the same token sequence with "
                "conflicting targets: "
                f"{conflicting.source!r} -> {conflicting.target!r}; "
                f"{source!r} -> {target!r}; normalized tokens={tokens!r}"
            )
        entries.append(entry)

    if not entries_by_tokens or not source_text:
        return TerminologyFilterResult(terminology={}, occurrences={})

    trie = _build_trie(entries_by_tokens)
    episode_tokens = tokenize(source_text)
    selected: dict[str, str] = {}
    occurrences: dict[str, int] = {}
    i = 0
    while i < len(episode_tokens):
        node = trie
        j = i
        best_entries: tuple[_TermEntry, ...] = ()
        best_end = i

        while j < len(episode_tokens):
            child = node.children.get(episode_tokens[j])
            if child is None:
                break
            node = child
            if node.entries:
                best_entries = node.entries
                best_end = j
            j += 1

        if not best_entries:
            i += 1
            continue

        for entry in best_entries:
            selected.setdefault(entry.source, entry.target)
            occurrences[entry.source] = occurrences.get(entry.source, 0) + 1
        i = best_end + 1

    return TerminologyFilterResult(terminology=selected, occurrences=occurrences)


def normalize_text(text: str) -> str:
    """Normalize Unicode spelling and punctuation without changing word meaning."""

    text = unicodedata.normalize("NFKC", text)
    text = text.casefold()
    return (
        text.replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2010", "-")
        .replace("\u2011", "-")
        .replace("\u2012", "-")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u2015", "-")
    )


def tokenize(text: str) -> list[str]:
    """Strip subtitle markup and return Unicode-aware lexical tokens."""

    text = _ASS_OVERRIDE_RE.sub(" ", text)
    text = _HTML_TAG_RE.sub(" ", text)
    return TOKEN_RE.findall(normalize_text(text))


def _build_trie(
    entries_by_tokens: Mapping[tuple[str, ...], list[_TermEntry]],
) -> _TrieNode:
    root = _TrieNode()
    for tokens, entries in entries_by_tokens.items():
        node = root
        for token in tokens:
            node = node.children.setdefault(token, _TrieNode())
        node.entries = tuple(entries)
    return root
