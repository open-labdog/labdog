"""Serialise generated playbooks so operator data is never templated (SEC-39).

ansible-core templates task arguments (and task names) on the controller
before it sends them to the target. Every module generator places operator
strings — cron commands, firewall rule comments inside the rendered
ruleset, ``/etc/hosts`` lines, unit-file content, user shells — into task
arguments, so a value containing a Jinja delimiter used to be evaluated on
the LabDog host, as the LabDog service user, on the next sync or plan.

The boundary is the YAML text itself: any string scalar that contains a
template delimiter is written with the ``!unsafe`` tag, which ansible-core
honours and passes to the module verbatim. Only strings LabDog authored as
templates are exempt, and those must be marked with :class:`Template`.
Making "unsafe" the default means a new operator-supplied field is covered
without anyone remembering to wrap it; forgetting to mark a LabDog template
fails loudly (the expression reaches the host as literal text) instead of
silently opening a code-execution path.

Unlike extra-vars (the reason SEC-20 rejects delimiters instead), the tag is
part of the playbook file, so it survives ansible-runner.
"""

from __future__ import annotations

import re
from typing import Any

import yaml

#: Anything ansible-core would treat as the start of a template.
_DELIMITERS = re.compile(r"\{\{|\{%|\{#")

UNSAFE_TAG = "!unsafe"


class Template(str):
    """A LabDog-authored Jinja string that Ansible should evaluate.

    Never wrap operator input in this. Every other string containing a
    template delimiter is emitted as ``!unsafe``.
    """

    __slots__ = ()


def has_template_delimiters(value: str) -> bool:
    return bool(_DELIMITERS.search(value))


class _PlaybookDumper(yaml.Dumper):
    pass


def _represent_str(dumper: yaml.Dumper, data: str) -> yaml.Node:
    if isinstance(data, Template) or not has_template_delimiters(data):
        return dumper.represent_str(str(data))
    style = "|" if "\n" in data else None
    return dumper.represent_scalar(UNSAFE_TAG, str(data), style=style)


_PlaybookDumper.add_representer(str, _represent_str)
_PlaybookDumper.add_representer(Template, _represent_str)


class _PlaybookLoader(yaml.SafeLoader):
    pass


def _construct_unsafe(loader: yaml.SafeLoader, node: yaml.Node) -> str:
    # Plain ``str``: the dumper tags it ``!unsafe`` again on the way out.
    return str(loader.construct_scalar(node))  # type: ignore[arg-type]


def _construct_str(loader: yaml.SafeLoader, node: yaml.Node) -> str:
    value = loader.construct_scalar(node)  # type: ignore[arg-type]
    # Only ``dump_playbook`` output is ever re-loaded, and there an
    # untagged string with a delimiter was a :class:`Template`.
    if isinstance(value, str) and has_template_delimiters(value):
        return Template(value)
    return value  # type: ignore[return-value]


_PlaybookLoader.add_constructor(UNSAFE_TAG, _construct_unsafe)
_PlaybookLoader.add_constructor("tag:yaml.org,2002:str", _construct_str)


def dump_playbook(playbook: Any) -> str:
    """``yaml.dump`` for playbooks, with operator strings tagged ``!unsafe``."""
    return yaml.dump(playbook, Dumper=_PlaybookDumper, default_flow_style=False, sort_keys=False)


def load_playbook(text: str) -> Any:
    """Inverse of :func:`dump_playbook`, preserving which strings are templates."""
    return yaml.load(text, Loader=_PlaybookLoader)  # noqa: S506 - SafeLoader subclass
