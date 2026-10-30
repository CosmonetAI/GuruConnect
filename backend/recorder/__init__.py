"""Office Recorder trusted API and processing service."""

import os

# GuruConnect uses Pydantic's built-in validation and no validation plugins.
# Autodiscovery scans every installed distribution before the API can start;
# cloud-managed development environments can block/cancel those metadata reads.
# An explicit environment value can still enable a configured plugin setup.
os.environ.setdefault("PYDANTIC_DISABLE_PLUGINS", "__all__")
