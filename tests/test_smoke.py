"""Smoke tests — verify the package imports and the CLI stub is callable."""

import roleplay


def test_version_exists() -> None:
    assert isinstance(roleplay.__version__, str)
    assert roleplay.__version__ == "0.1.0"


def test_cli_help() -> None:
    from typer.testing import CliRunner

    from roleplay.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Usage" in result.output
