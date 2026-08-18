"""Regression tests for safe runtime dotenv loading."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"
sys.path.insert(0, str(APP))

from wyzebridge.env_loader import main, shell_exports  # noqa: E402


def test_shell_exports_preserves_shell_metacharacters_as_literal_data(tmp_path):
    marker = tmp_path / "must_not_exist"
    env_file = tmp_path / ".env"
    env_file.write_text(
        f'FOO=<example>\nBAR=$(touch {marker})\nQUOTED="hello world"\n',
        encoding="utf-8",
    )

    exports = shell_exports(env_file)
    result = subprocess.run(
        ["sh", "-c", f'{exports}\nprintf "%s|%s|%s\\n" "$FOO" "$BAR" "$QUOTED"'],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )

    assert result.returncode == 0, result.stdout
    assert result.stdout.strip() == f"<example>|$(touch {marker})|hello world"
    assert not marker.exists()


def test_shell_exports_rejects_non_assignment_before_emitting_output(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("GOOD=1\nnot an assignment\n", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid dotenv assignment"):
        shell_exports(env_file)


def test_main_returns_nonzero_for_invalid_env_without_stdout(tmp_path, capsys):
    env_file = tmp_path / ".env"
    env_file.write_text("GOOD=1\nnot an assignment\n", encoding="utf-8")

    assert main([str(env_file)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""


def test_shell_exports_supports_export_prefix_and_quotes(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text('export FOO="hello world"\nBAR=plain\n', encoding="utf-8")

    exports = shell_exports(env_file)

    assert "export FOO='hello world'" in exports
    assert "export BAR=plain" in exports
