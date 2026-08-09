"""Vulture whitelist for CueWeaver false positives."""

# Public result API.
JobResult.status

# PySubtrans project attributes are accessed dynamically.
_.write_translation

# HTTPServer callback methods are invoked by the standard library.
ProviderFixtureHandler.do_POST
ProviderFixtureHandler.log_message
