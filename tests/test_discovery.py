"""Unit tests for script discovery."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from scriptsuite.discovery import ScriptInfo, discover, resolve_scripts_dir


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "script"
    root.mkdir()

    (root / "alpha.py").write_text('"""Alpha does a thing."""\nprint(1)\n')
    (root / "beta.sh").write_text("#!/usr/bin/env bash\n# Beta checks things.\necho hi\n")
    (root / "plain.py").write_text("x = 1\n")

    sub = root / "unit"
    sub.mkdir()
    (sub / "test_thing.py").write_text(
        "import unittest\n\n\nclass T(unittest.TestCase):\n    pass\n"
    )
    (sub / "helper.py").write_text('"""A helper."""\n')

    noise = root / "__pycache__"
    noise.mkdir()
    (noise / "cached.py").write_text("raise SystemExit\n")

    hidden = root / ".hidden.py"
    hidden.write_text("raise SystemExit\n")

    (root / "notes.txt").write_text("not a script\n")
    (root / "runner").write_text("#!/usr/bin/env python3\nprint('no extension')\n")

    return root


def by_name(scripts: list[ScriptInfo]) -> dict[str, ScriptInfo]:
    return {script.rel_path: script for script in scripts}


# -- discovery --------------------------------------------------------------


def test_finds_every_runnable_script(tree: Path):
    found = by_name(discover(tree))
    assert set(found) == {
        "alpha.py",
        "beta.sh",
        "plain.py",
        os.path.join("unit", "test_thing.py"),
        os.path.join("unit", "helper.py"),
        "runner",
    }


def test_skips_noise_directories_and_non_scripts(tree: Path):
    found = by_name(discover(tree))
    assert not any("__pycache__" in path for path in found)
    assert ".hidden.py" not in found
    assert "notes.txt" not in found


def test_sorted_by_path(tree: Path):
    paths = [script.rel_path for script in discover(tree)]
    assert paths == sorted(paths, key=str.lower)


def test_non_recursive_stays_shallow(tree: Path):
    paths = [script.rel_path for script in discover(tree, recursive=False)]
    assert not any("unit" in path for path in paths)


def test_unknown_extension_with_a_shebang_is_still_found(tmp_path: Path):
    # A `.exp` script is the common case, but the rule is general: the
    # shebang says how to run it, so the extension does not get a veto.
    root = tmp_path / "script"
    root.mkdir()
    (root / "chat.exp").write_text('#!/usr/bin/expect -f\nputs "hi"\n')
    (root / "runner.weird").write_text("#!/bin/sh\necho hi\n")
    (root / "data.weird").write_text("no shebang here\n")

    found = by_name(discover(root))
    assert set(found) == {"chat.exp", "runner.weird"}
    assert found["chat.exp"].command[:2] == ("/usr/bin/expect", "-f")
    assert Path(found["runner.weird"].command[0]).name == "sh"


def test_expect_scripts_run_without_a_shebang(tmp_path: Path):
    root = tmp_path / "script"
    root.mkdir()
    (root / "dialog.exp").write_text('puts "hello"\n')
    found = discover(root)[0]
    assert found.language == "expect"
    assert found.command == ("expect", str(root / "dialog.exp"))


def test_tcl_scripts_use_tclsh(tmp_path: Path):
    root = tmp_path / "script"
    root.mkdir()
    (root / "lib.tcl").write_text("proc hi {} { return hi }\n")
    assert discover(root)[0].command == ("tclsh", str(root / "lib.tcl"))


def test_skipped_files_are_collected_for_the_empty_state(tmp_path: Path):
    root = tmp_path / "script"
    root.mkdir()
    (root / "run_case").write_text("no shebang\n")
    (root / "notes.txt").write_text("prose\n")
    skipped: list[str] = []
    discover(root, collect_skipped=skipped)
    # Extensionless files are reported, data files are not.
    assert skipped == ["run_case"]


def test_data_files_are_not_offered_as_skipped_scripts(tmp_path: Path):
    root = tmp_path / "script"
    root.mkdir()
    (root / "notes.txt").write_text("x")
    (root / "config.yaml").write_text("x")
    skipped: list[str] = []
    discover(root, collect_skipped=skipped)
    assert skipped == []


def test_known_extensions_are_reported():
    from scriptsuite.discovery import known_extensions

    listed = known_extensions()
    assert ".py" in listed and ".exp" in listed and ".sh" in listed


def test_missing_directory_is_empty(tmp_path: Path):
    assert discover(tmp_path / "nope") == []


# -- metadata ---------------------------------------------------------------


def test_docstring_becomes_the_description(tree: Path):
    assert by_name(discover(tree))["alpha.py"].description == "Alpha does a thing."


def test_comment_becomes_the_description_for_shell(tree: Path):
    assert by_name(discover(tree))["beta.sh"].description == "Beta checks things."


def test_language_detected_from_extension(tree: Path):
    found = by_name(discover(tree))
    assert found["alpha.py"].language == "python"
    assert found["beta.sh"].language == "shell"


def test_shebang_detected_when_there_is_no_extension(tree: Path):
    assert by_name(discover(tree))["runner"].language == "python"


def test_test_detection_from_imports_and_names(tree: Path):
    found = by_name(discover(tree))
    assert found[os.path.join("unit", "test_thing.py")].is_test is True
    assert found["alpha.py"].is_test is False


def test_command_uses_the_running_interpreter_for_python(tree: Path):
    script = by_name(discover(tree))["alpha.py"]
    assert script.command[0] == os.sys.executable
    assert script.command[1].endswith("alpha.py")


def test_command_honours_an_explicit_python(tree: Path):
    script = by_name(discover(tree, python="/usr/bin/python3.13"))["alpha.py"]
    assert script.command[0] == "/usr/bin/python3.13"


def test_python_override_replaces_a_shebang_interpreter(tmp_path: Path):
    root = tmp_path / "script"
    root.mkdir()
    script = root / "sheb.py"
    script.write_text("#!/usr/bin/env python2\nprint(1)\n")
    found = discover(root, python="/usr/bin/python3.13")
    assert found[0].command[0] == "/usr/bin/python3.13"


def test_shebang_wins_over_the_extension(tree: Path):
    # beta.sh declares `#!/usr/bin/env bash`, so bash runs it, not the kernel.
    command = by_name(discover(tree))["beta.sh"].command
    assert Path(command[0]).name == "bash"
    assert command[1].endswith("beta.sh")


def test_non_executable_shell_script_still_runs(tmp_path: Path):
    root = tmp_path / "script"
    root.mkdir()
    script = root / "no_shebang.sh"
    script.write_text("echo hi\n")
    script.chmod(0o644)  # no exec bit, no shebang
    command = discover(root)[0].command
    assert command[0] == "sh"
    assert command[1].endswith("no_shebang.sh")


def test_preview_command_is_shortened(tree: Path):
    preview = by_name(discover(tree))["alpha.py"].preview_command()
    assert preview.startswith("python") or preview.startswith("python3")
    assert "/" not in preview.split()[0]
    assert "alpha.py" in preview


def test_executable_bit_is_recorded(tree: Path):
    root_scripts = by_name(discover(tree))
    assert root_scripts["beta.sh"].executable is False
    beta = tree / "beta.sh"
    beta.chmod(beta.stat().st_mode | stat.S_IXUSR)
    assert by_name(discover(tree))["beta.sh"].executable is True


def test_syntax_error_does_not_break_discovery(tmp_path: Path):
    root = tmp_path / "script"
    root.mkdir()
    (root / "broken.py").write_text("def (((:\n")
    (root / "fine.py").write_text('"""Fine."""\n')
    found = by_name(discover(root))
    assert set(found) == {"broken.py", "fine.py"}
    assert found["fine.py"].description == "Fine."


def test_empty_directory(tmp_path: Path):
    root = tmp_path / "script"
    root.mkdir()
    assert discover(root) == []


# -- resolve_scripts_dir ----------------------------------------------------


def test_explicit_directory_wins(tmp_path: Path):
    assert resolve_scripts_dir(tmp_path) == tmp_path.resolve()


def test_env_var_is_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SCRIPTSUITE_SCRIPTS_DIR", str(tmp_path))
    assert resolve_scripts_dir() == tmp_path.resolve()


def test_conventional_folder_is_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("SCRIPTSUITE_SCRIPTS_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "script").mkdir()
    assert resolve_scripts_dir() == (tmp_path / "script").resolve()


def test_missing_folder_still_resolves(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("SCRIPTSUITE_SCRIPTS_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    assert resolve_scripts_dir() == (tmp_path / "script").resolve()
