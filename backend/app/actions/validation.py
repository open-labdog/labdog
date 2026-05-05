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

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from app.actions.types import ActionDefinition, ActionParameter

_PYDANTIC_TYPE: dict[str, type] = {
    "string": str,
    "int": int,
    "bool": bool,
}


def _annotation_for(p: ActionParameter) -> tuple[type, Any]:
    """Pydantic ``(annotation, default)`` tuple for one parameter."""
    if p.type == "choice":
        if p.choices is None or len(p.choices) == 0:
            # Manifest validation should keep this from happening, but
            # a bad row could theoretically arrive — fall back to str.
            ann: type = str
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
