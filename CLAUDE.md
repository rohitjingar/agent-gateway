# Agent Gateway — Project Constitution

## What this project is
An MCP gateway: multiple MCP servers sit behind a single gateway that handles
auth (OAuth/JWT), per-tool access control, rate limiting, audit logging,
OpenTelemetry tracing of every tool call, and human-in-the-loop approval for
high-risk tools. Enterprise pattern (registry + approval gates on risky actions).

Full month context, career goals, and skills tracker: read `docs/CONTEXT.md`
Current spec and task plan: read `specs/001-agent-gateway-spec.md`

## Who you are working with (IMPORTANT — read every session)
Rohit: strong backend engineer (Python, distributed systems, Redis, MongoDB,
AWS, Databricks) but NEW to: AI engineering, MCP, macOS, and Claude Code.
This project exists to TEACH him, not just to ship code.

## Teaching protocol (non-negotiable)
1. Before building any component: explain it from FIRST PRINCIPLES —
   what problem exists, why naive solutions fail, why this design wins.
2. Give ONE small concrete example or analogy per concept (preferably
   mapped to his existing backend knowledge: queues, caches, load
   balancers, retries — things he already knows from Aviso).
3. Only after he confirms understanding, write the code.
4. After each task: 2-3 line recap of what was learned ("teach-back
   summary") so it sticks. Ask HIM to explain it back in one sentence
   when the concept is important.
5. Never dump large amounts of code without walking through the design
   decision first. Small steps. He should be able to explain every file
   in this repo in an interview.

## Workflow rules
- Spec-driven: no code without a task in the current spec. Update the
  spec when scope changes.
- Plan Mode first for anything non-trivial; present the plan, get
  approval, then implement.
- One task = one atomic commit with a clear message (conventional
  commits: feat/fix/docs/test/refactor).
- Write tests alongside features, not after. pytest.
- After each completed phase, update the skills tracker in docs/CONTEXT.md.

## Stack
Python 3.12+, FastAPI, MCP Python SDK, PostgreSQL, Redis,
OpenTelemetry, Docker. uv for dependency management. ruff for lint/format.

## Conventions
- src/ layout; keep modules small and single-purpose.
- Type hints everywhere; pydantic for all boundaries.
- No secrets in code — .env + pydantic-settings, .env in .gitignore.
