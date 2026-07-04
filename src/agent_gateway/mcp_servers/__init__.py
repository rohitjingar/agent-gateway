"""Self-contained demo MCP servers that sit *behind* the gateway.

They are real upstreams (touch disk / mutate state) so the gateway's auth,
rate limiting, audit, and approval gates have something concrete to protect.
No external APIs or secrets, so the whole stack runs with `docker compose up`.
"""
