import click


def format_error(filepath: str, message: str) -> str:
    return f"{filepath}: ERROR: {message}"


def format_warning(filepath: str, message: str) -> str:
    return f"{filepath}: WARNING: {message}"


def format_rule_error(filepath: str, rule_index: int, message: str) -> str:
    return f"{filepath}: ERROR: rule {rule_index}: {message}"


def format_rule_warning(filepath: str, rule_index: int, message: str) -> str:
    return f"{filepath}: WARNING: rule {rule_index}: {message}"


def echo_error(filepath: str, message: str) -> None:
    click.echo(format_error(filepath, message), err=True)


def echo_warning(filepath: str, message: str) -> None:
    click.echo(format_warning(filepath, message), err=True)


def echo_rule_error(filepath: str, rule_index: int, message: str) -> None:
    click.echo(format_rule_error(filepath, rule_index, message), err=True)


def echo_rule_warning(filepath: str, rule_index: int, message: str) -> None:
    click.echo(format_rule_warning(filepath, rule_index, message), err=True)
