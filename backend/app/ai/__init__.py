"""AI subsystem — agentic system administration.

Layout mirrors the other integration modules (``app.grafana``,
``app.proxmox``): models / schemas / service / client, with the
agent-specific pieces split out:

- ``models``     — AIProvider, AISession, AIMessage, AIToolCall, AIUsageDay
- ``schemas``    — pydantic request/response shapes
- ``service``    — provider lookup, session lifecycle, usage/budget accounting
- ``providers/`` — one streaming interface, three backends
- ``tools/``     — the capabilities the model may invoke
- ``safety``     — command classification (default-deny)
- ``redaction``  — strip secrets from command output before it reaches an LLM
- ``loop``       — the agentic turn loop
"""
