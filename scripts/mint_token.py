"""Mint a demo JWT for a subject+role using the configured secret.

    uv run python scripts/mint_token.py alice developer
Then:
    curl -H "Authorization: Bearer <token>" http://127.0.0.1:8000/tools
"""

from __future__ import annotations

import sys

from agent_gateway.auth import create_access_token
from agent_gateway.rbac import ROLE_POLICY

if __name__ == "__main__":
    subject = sys.argv[1] if len(sys.argv) > 1 else "demo"
    role = sys.argv[2] if len(sys.argv) > 2 else "developer"
    if role not in ROLE_POLICY:
        sys.exit(f"unknown role {role!r}; known roles: {sorted(ROLE_POLICY)}")
    print(create_access_token(subject, role))
