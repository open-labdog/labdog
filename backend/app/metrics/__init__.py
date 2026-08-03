"""Forward-only metrics substrate for dashboard charts and (later) an
OpenMetrics ``/metrics`` exporter.

``recorder.py`` writes ``DriftSample`` rows atomically with each module's
existing drift-check write. ``service.py`` aggregates ``SyncJob`` and
``DriftSample`` rows into the time-bucketed series consumed by
``app.api.dashboard`` (and reusable by a future exporter). ``schemas.py``
holds the shared Pydantic response shapes.
"""
