"""Per-module GitOps import handlers.

Each handler in this package is responsible for a single configuration module
(firewall, services, packages, …).  Handlers are called sequentially by the
dispatcher in ``app.gitops.importer`` under a shared advisory lock and
transaction.
"""
