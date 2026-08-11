"""Fixed guidance for using optional metadata during translation."""

_TRANSLATION_CONTEXT_INSTRUCTIONS = """## Translation Context

The following metadata is supplemental context for understanding the series and episode.

### How to use this context

- Source-language metadata is provided primarily for understanding the plot, characters, relationships, identities, and events.
- Target-language metadata is provided primarily as a reference for established localized names and terminology.
- Target-language metadata may be incomplete, inaccurate, written from an omniscient perspective, or describe information that characters do not yet know.
- Do not copy titles, ranks, relationships, institutions, or other terms from the target-language metadata unless they clearly refer to the same entity or concept in the current subtitle.
- Do not reveal a character's true identity, title, relationship, or future status unless the speaker knows it at this point in the story.
- Do not merge different people, institutions, ranks, or concepts merely because they have similar meanings.
- When metadata conflicts with the source subtitle or the immediate dialogue context, the source subtitle and dialogue context take precedence.

Use the following priority when resolving ambiguity:

1. Current source subtitle
2. Immediate subtitle/dialogue context"""

_METADATA_PRIORITY_INSTRUCTIONS = """3. Source-language episode metadata
4. Source-language series metadata
5. Target-language episode metadata
6. Target-language series metadata"""


def translation_context_instructions(*, metadata_available: bool = False) -> str:
    """Return guidance, adding metadata priorities only when metadata exists."""

    if not metadata_available:
        return _TRANSLATION_CONTEXT_INSTRUCTIONS
    return f"{_TRANSLATION_CONTEXT_INSTRUCTIONS}\n{_METADATA_PRIORITY_INSTRUCTIONS}"
