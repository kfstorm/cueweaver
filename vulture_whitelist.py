"""Vulture whitelist for CueWeaver false positives."""

# PySubtrans project attributes are accessed dynamically.
_.write_translation

# HTTP application contracts are public adapter APIs.
CueWeaverApplication
TranslateRequest.subtitle_path
TranslateRequest.term_map_path
create_app
create_development_app_from_env
create_product_app
create_product_app_from_env

# FastAPI middleware callbacks are registered dynamically by the decorator.
require_json_content_type
product_status
spa
api_root_not_found
api_path_not_found

# Pydantic reads request-model configuration dynamically.
RequestBody.model_config
