"""Fail-closed re-check of the values handed to ansible-runner.

:mod:`app.actions.validation` rejects template delimiters at the API
boundary, which is where the error message belongs — the operator finds
out while they are still looking at the form. This module is the second
gate, immediately before the values become extra-vars.

It exists because the boundary check protects one shape of the problem
and not all of them. Parameters can reach a run without passing through
``build_param_model``: a row written before this validation existed, a
``choice`` whose permitted values come from a pack manifest rather than
from LabDog, or a future caller that assembles a run directly. The cost
of re-checking is a regex over a handful of short strings; the cost of
not re-checking is arbitrary code execution on the LabDog host, because
Ansible evaluates extra-vars on the controller rather than on the target.

Raising here fails the host run with a clear message rather than
silently dropping the value, which would run the action with a parameter
the operator did not supply.
"""

from __future__ import annotations

from typing import Any

from app.actions.validation import _TEMPLATE_DELIMITERS


def sanitize_extra_vars(parameters: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return *parameters* unchanged, or raise ``ValueError`` naming the key.

    Walks nested dicts and lists: a manifest may declare a structured
    parameter, and a template expression buried in a list element is
    evaluated exactly as readily as one at the top level.
    """
    if not parameters:
        return parameters
    for key, value in parameters.items():
        _check(str(key), value)
    return parameters


def _check(path: str, value: Any) -> None:
    if isinstance(value, str):
        if _TEMPLATE_DELIMITERS.search(value):
            raise ValueError(
                f"action parameter {path!r} contains template delimiters; "
                "refusing to pass it to Ansible, which would evaluate it on "
                "the LabDog host"
            )
        return
    if isinstance(value, dict):
        for k, v in value.items():
            _check(f"{path}.{k}", v)
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _check(f"{path}[{index}]", item)
