"""Single source of truth for the plugin version.

``plugin.json``, ``server/pyproject.toml`` and the MCP ``serverInfo`` all read
this value through ``validate_package.py`` so a release cannot drift.
"""

PLUGIN_VERSION = "2.1.1"
