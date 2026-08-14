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
list_term_maps
get_term_map
create_term_map
post_term_map_item_not_found
rename_term_map
replace_term_map
delete_term_map
product_status
api_not_found_handler
product_not_found
browse
create_job
list_jobs
get_job
retry_job
clear_completed_jobs
get_completed_jobs_route_not_found
delete_job

# Pydantic reads request-model configuration dynamically.
RequestBody.model_config
BrowseBody.model_config
expat.ParserCreate().StartDoctypeDeclHandler
expat.ParserCreate().EntityDeclHandler
expat.ParserCreate().ExternalEntityRefHandler
