"""Tests for the command line entry point."""

from __future__ import annotations

from pathlib import Path

import pytest

from scriptsuite.cli import main


@pytest.fixture
def scripts(tmp_path: Path) -> Path:
    root = tmp_path / "script"
    root.mkdir()
    (root / "alpha.py").write_text('"""Alpha does a thing."""\nprint(1)\n')
    (root / "note.txt").write_text("not a script\n")
    nested = root / "unit"
    nested.mkdir()
    (nested / "test_alpha.py").write_text("import pytest\n\ndef test_x():\n    pass\n")
    return root


def test_version_prints_and_exits(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        main(["--version"])
    assert raised.value.code == 0
    assert "scriptsuite" in capsys.readouterr().out


def test_list_prints_scripts(scripts: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--list", str(scripts)]) == 0
    output = capsys.readouterr().out
    assert "alpha.py" in output
    assert "unit/test_alpha.py" in output
    assert "Alpha does a thing." in output
    assert "note.txt" not in output


def test_list_missing_folder(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--list", str(tmp_path / "gone")]) == 2
    assert "no such folder" in capsys.readouterr().err


def test_list_empty_folder(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "script"
    root.mkdir()
    assert main(["--list", str(root)]) == 1
    error = capsys.readouterr().err
    assert "no runnable scripts" in error
    assert "recognised extensions" in error


def test_no_recursive_limits_list(
    scripts: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--list", "--no-recursive", str(scripts)]) == 0
    output = capsys.readouterr().out
    assert "alpha.py" in output
    assert "test_alpha.py" not in output


def test_env_var_is_used(
    scripts: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SCRIPTSUITE_SCRIPTS_DIR", str(scripts))
    assert main(["--list"]) == 0
    assert "alpha.py" in capsys.readouterr().out


def test_no_recursive_reaches_the_app(
    scripts: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    class FakeApp:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def run(self) -> None:
            captured["ran"] = True

    monkeypatch.setattr("scriptsuite.app.ScriptSuiteApp", FakeApp)
    assert main(["--no-recursive", str(scripts)]) == 0
    assert captured["recursive"] is False
    assert captured["ran"] is True
    assert captured["scripts_dir"] == scripts.resolve()


def test_python_override_reaches_the_app(
    scripts: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    class FakeApp:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def run(self) -> None:
            pass

    monkeypatch.setattr("scriptsuite.app.ScriptSuiteApp", FakeApp)
    assert main(["--python", "/opt/python", str(scripts)]) == 0
    assert captured["python"] == "/opt/python"


def test_python_env_var_is_used(scripts: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeApp:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def run(self) -> None:
            pass

    monkeypatch.setenv("SCRIPTSUITE_PYTHON", "/opt/env-python")
    monkeypatch.setattr("scriptsuite.app.ScriptSuiteApp", FakeApp)
    assert main([str(scripts)]) == 0
    assert captured["python"] == "/opt/env-python"
