"""Lightweight offline validation for the portable plugin package.

Runs with the standard library only:

    python3 plugins/seo-topic-planner/validate_package.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
manifest = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
mcp = json.loads((ROOT / "mcp.json").read_text(encoding="utf-8"))
interface = manifest["extensions"]["com.openai"]["interface"]

assert manifest["name"] == ROOT.name
assert manifest["version"].count(".") == 2
assert len(interface["shortDescription"]) <= 30
assert 1 <= len(interface["defaultPrompt"]) <= 3
assert set(mcp["mcpServers"]) == {"seo-topic-planner"}
assert mcp["mcpServers"]["seo-topic-planner"]["type"] == "stdio"

sys.path.insert(0, str(ROOT / "server"))
from seo_topic_planner.mcp_server import TOOLS  # noqa: E402
from seo_topic_planner.version import PLUGIN_VERSION  # noqa: E402

assert manifest["version"] == PLUGIN_VERSION, "plugin.json and version.py disagree"
pyproject = (ROOT / "server" / "pyproject.toml").read_text(encoding="utf-8")
pyproject_version = re.search(r'^version = "([^"]+)"$', pyproject, flags=re.MULTILINE)
assert pyproject_version and pyproject_version.group(1) == PLUGIN_VERSION, "pyproject.toml version drift"

# Every tool must carry review-visible behavior hints.
tool_names = {tool["name"] for tool in TOOLS}
for tool in TOOLS:
    annotations = tool.get("annotations") or {}
    for key in ("title", "readOnlyHint", "openWorldHint", "idempotentHint"):
        assert key in annotations, f"{tool['name']} is missing annotation {key}"
    assert isinstance(annotations["readOnlyHint"], bool)
    assert isinstance(annotations["openWorldHint"], bool)
    if annotations["readOnlyHint"]:
        assert "destructiveHint" not in annotations, f"{tool['name']} read-only tool must not be destructive"
    else:
        assert isinstance(annotations.get("destructiveHint"), bool), f"{tool['name']} needs destructiveHint"
assert {"connect_dataforseo", "complete_dataforseo_connection", "disconnect_dataforseo"} <= tool_names
assert {"open_brand_context_card", "confirm_brand_context"} <= tool_names
assert {
    "open_brand_entity_card",
    "create_brand_entity",
    "update_brand_entity",
    "confirm_brand_entity",
    "list_brand_entities",
    "get_brand_entity_recommendations",
    "record_topical_entities",
    "list_topical_entities",
    "record_opportunity_analysis",
} <= tool_names

skill = (ROOT / "skills" / "seo-topic-planner" / "SKILL.md").read_text(encoding="utf-8")
assert skill.startswith("---\nname: seo-topic-planner\n")
assert manifest["extensions"]["com.openai"]["onboardingSkill"] == "./skills/setup/SKILL.md"
setup_skill = (ROOT / "skills" / "setup" / "SKILL.md").read_text(encoding="utf-8")
assert setup_skill.startswith("---\nname: seo-topic-planner-setup\n")
assert (ROOT / "server" / "run.py").is_file()
assert (ROOT / "server" / "seo_topic_planner" / "oauth.py").is_file()
assert (ROOT / "ui" / "connection-settings.html").is_file()
assert (ROOT / "ui" / "brand-context.html").is_file()
assert (ROOT / "ui" / "brand-entity.html").is_file()

# The packaging boundary must exclude generated artifacts and secrets.
from build_package import collect_files  # noqa: E402

shippable = [item.as_posix() for item in collect_files()]
for name in shippable:
    assert not name.startswith("."), f"hidden file would ship: {name}"
    assert "__pycache__" not in name, f"cache would ship: {name}"
assert not any(item.endswith((".pyc", ".pyo")) for item in shippable)

print("plugin package validation passed")
