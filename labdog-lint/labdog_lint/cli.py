import sys

import click
import yaml
from pydantic import ValidationError

from labdog_lint.formatter import (
    echo_error,
    echo_rule_error,
    echo_rule_warning,
    echo_warning,
)
from labdog_lint.schema import LabDogGroupYAML
from labdog_lint.validators import validate_cidr, validate_port


@click.command()
@click.argument("files", nargs=-1, type=click.Path(exists=True))
@click.option("--strict", is_flag=True, help="Treat warnings as errors")
def main(files, strict):
    """Validate LabDog YAML firewall rule files."""
    if not files:
        click.echo("Usage: labdog-lint <file> [file...]", err=True)
        sys.exit(1)

    errors = 0
    warnings = 0

    for filepath in files:
        with open(filepath) as f:
            content = f.read()

        try:
            data = yaml.safe_load(content)
        except yaml.YAMLError as e:
            echo_error(filepath, f"Invalid YAML syntax: {e}")
            errors += 1
            continue

        if not isinstance(data, dict):
            echo_error(filepath, "YAML must be a mapping")
            errors += 1
            continue

        try:
            parsed = LabDogGroupYAML.model_validate(data)
        except ValidationError as e:
            for err in e.errors():
                loc = ".".join(str(loc_part) for loc_part in err["loc"])
                echo_error(filepath, f"{loc}: {err['msg']}")
            errors += 1
            continue

        known_keys = {"group", "priority", "firewall"}
        for key in data:
            if key not in known_keys:
                echo_warning(filepath, f"Unknown top-level key '{key}' (future module?)")
                warnings += 1

        if parsed.firewall and parsed.firewall.rules:
            for i, rule in enumerate(parsed.firewall.rules, 1):
                if rule.source:
                    err = validate_cidr(rule.source)
                    if err:
                        echo_rule_error(filepath, i, f"source: {err}")
                        errors += 1
                if rule.dest:
                    err = validate_cidr(rule.dest)
                    if err:
                        echo_rule_error(filepath, i, f"dest: {err}")
                        errors += 1

                if rule.port is not None:
                    err = validate_port(rule.port)
                    if err:
                        echo_rule_error(filepath, i, err)
                        errors += 1

                if rule.protocol == "icmp" and rule.port is not None:
                    echo_rule_error(filepath, i, "ICMP protocol cannot have port")
                    errors += 1

                if rule.source == "0.0.0.0/0" and rule.port is None and rule.action == "allow":
                    echo_rule_warning(
                        filepath,
                        i,
                        "allows all traffic from 0.0.0.0/0 (no port restriction)",
                    )
                    warnings += 1

    if errors > 0:
        sys.exit(1)
    if strict and warnings > 0:
        sys.exit(2)
    sys.exit(0)
