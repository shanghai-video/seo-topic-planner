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
assert interface["websiteURL"].startswith("https://"), "websiteURL must be an absolute https URL"
assert interface["developerName"] == manifest["author"]["name"], "developerName and author.name disagree"
assert set(mcp["mcpServers"]) == {"seo-topic-planner"}
assert mcp["mcpServers"]["seo-topic-planner"]["type"] == "stdio"

# Claude Code reads its own manifest; it must describe the same plugin and
# launch the same server entry point as the Codex manifest and mcp.json.
claude_manifest = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
for key in ("name", "version", "description", "author", "homepage", "repository", "license"):
    assert claude_manifest[key] == manifest[key], f".claude-plugin/plugin.json {key} drifts from plugin.json"
codex_server = mcp["mcpServers"]["seo-topic-planner"]
claude_server = claude_manifest["mcpServers"]["seo-topic-planner"]
assert claude_server["command"] == codex_server["command"], "Claude Code and Codex launch different commands"
claude_args = [arg.replace("${CLAUDE_PLUGIN_ROOT}", "${PLUGIN_ROOT}") for arg in claude_server["args"]]
assert claude_args == codex_server["args"], "Claude Code and Codex launch different server entry points"

sys.path.insert(0, str(ROOT / "server"))
from seo_topic_planner.mcp_server import TOOLS  # noqa: E402
from seo_topic_planner.version import PLUGIN_VERSION  # noqa: E402

assert manifest["version"] == PLUGIN_VERSION, "plugin.json and version.py disagree"
assert PLUGIN_VERSION.startswith("2."), "unexpected plugin version"
pyproject = (ROOT / "server" / "pyproject.toml").read_text(encoding="utf-8")
pyproject_version = re.search(r'^version = "([^"]+)"$', pyproject, flags=re.MULTILINE)
assert pyproject_version and pyproject_version.group(1) == PLUGIN_VERSION, "pyproject.toml version drift"

# Every tool must carry review-visible behavior hints.
tool_names = {tool["name"] for tool in TOOLS}
for tool in TOOLS:
    annotations = tool.get("annotations") or {}
    for key in ("title", "readOnlyHint", "openWorldHint", "idempotentHint", "destructiveHint"):
        assert key in annotations, f"{tool['name']} is missing annotation {key}"
    for key in ("readOnlyHint", "openWorldHint", "idempotentHint", "destructiveHint"):
        assert isinstance(annotations[key], bool), f"{tool['name']} {key} must be a boolean"
    if annotations["readOnlyHint"]:
        assert annotations["destructiveHint"] is False, f"{tool['name']} read-only tool cannot be destructive"
assert len(TOOLS) == len(tool_names) == 18, "the tool surface is intentionally small"
core_tools = {"dataforseo_connection", "create_project", "save_gap_analysis", "save_page_plan", "get_report"}
assert core_tools <= tool_names

skill = (ROOT / "skills" / "seo-topic-planner" / "SKILL.md").read_text(encoding="utf-8")
assert skill.startswith("---\nname: seo-topic-planner\n")
assert manifest["extensions"]["com.openai"]["onboardingSkill"] == "./skills/setup/SKILL.md"
setup_skill = (ROOT / "skills" / "setup" / "SKILL.md").read_text(encoding="utf-8")
assert setup_skill.startswith("---\nname: seo-topic-planner-setup\n")
assert (ROOT / "server" / "run.py").is_file()
assert (ROOT / "server" / "seo_topic_planner" / "oauth.py").is_file()
# The Public Suffix List is a required bundled data file for domain matching.
psl = ROOT / "server" / "seo_topic_planner" / "data" / "public_suffix_list.dat"
assert psl.is_file(), "the bundled Public Suffix List is missing"
assert "BEGIN ICANN DOMAINS" in psl.read_text(encoding="utf-8")
for reference in ("keyword-strategy.md", "gap-analysis.md", "page-plan.md"):
    path = ROOT / "skills" / "seo-topic-planner" / "references" / reference
    assert path.is_file(), f"missing skill reference {reference}"
    assert f"references/{reference}" in skill, f"SKILL.md does not point to {reference}"

# The packaging boundary must exclude generated artifacts and secrets.
from build_package import collect_files  # noqa: E402

shippable = [item.as_posix() for item in collect_files()]
for name in shippable:
    assert not name.startswith("."), f"hidden file would ship: {name}"
    assert "__pycache__" not in name, f"cache would ship: {name}"
assert not any(item.endswith((".pyc", ".pyo")) for item in shippable)
assert "skills/seo-topic-planner/references/gap-analysis.md" in shippable
assert "LICENSE" in shippable, "the MIT license text must ship with every copy"
assert manifest["license"] == "MIT"
assert not any(item.startswith("server/tests/") for item in shippable)

print("plugin package validation passed")
