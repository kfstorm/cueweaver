"""Vulture whitelist for CueWeaver false positives."""

# PySubtrans project attributes are accessed dynamically.
_.write_translation

# HTTP application contracts are public adapter APIs.
CueWeaverApplication
TranslateRequest.subtitle_path
TranslateRequest.term_map_path
create_app

# FastAPI middleware callbacks are registered dynamically by the decorator.
require_json_content_type

# Pydantic reads request-model configuration dynamically.
RequestBody.model_config
