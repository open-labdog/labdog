"""Forward-only metrics substrate feeding two independent consumers: the
dashboard charts and the Prometheus ``/metrics`` exporter. Three-way split:

* ``recorder.py`` — writes. Appends ``DriftSample`` rows atomically with
  each module's existing drift-check write.
* ``service.py`` — reads for the dashboard UI. Aggregates ``SyncJob`` and
  ``DriftSample`` rows into ``date_trunc`` *time-bucketed* series consumed
  by ``app.api.dashboard``.
* ``aggregates.py`` — reads for the exporter. Point-in-time snapshots
  (current counts / sums / max-ages) consumed by
  ``app.metrics.collector`` and rendered by ``app.metrics.exposition`` at
  ``GET /metrics``.

``service.py`` and ``aggregates.py`` are deliberately **not** the same
code: Prometheus needs current values (its TSDB does its own bucketing,
and back-dated samples are rejected as out-of-order on the next scrape),
while the dashboard needs pre-bucketed history. ``schemas.py`` holds the
Pydantic response shapes for ``service.py``'s dashboard-facing series only
— the exporter's aggregations return plain tuples/dataclasses instead (see
``aggregates.py``), since they're rendered as exposition-format text, not
JSON.
"""
