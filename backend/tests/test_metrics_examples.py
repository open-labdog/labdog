"""Guards for the shipped Prometheus examples in ``docs/examples/prometheus/``.

Pure unit tests — no DB, no app, so this module deliberately does *not* carry
``pytest.mark.integration``.

Two things are checked:

1. The files parse. A truncated or hand-edited Grafana dashboard export is
   silently broken until someone tries to import it; ``json.loads`` catches
   that in CI instead.
2. Every ``labdog_*`` metric referenced by the dashboard or the alert rules
   actually appears in the exporter source. This is the link that stops the
   examples rotting: rename a metric in ``app/metrics/collector.py`` and the
   dashboard panel referencing the old name fails here rather than rendering
   "No data" months later in someone's Grafana.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXAMPLES = _REPO_ROOT / "docs" / "examples" / "prometheus"
_COLLECTOR = Path(__file__).resolve().parents[1] / "app" / "metrics" / "collector.py"

#: Histogram families are emitted as ``<name>_bucket`` / ``_sum`` / ``_count``
#: samples, but only the family name appears in the collector source.
_HISTOGRAM_SUFFIXES = ("_bucket", "_sum", "_count")

#: ``labdog_*`` names that legitimately appear in the examples without being
#: emitted by this exporter.
_NOT_EXPORTER_METRICS = frozenset(
    {
        # Stamped on node_exporter series by the Alloy install action — part of
        # the *inbound* Mimir integration, not this exporter. See
        # docs/ui/metrics.md.
        "labdog_host_id",
        "labdog_hostname",
    }
)


def _example_files(pattern: str) -> list[Path]:
    return sorted(_EXAMPLES.glob(pattern))


def test_examples_directory_exists():
    assert _EXAMPLES.is_dir(), f"missing examples directory: {_EXAMPLES}"


@pytest.mark.parametrize("path", _example_files("*.json"), ids=lambda p: p.name)
def test_json_examples_parse(path: Path):
    """A truncated dashboard export is invisible until import time."""
    data = json.loads(path.read_text())
    assert isinstance(data, dict)


@pytest.mark.parametrize("path", _example_files("*.yml"), ids=lambda p: p.name)
def test_yaml_examples_parse(path: Path):
    data = yaml.safe_load(path.read_text())
    assert isinstance(data, dict)


def test_dashboard_has_expected_identity():
    """The uid is what "update in place" reimports key off."""
    dashboard = json.loads((_EXAMPLES / "labdog-overview.json").read_text())
    assert dashboard["uid"] == "labdog-overview"
    assert dashboard["title"]
    assert dashboard["panels"], "dashboard has no panels"
    # Import prompts for a datasource, so the input must be declared.
    assert any(i["name"] == "DS_PROMETHEUS" for i in dashboard["__inputs"])


def test_alert_rules_are_well_formed():
    rules_doc = yaml.safe_load((_EXAMPLES / "labdog-alerts.yml").read_text())
    groups = rules_doc["groups"]
    assert groups, "no rule groups"

    rules = [r for g in groups for r in g["rules"]]
    assert rules, "no rules"

    for rule in rules:
        name = rule.get("alert")
        assert name, f"rule without an alert name: {rule}"
        assert rule.get("expr"), f"{name}: missing expr"
        # Severity drives routing; an unlabelled alert silently bypasses it.
        assert rule.get("labels", {}).get("severity"), f"{name}: missing severity label"
        assert rule.get("annotations", {}).get("summary"), f"{name}: missing summary"


def _referenced_metric_names(text: str) -> set[str]:
    names = set(re.findall(r"\blabdog_[a-z0-9_]+\b", text))
    resolved: set[str] = set()
    for name in names:
        for suffix in _HISTOGRAM_SUFFIXES:
            if name.endswith(suffix):
                name = name[: -len(suffix)]
                break
        resolved.add(name)
    return resolved - _NOT_EXPORTER_METRICS


@pytest.mark.parametrize(
    "filename", ["labdog-overview.json", "labdog-alerts.yml"], ids=["dashboard", "alerts"]
)
def test_referenced_metrics_exist_in_exporter(filename: str):
    """Every labdog_* metric the examples use must exist in the exporter.

    Catches the drift that otherwise shows up as a silent "No data" panel or an
    alert that can never fire.
    """
    source = _COLLECTOR.read_text()
    referenced = _referenced_metric_names((_EXAMPLES / filename).read_text())
    assert referenced, f"{filename} references no labdog metrics — regex likely broke"

    missing = sorted(name for name in referenced if name not in source)
    assert not missing, (
        f"{filename} references metric(s) not emitted by "
        f"app/metrics/collector.py: {missing}"
    )
