"""DB-backed action pack configuration and sync subsystem.

This package owns the admin-configurable side of action packs: the
``action_packs`` table, the UI-facing schemas, git authentication for
private repos, and the orchestration that keeps checkouts on disk in
sync with the DB rows.

The filesystem-level "load manifests from a directory" logic lives in
``app.actions.packs`` and is unchanged — this package feeds it packs
it discovers from the DB.
"""
