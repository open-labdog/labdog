"""The prompt an alert investigation starts from, and its operator override.

This lives in its own module rather than beside the task that uses it
because two very different things need it: the Celery task that renders
it, and :mod:`app.settings_service`, which holds the operator's edited
copy and must validate one before storing it. Importing the task from
the settings layer would drag Celery into every process that reads a
setting, so the template, its field list, and its validator sit here
where both sides can reach them and neither owns the other.

**Why it is editable at all.** The built-in wording asks a generic
question — "is this real, and what is causing it" — because it has to
work for an alert this code has never seen. An operator knows things it
cannot: which alerts on their estate are chronically noisy, that a
particular exporter lies during backups, that the answer should always
mention the service the host runs. That knowledge has nowhere to go
otherwise, and the alternative is editing the source.

The placeholders are the whole contract, so they are validated when the
operator saves rather than when an alert fires at three in the morning.
A template naming a field that does not exist would otherwise raise
``KeyError`` deep inside a task and leave an alert un-investigated with
nothing in the UI to say why.
"""

from __future__ import annotations

import logging
from string import Formatter

logger = logging.getLogger(__name__)

#: Every placeholder a template may use, and what it expands to. The
#: values are shown to the operator in Settings, so they are written for
#: someone deciding whether to include the field rather than for someone
#: reading this file.
FIELDS: dict[str, str] = {
    "alertname": "The alert's name",
    "severity": "The severity label, or a note that it was not labelled",
    "status": "firing or resolved",
    "starts_at": "When the alert started, in ISO 8601",
    "labels": "Every label, one per line as '- key: value'",
    "annotations": "Every annotation, one per line as '- key: value'",
}

#: How much of the alert LabDog puts in front of the model. The whole
#: label and annotation set, because an investigation is only as good as
#: its context and the operator chose what to label.
DEFAULT_TEMPLATE = """\
A monitoring alert fired and you are investigating it. Find out whether \
it reflects a real problem on the host, and if so, what is causing it.

Alert: {alertname}
Severity: {severity}
Status: {status}
Started: {starts_at}

Labels:
{labels}

Annotations:
{annotations}

Work out what this alert is telling you, check the host it points at, \
and report what you find. If the alert looks like a false positive or \
has already cleared, say so plainly — that is a useful answer.
"""


def _render_pairs(mapping: dict | None) -> str:
    if not mapping:
        return "(none)"
    return "\n".join(f"- {k}: {v}" for k, v in sorted(mapping.items()))


def values_for(event) -> dict[str, str]:  # noqa: ANN001 - AlertEvent, imported lazily
    """The substitutions for one alert. Keys are exactly :data:`FIELDS`."""
    return {
        "alertname": event.alertname,
        "severity": event.severity or "(not labelled)",
        "status": event.status,
        "starts_at": event.starts_at.isoformat() if event.starts_at else "(unknown)",
        "labels": _render_pairs(event.labels),
        "annotations": _render_pairs(event.annotations),
    }


def _placeholder_list() -> str:
    return ", ".join("{" + name + "}" for name in FIELDS)


def validate_template(template: str) -> str:
    """Return ``template`` unchanged, or raise ``ValueError`` saying why not.

    Deliberately strict about field names: only the bare names in
    :data:`FIELDS`, with no attribute or index access. ``{labels[0]}``
    would work today and break the moment the shape behind a field
    changes, and there is no reason to let a prompt reach into it.
    """
    if not template.strip():
        raise ValueError(
            "The prompt cannot be empty — an investigation needs something to start from."
        )

    try:
        parsed = list(Formatter().parse(template))
    except ValueError as exc:
        # Almost always a single brace in prose: "use {} for an empty set".
        raise ValueError(
            f"Unbalanced braces: {exc}. Write {{{{ and }}}} for a literal brace."
        ) from exc

    named = [name for _, name, _, _ in parsed if name is not None]
    unknown = sorted({name for name in named if name not in FIELDS})
    if unknown:
        shown = ", ".join("{" + (name or "") + "}" for name in unknown)
        raise ValueError(
            f"Unknown placeholder(s): {shown}. Available: {_placeholder_list()}. "
            f"Write {{{{ and }}}} for a literal brace."
        )

    # Catches what the field-name check cannot: a format spec or
    # conversion the values will not satisfy, such as {severity:d}.
    try:
        template.format(**dict.fromkeys(FIELDS, ""))
    except (IndexError, KeyError, ValueError) as exc:
        raise ValueError(f"The prompt is not a valid template: {exc}") from exc

    return template


def render(template: str | None, event) -> str:  # noqa: ANN001 - AlertEvent
    """Fill ``template`` in for one alert, falling back to the built-in.

    The fallback is not belt-and-braces around the save-time validation.
    A template saved against one release can name a placeholder a later
    release no longer provides, and an alert investigation that starts
    with slightly generic wording is a far better outcome than one that
    never starts at all.
    """
    values = values_for(event)
    if template:
        try:
            return template.format(**values)
        except Exception:
            logger.warning(
                "ai.alert_mission_template is not usable; falling back to the built-in prompt",
                exc_info=True,
            )
    return DEFAULT_TEMPLATE.format(**values)
