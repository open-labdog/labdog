"""Dependency-free renderer for Prometheus text exposition format **0.0.4**.

Deliberately **not** OpenMetrics 1.0 — 0.0.4 is universally parsed by every
Prometheus-compatible scraper, while OpenMetrics 1.0 adds a mandatory
``# EOF`` terminator and different counter-naming semantics that commonly
cause "half the metrics vanished" when a renderer gets it slightly wrong.
True OpenMetrics support (via ``Accept`` content negotiation) is a purely
additive follow-up — see ``TODO.md``.

Why hand-rolled instead of ``prometheus_client``: that library's value is
its registry, and ``app.metrics.collector.collect`` can't use it —
``collect()`` is async while ``prometheus_client``'s ``.collect()`` protocol
is sync. Worse, its in-process counters would be *wrong* under multiple
uvicorn workers (each worker has its own memory), whereas every metric here
is derived fresh from the database, so all workers report identical values.
Its default registry also auto-registers process/GC/platform collectors
that describe the (near-idle) API process — actively misleading as "LabDog
health". So the library would only have contributed ``CONTENT_TYPE_LATEST``
and ~10 lines of escaping logic; not worth the dependency.

Callers never hand-assemble ``Sample`` tuples for bucket-style metrics —
use the ``gauge()`` / ``counter()`` / ``histogram()`` builders below.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

#: A label set, e.g. ``(("status", "success"), ("module", "firewall"))``.
#: Always sorted by the caller for deterministic output (not enforced here).
Labels = tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class Sample:
    """One exposition line's data, minus the metric family name.

    ``suffix`` is appended to the family name to form the full metric name
    — ``""`` for gauges/counters (whose family name is already complete,
    e.g. ``labdog_sync_jobs_total``), or ``"_bucket"`` / ``"_sum"`` /
    ``"_count"`` for histogram components (whose family name is the base,
    e.g. ``labdog_sync_job_duration_seconds``).
    """

    suffix: str
    labels: Labels
    value: float


@dataclass(frozen=True, slots=True)
class MetricFamily:
    name: str
    type: str  # "gauge" | "counter" | "histogram"
    help: str
    samples: tuple[Sample, ...]


# ---------------------------------------------------------------------------
# Escaping — text exposition format 0.0.4 rules
# ---------------------------------------------------------------------------


def escape_help(text: str) -> str:
    """Escape a HELP string: backslash and newline only (NOT quotes).

    Order matters — backslash must be escaped first, otherwise the
    backslash introduced by escaping ``\\n`` would itself get re-escaped.
    """
    return text.replace("\\", "\\\\").replace("\n", "\\n")


def escape_label_value(value: str) -> str:
    """Escape a label value: backslash, then quote, then newline.

    Backslash MUST be escaped first — escaping quotes or newlines before
    the backslash pass would double-escape the backslashes those passes
    introduce (e.g. ``a"b`` must become ``a\\"b``, not ``a\\\\"b``).
    """
    value = value.replace("\\", "\\\\")
    value = value.replace('"', '\\"')
    value = value.replace("\n", "\\n")
    return value


def format_float(value: float) -> str:
    """Render a float per exposition-format conventions.

    NaN/+Inf/-Inf get their literal spellings; integral floats are
    rendered without a trailing ``.0`` (``3.0`` -> ``"3"``) since that is
    how bucket bounds and whole-number gauges read most naturally, and
    every value round-trips through a real float parser regardless.
    """
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "+Inf" if value > 0 else "-Inf"
    if value == int(value):
        return str(int(value))
    return repr(float(value))


def render_labels(labels: Labels) -> str:
    """Render a label set as ``{k="v",...}``, or ``""`` when empty.

    Never emits empty braces (``{}``) — an unlabelled sample has no
    braces at all.
    """
    if not labels:
        return ""
    parts = ",".join(f'{k}="{escape_label_value(v)}"' for k, v in labels)
    return "{" + parts + "}"


def render(families: list[MetricFamily]) -> str:
    """Render a full exposition body: HELP/TYPE once per family, then
    every sample line, with a trailing newline (required by the format).
    """
    lines: list[str] = []
    for family in families:
        lines.append(f"# HELP {family.name} {escape_help(family.help)}")
        lines.append(f"# TYPE {family.name} {family.type}")
        for sample in family.samples:
            metric_name = f"{family.name}{sample.suffix}"
            lines.append(
                f"{metric_name}{render_labels(sample.labels)} {format_float(sample.value)}"
            )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Builders — callers never hand-assemble Sample tuples
# ---------------------------------------------------------------------------


def gauge(name: str, help: str, series: list[tuple[Labels, float]]) -> MetricFamily:
    """Build a gauge family from ``(labels, value)`` pairs."""
    samples = tuple(Sample(suffix="", labels=labels, value=value) for labels, value in series)
    return MetricFamily(name=name, type="gauge", help=help, samples=samples)


def counter(name: str, help: str, series: list[tuple[Labels, float]]) -> MetricFamily:
    """Build a counter family from ``(labels, value)`` pairs.

    ``name`` must already carry the ``_total`` suffix — the exposition
    format's counter convention puts it in the family name itself, not in
    a per-sample suffix (see :class:`Sample`).
    """
    samples = tuple(Sample(suffix="", labels=labels, value=value) for labels, value in series)
    return MetricFamily(name=name, type="counter", help=help, samples=samples)


def histogram(
    name: str,
    help: str,
    *,
    bounds: tuple[float, ...],
    series: list[tuple[Labels, list[int], int, float]],
) -> MetricFamily:
    """Build a histogram family.

    ``series`` is a list of ``(labels, bucket_counts, total_count,
    total_sum)`` — one entry per label combination (e.g. per module).
    ``bucket_counts`` must already be *cumulative* (each bound's count
    includes every observation at or below it) and aligned 1:1 with
    ``bounds``; SQL builds them that way via
    ``COUNT(*) FILTER (WHERE duration <= bound)`` so this function never
    has to accumulate anything itself. This function appends the
    mandatory ``le="+Inf"`` bucket (== ``total_count``), then ``_sum``
    and ``_count``, and — under ``__debug__`` only, so it costs nothing
    in production — asserts the buckets are actually monotonically
    non-decreasing and that ``total_count`` isn't less than the last
    finite bucket (a caller bug that would otherwise silently produce an
    invalid histogram Prometheus accepts but ``histogram_quantile()``
    computes garbage from).
    """
    samples: list[Sample] = []
    for labels, bucket_counts, total_count, total_sum in series:
        if __debug__:
            prev = 0
            for count in bucket_counts:
                assert count >= prev, (
                    f"non-monotonic histogram bucket for {name}{labels}: "
                    f"{count} < previous cumulative count {prev}"
                )
                prev = count
            assert total_count >= prev, (
                f"le=+Inf count ({total_count}) is less than the last finite "
                f"bucket ({prev}) for {name}{labels}"
            )
        for bound, count in zip(bounds, bucket_counts, strict=True):
            le_labels = (*labels, ("le", format_float(bound)))
            samples.append(Sample(suffix="_bucket", labels=le_labels, value=count))
        inf_labels = (*labels, ("le", "+Inf"))
        samples.append(Sample(suffix="_bucket", labels=inf_labels, value=total_count))
        samples.append(Sample(suffix="_sum", labels=labels, value=total_sum))
        samples.append(Sample(suffix="_count", labels=labels, value=total_count))
    return MetricFamily(name=name, type="histogram", help=help, samples=tuple(samples))
