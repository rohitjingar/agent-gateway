"""Authorization: may THIS role call THIS tool? (per-tool RBAC.)

Route-based RBAC ("role X may hit POST /tools/call") is too coarse for agents:
the risk isn't the route, it's the *specific tool*. `read_file` and
`delete_branch` arrive on the same route but carry wildly different blast radius.
So the policy is keyed on the namespaced tool name, not the HTTP path.

Wildcards: "*" (everything), "<server>.*" (a whole upstream).
In production this table would live in a DB or policy file; a dict keeps the
idea legible and is trivial to reason about in review.
"""

from __future__ import annotations

ROLE_POLICY: dict[str, list[str]] = {
    "admin": ["*"],
    "developer": ["files.*", "github.*"],
    "readonly": ["files.read_file", "files.list_dir", "github.list_branches"],
}


def is_allowed(role: str, tool_name: str) -> bool:
    for pattern in ROLE_POLICY.get(role, []):
        if pattern == "*":
            return True
        if pattern.endswith(".*") and tool_name.startswith(pattern[:-1]):
            return True
        if pattern == tool_name:
            return True
    return False


def allowed_tools(role: str, tool_names: list[str]) -> list[str]:
    return [name for name in tool_names if is_allowed(role, name)]
