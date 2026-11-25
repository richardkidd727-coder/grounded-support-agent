"""Export the event protocol as JSON Schema for TypeScript code generation.

Usage: uv run export-schemas [output_path]
"""

import json
import sys
from pathlib import Path
from typing import Any

from support_agent.events import AgentEventAdapter

DEFAULT_OUT = (
    Path(__file__).resolve().parents[5]  # cli -> support_agent -> src -> api -> apps -> root
    / "packages"
    / "shared-types"
    / "schema"
    / "agent-event.schema.json"
)


def _strip_property_titles(node: Any) -> None:
    """Pydantic titles every property; json2ts turns each into a noise alias (Seq1, Seq2...)."""
    if isinstance(node, dict):
        props = node.get("properties")
        if isinstance(props, dict):
            for prop in props.values():
                if isinstance(prop, dict):
                    prop.pop("title", None)
        for child in node.values():
            _strip_property_titles(child)
    elif isinstance(node, list):
        for child in node:
            _strip_property_titles(child)


def build_schema() -> dict[str, Any]:
    schema = AgentEventAdapter.json_schema(mode="serialization")
    _strip_property_titles(schema)
    schema["title"] = "AgentEvent"
    return schema


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(build_schema(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
