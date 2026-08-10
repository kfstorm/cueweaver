"""Vulture whitelist for CueWeaver false positives."""

# Public result API.
JobResult.status
JobRunner.publish_intermediate
JobRunner.retry_publishing
JobRunner.retry_metadata

# PySubtrans project attributes are accessed dynamically.
_.write_translation
_.translation

# HTTPServer callback methods are invoked by the standard library.
ProviderFixtureHandler.do_POST
ProviderFixtureHandler.log_message

# Metadata methods are selected by entity/field name at runtime.
MetadataProvider.get_series_title
MetadataProvider.get_episode_title
TMDbMetadataProvider.get_series_title
TMDbMetadataProvider.get_episode_title
