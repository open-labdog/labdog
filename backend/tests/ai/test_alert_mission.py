"""The alert prompt template, and the validation that keeps it usable.

An operator can rewrite the wording an alert investigation starts from.
The whole risk of letting them is that the template is a ``str.format``
string: a placeholder that does not exist raises ``KeyError`` inside a
Celery task, hours later, with nothing in the UI to say why the alert
went uninvestigated. So the tests here are mostly about the moment of
saving — a bad template must be refused while the person who wrote it is
still looking at it.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.ai.alert_mission import DEFAULT_TEMPLATE, FIELDS, render, validate_template
from app.settings_service import SETTING_DEFINITIONS, _validate

KEY = "ai.alert_mission_template"


class FakeAlert:
    """Only the attributes the renderer reads."""

    def __init__(self, **kw):
        self.alertname = kw.get("alertname", "HostDown")
        self.severity = kw.get("severity", "critical")
        self.status = kw.get("status", "firing")
        self.starts_at = kw.get("starts_at", datetime(2026, 8, 23, 19, 0, tzinfo=UTC))
        self.labels = kw.get("labels", {"instance": "jellyfin", "severity": "critical"})
        self.annotations = kw.get("annotations", {"summary": "it is down"})


def test_the_shipped_default_is_a_valid_template():
    # If this ever fails, every fresh install refuses to save its own default.
    assert validate_template(DEFAULT_TEMPLATE) == DEFAULT_TEMPLATE


def test_the_default_setting_is_the_shipped_template():
    assert SETTING_DEFINITIONS[KEY]["default"] == DEFAULT_TEMPLATE


def test_every_documented_field_actually_renders():
    """The help text promises these placeholders; they must all work."""
    template = " ".join("{" + name + "}" for name in FIELDS)
    out = render(template, FakeAlert())
    assert "HostDown" in out
    assert "critical" in out
    assert "firing" in out
    assert "2026-08-23T19:00:00+00:00" in out
    assert "- instance: jellyfin" in out
    assert "- summary: it is down" in out


@pytest.mark.parametrize(
    "template",
    [
        "",
        "   \n  ",
        "Investigate {hostname} please",  # not a field
        "Look at {} for me",  # positional
        "Look at {0} for me",
        "Reach into {labels[0]}",  # index access is deliberately refused
        "Unbalanced {alertname",
        "{severity:d}",  # a format spec the value cannot satisfy
    ],
)
def test_unusable_templates_are_refused(template):
    with pytest.raises(ValueError):
        validate_template(template)


def test_the_refusal_names_the_bad_placeholder_and_the_good_ones():
    with pytest.raises(ValueError) as exc:
        validate_template("Investigate {hostname}")
    message = str(exc.value)
    assert "{hostname}" in message
    assert "{alertname}" in message  # tells them what they *can* use


def test_a_template_with_no_placeholders_is_allowed():
    """Standing instructions with no alert detail are a real choice."""
    assert validate_template("Check the host and tell me if it is healthy.")


def test_literal_braces_survive():
    assert render("use {{}} for empty", FakeAlert()) == "use {} for empty"


def test_missing_severity_and_start_time_are_labelled_not_blank():
    out = render(DEFAULT_TEMPLATE, FakeAlert(severity=None, starts_at=None, annotations={}))
    assert "(not labelled)" in out
    assert "(unknown)" in out
    assert "(none)" in out


def test_render_falls_back_when_a_stored_template_has_gone_bad():
    """A template saved against an older release must not strand an alert.

    Save-time validation cannot protect a template from a *later* LabDog
    dropping a field it names. Starting the investigation with generic
    wording beats not starting it.
    """
    out = render("Investigate {a_field_a_later_release_removed}", FakeAlert())
    assert out.startswith("A monitoring alert fired")


def test_render_uses_the_default_when_nothing_is_stored():
    assert render(None, FakeAlert()).startswith("A monitoring alert fired")
    assert render("", FakeAlert()).startswith("A monitoring alert fired")


class TestTheSettingsLayerEnforcesIt:
    """`text` settings are free-form, which is not the same as unchecked."""

    def test_a_valid_template_saves(self):
        assert _validate(KEY, "Check {alertname}.") == "Check {alertname}."

    def test_an_invalid_template_is_refused_with_the_key_named(self):
        with pytest.raises(ValueError) as exc:
            _validate(KEY, "Check {nonsense}.")
        assert KEY in str(exc.value)

    def test_the_length_cap_bites(self):
        limit = SETTING_DEFINITIONS[KEY]["max_length"]
        with pytest.raises(ValueError, match="maximum length"):
            _validate(KEY, "x" * (limit + 1))
        assert _validate(KEY, "x" * limit)
