"""Aggregated totals for ``drift_samples`` rows that retention has deleted.

See ``alembic/versions/0036_drift_sample_rollup.py`` for why this exists:
the drift counters are ``COUNT(*)``/``SUM()`` over the whole table, so
deleting rows would make a Prometheus counter go backwards.
"""

from sqlalchemy import JSON, BigInteger, Float, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class DriftSampleRollup(Base):
    __tablename__ = "drift_sample_rollup"

    module_type: Mapped[str] = mapped_column(String(50), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), primary_key=True)

    checks: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    add_count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    remove_count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    policy_change_count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")

    #: Only samples that recorded a duration, matching the histogram's own
    #: ``WHERE duration_ms IS NOT NULL``.
    duration_count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    duration_sum_seconds: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    #: Cumulative per-bucket counts, and the bounds they were computed
    #: against. The bounds are stored so a later change to
    #: ``_BUCKETS_DRIFT`` can be detected instead of silently adding
    #: mismatched arrays.
    duration_buckets: Mapped[list] = mapped_column(JSON, nullable=False, server_default="[]")
    duration_bounds: Mapped[list] = mapped_column(JSON, nullable=False, server_default="[]")
