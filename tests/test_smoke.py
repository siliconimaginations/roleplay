"""Smoke tests — verify the package imports and the CLI stub is callable."""

import pytest

import roleplay


def test_version_exists() -> None:
    assert isinstance(roleplay.__version__, str)
    assert roleplay.__version__ == "0.1.0"


def test_cli_runs(capsys: pytest.CaptureFixture[str]) -> None:
    from roleplay.cli import main

    main()
    captured = capsys.readouterr()
    assert "coming soon" in captured.out
