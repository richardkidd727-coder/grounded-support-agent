"""Shrink Pydantic JSON schemas into what LLM function-calling APIs want.

Pydantic emits `title` on every property and `$defs`/`$ref` for enums and nested models. Titles
are pure token cost (the tool schemas are resent on every model call, against an 8k tokens/minute
free-tier budget), and `$ref` support differs between providers - so refs are inlined.
"""

from __future__ import annotations

from typing import Any


def compact_schema(schema: dict[str, Any]) -> dict[str, Any]:
    defs: dict[str, Any] = schema.get("$defs", {})

    def walk_schema(node: dict[str, Any]) -> dict[str, Any]:
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            siblings = {k: v for k, v in node.items() if k != "$ref"}
            return walk_schema({**defs[ref.removeprefix("#/$defs/")], **siblings})
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key in ("title", "$defs"):
                continue
            if key == "properties" and isinstance(value, dict):
                # property *names* are kept verbatim (a field may be called "title")
                out[key] = {name: walk_schema(sub) for name, sub in value.items()}
            elif isinstance(value, dict):
                out[key] = walk_schema(value)
            elif isinstance(value, list):
                out[key] = [walk_schema(v) if isinstance(v, dict) else v for v in value]
            else:
                out[key] = value
        return out

    return walk_schema(schema)
