"""Unit tests for the command-line option extractor."""

from __future__ import annotations

from pathlib import Path

import pytest

from scriptsuite.options import (
    ProgramInfo,
    build_arguments,
    describe,
    split_arguments,
)


def write(tmp_path: Path, source: str, name: str = "s.py") -> Path:
    path = tmp_path / name
    path.write_text(source)
    return path


# -- argparse ---------------------------------------------------------------

ARGPARSE = """
import argparse

def build():
    p = argparse.ArgumentParser(
        prog="tool.py", description="Does the thing.",
    )
    p.add_argument("target", help="where to go")
    p.add_argument("-v", "--verbose", action="store_true", help="chatty output")
    p.add_argument("-n", "--count", type=int, default=3, help="how many times")
    p.add_argument("--mode", choices=["fast", "safe"], help="how to go")
    p.add_argument("--tag", action="append", help="label it")
    p.add_argument("--out", metavar="FILE", help="where to write")
    p.add_argument("--must", required=True, help="non negotiable")
    return p
"""


@pytest.fixture
def argparse_info(tmp_path: Path) -> ProgramInfo:
    return describe(write(tmp_path, ARGPARSE), "python")


def test_detects_argparse(argparse_info: ProgramInfo):
    assert argparse_info.detected == "argparse"


def test_program_description(argparse_info: ProgramInfo):
    assert argparse_info.summary == "Does the thing."


def test_positional_counted(argparse_info: ProgramInfo):
    assert argparse_info.positional_count == 1
    assert argparse_info.positional_hint == "takes 1 positional argument"


def test_every_option_found(argparse_info: ProgramInfo):
    labels = {option.label for option in argparse_info.options}
    assert labels == {
        "-v, --verbose",
        "-n, --count",
        "--mode",
        "--tag",
        "--out",
        "--must",
    }


def test_help_text_carried(argparse_info: ProgramInfo):
    verbose = next(o for o in argparse_info.options if "--verbose" in o.flags)
    assert verbose.help == "chatty output"


def test_store_true_is_a_flag(argparse_info: ProgramInfo):
    verbose = next(o for o in argparse_info.options if "--verbose" in o.flags)
    assert verbose.takes_value is False
    assert verbose.value_hint == ""


def test_plain_option_takes_a_value(argparse_info: ProgramInfo):
    count = next(o for o in argparse_info.options if "--count" in o.flags)
    assert count.takes_value is True
    assert count.value_hint == "VALUE"


def test_metavar_is_used_as_the_hint(argparse_info: ProgramInfo):
    out = next(o for o in argparse_info.options if "--out" in o.flags)
    assert out.value_hint == "FILE"


def test_choices_are_shown(argparse_info: ProgramInfo):
    mode = next(o for o in argparse_info.options if "--mode" in o.flags)
    assert mode.value_hint == "{fast|safe}"


def test_append_is_repeatable(argparse_info: ProgramInfo):
    tag = next(o for o in argparse_info.options if "--tag" in o.flags)
    assert tag.repeatable is True


def test_required_is_read_as_a_boolean(argparse_info: ProgramInfo):
    # `required=False` must not be read as the truthy string "False".
    for option in argparse_info.options:
        if "--must" in option.flags:
            assert option.required is True
        else:
            assert option.required is False


def test_nargs_zero_is_a_flag(tmp_path: Path):
    info = describe(
        write(
            tmp_path,
            "import argparse\n"
            "p = argparse.ArgumentParser()\n"
            'p.add_argument("--shout", action="store_const", const=True, nargs=0)\n',
        ),
        "python",
    )
    assert info.options[0].takes_value is False


def test_positionals_do_not_become_options(tmp_path: Path):
    info = describe(
        write(
            tmp_path,
            "import argparse\n"
            "p = argparse.ArgumentParser()\n"
            'p.add_argument("one")\n'
            'p.add_argument("two")\n',
        ),
        "python",
    )
    assert info.options == ()
    assert info.positional_count == 2


def test_python_without_argparse_reports_nothing(tmp_path: Path):
    # A "--x" inside a string is not an option.
    info = describe(
        write(
            tmp_path,
            "import sys\n"
            "def main():\n"
            '    print("--not-an-option"))\n'
            "    return sys.argv[0]\n",
            "plain.py",
        ),
        "python",
    )
    assert info.options == ()
    assert info.detected == "none"


def test_syntax_error_is_not_fatal(tmp_path: Path):
    info = describe(write(tmp_path, "def (((:\n"), "python")
    assert info.options == ()


# -- shell / expect ---------------------------------------------------------


def test_getopts_is_decoded(tmp_path: Path):
    info = describe(
        write(tmp_path, 'while getopts ":ab:c" opt; do echo "$opt"; done\n', "s.sh"),
        "shell",
    )
    by_flag = {option.flags[0]: option for option in info.options}
    assert set(by_flag) == {"-a", "-b", "-c"}
    assert by_flag["-a"].takes_value is False
    assert by_flag["-b"].takes_value is True


def test_undocumented_shell_flags_are_not_invented(tmp_path: Path):
    # `wc -c` and `set -u` are not this script's options.
    info = describe(
        write(tmp_path, 'set -euo pipefail\nbytes=$(wc -c <"$f")\n', "s.sh"),
        "shell",
    )
    assert info.options == ()


def test_documented_long_flag_is_kept(tmp_path: Path):
    info = describe(
        write(
            tmp_path,
            '# usage: tool.sh --output FILE --force\nrun --output "$f" --force\n',
            "s.sh",
        ),
        "shell",
    )
    assert {option.flags[0] for option in info.options} == {"--output", "--force"}


def test_shell_positional_is_detected(tmp_path: Path):
    info = describe(write(tmp_path, 'echo "$1" "$2"\n', "s.sh"), "shell")
    assert info.positional_count == 2


def test_tcl_argv_list_is_positional(tmp_path: Path):
    info = describe(
        write(tmp_path, 'set a [lindex $argv 0]\nputs "got $a"\n', "s.exp"),
        "expect",
    )
    assert info.positional_count == 1
    assert info.takes_positional


def test_expect_named_argument_index(tmp_path: Path):
    info = describe(write(tmp_path, "spawn ssh $argv(1)\nwait\n", "s.exp"), "expect")
    assert info.positional_count == 1


def test_usage_line_is_captured(tmp_path: Path):
    info = describe(
        write(tmp_path, "# usage: s.exp <host> <port>\nspawn $host\n", "s.exp"),
        "expect",
    )
    assert "s.exp" in info.usage


# -- Tcl / expect: cmdline::getoptions ---------------------------------------

CMDLINE = """#!/usr/bin/expect -f

# Arguments
# -host => Host IP
# -debug => debug flag

package require cmdline

set parameters {
    {host.arg "" "Target host"}
    {user.arg "" "Login user"}
    {pass.arg "" "Password"}
    {debug    "Enable debug output"}
}

array set arg [cmdline::getoptions argv $parameters]
"""


@pytest.fixture
def cmdline_info(tmp_path: Path) -> ProgramInfo:
    return describe(write(tmp_path, CMDLINE, "s.exp"), "expect")


def test_cmdline_is_detected(cmdline_info: ProgramInfo):
    assert cmdline_info.detected == "cmdline"


def test_cmdline_flags_use_a_double_dash(cmdline_info: ProgramInfo):
    # `expect` reserves single-dash options for itself, so the package is
    # always called with `--name`.
    assert {option.flags[0] for option in cmdline_info.options} == {
        "--host",
        "--user",
        "--pass",
        "--debug",
    }


def test_dot_arg_type_takes_a_value(cmdline_info: ProgramInfo):
    host = next(o for o in cmdline_info.options if "--host" in o.flags)
    assert host.takes_value is True
    assert host.help == "Target host"


def test_bare_name_is_a_switch(cmdline_info: ProgramInfo):
    debug = next(o for o in cmdline_info.options if "--debug" in o.flags)
    assert debug.takes_value is False
    assert debug.help == "Enable debug output"


def test_cmdline_dash_typed_specs(tmp_path: Path):
    info = describe(
        write(
            tmp_path,
            "package require cmdline\n"
            "set p {\n"
            '    {verbose -flag 0 "be loud"}\n'
            '    {count -integer 1 "how many"}\n'
            "}\n"
            "array set arg [cmdline::getoptions argv $p]\n",
            "s.exp",
        ),
        "expect",
    )
    by_flag = {option.flags[0]: option for option in info.options}
    assert by_flag["--verbose"].takes_value is False
    assert by_flag["--count"].takes_value is True
    assert by_flag["--count"].default == "1"


def test_cmdline_default_and_description(tmp_path: Path):
    info = describe(
        write(
            tmp_path,
            "package require cmdline\n"
            "set p {\n"
            '    {level.text "info" "severity"}\n'
            "}\n"
            "array set arg [cmdline::getoptions argv $p]\n",
            "s.exp",
        ),
        "expect",
    )
    assert info.options[0].default == "info"
    assert info.options[0].help == "severity"


def test_nested_braces_in_a_block_are_survived(tmp_path: Path):
    info = describe(
        write(
            tmp_path,
            "package require cmdline\n"
            "set p {\n"
            '    {a.arg "" "first"}\n'
            '    {b.arg "" "second"}\n'
            "}\n"
            "array set arg [cmdline::getoptions argv $p]\n",
            "s.exp",
        ),
        "expect",
    )
    assert [o.flags[0] for o in info.options] == ["--a", "--b"]


def test_script_without_cmdline_is_unaffected(tmp_path: Path):
    info = describe(write(tmp_path, "puts {hello}\n", "plain.exp"), "expect")
    assert info.detected == "none"
    assert info.options == ()


# -- fields -----------------------------------------------------------------


def test_fields_pair_positionals_then_options(argparse_info: ProgramInfo):
    # A positional is emitted bare, an option with its flag.
    assert [f.token for f in argparse_info.fields()] == [
        "target",
        "--verbose",
        "--count",
        "--mode",
        "--tag",
        "--out",
        "--must",
    ]


def test_positional_field_carries_the_metavar(argparse_info: ProgramInfo):
    field = argparse_info.fields()[0]
    assert field.positional is True
    assert field.kind == "value"
    assert field.token == "target"
    assert field.id == "pos0"


def test_flag_field_has_no_placeholder(argparse_info: ProgramInfo):
    verbose = next(f for f in argparse_info.fields() if f.token == "--verbose")
    assert verbose.kind == "flag"
    assert verbose.placeholder == ""


def test_value_field_shows_its_default(argparse_info: ProgramInfo):
    count = next(f for f in argparse_info.fields() if f.token == "--count")
    assert "default 3" in count.placeholder


def test_emit_flag_prefers_the_long_form():
    from scriptsuite.options import Option

    assert Option(flags=("-v", "--verbose")).emit_flag == "--verbose"
    assert Option(flags=("-v",)).emit_flag == "-v"


def test_unnamed_positionals_still_get_fields(tmp_path: Path):
    # A script that indexes sys.argv without argparse: counted, not named.
    info = describe(
        write(tmp_path, "import sys\nprint(sys.argv[1], sys.argv[2])\n"),
        "python",
    )
    fields = info.fields()
    assert [f.token for f in fields] == ["arg 1", "arg 2"]
    assert all(f.positional for f in fields)


def test_tcl_argv_script_gets_a_positional_field(tmp_path: Path):
    info = describe(
        write(tmp_path, "set a [lindex $argv 0]\nputs $a\n", "s.exp"), "expect"
    )
    assert [f.token for f in info.fields()] == ["arg 1"]


# -- building the command line ----------------------------------------------


def test_build_arguments_orders_positionals_first(cmdline_info: ProgramInfo):
    tokens, problem = build_arguments(
        cmdline_info,
        {"--host": "10.0.0.5", "--user": "root", "--pass": "s3", "--debug": True},
    )
    assert tokens == ["--host", "10.0.0.5", "--user", "root", "--pass", "s3", "--debug"]
    assert problem is None


def test_build_arguments_omits_empty_fields(cmdline_info: ProgramInfo):
    tokens, _ = build_arguments(cmdline_info, {"--host": "10.0.0.5", "--user": ""})
    assert tokens == ["--host", "10.0.0.5"]


def test_build_arguments_omits_untoggled_flags(cmdline_info: ProgramInfo):
    tokens, _ = build_arguments(cmdline_info, {"--debug": False})
    assert tokens == []


def test_build_arguments_appends_extra_tokens(cmdline_info: ProgramInfo):
    tokens, _ = build_arguments(cmdline_info, {"--debug": True}, "--other thing")
    assert tokens == ["--debug", "--other", "thing"]


def test_build_arguments_reports_bad_quoting(cmdline_info: ProgramInfo):
    tokens, problem = build_arguments(cmdline_info, {}, '--out "unclosed')
    assert problem is not None
    assert tokens == ["--out", '"unclosed']


def test_build_arguments_for_positional_script(tmp_path: Path):
    info = describe(write(tmp_path, "import sys\nprint(sys.argv[1])\n"), "python")
    tokens, _ = build_arguments(info, {"pos0": "hello"})
    assert tokens == ["hello"]


def test_build_arguments_with_no_fields(tmp_path: Path):
    info = describe(write(tmp_path, "print('hi')\n"), "python")
    tokens, _ = build_arguments(info, {}, "-x")
    assert tokens == ["-x"]


# -- behaviour --------------------------------------------------------------


def test_describe_survives_a_missing_file(tmp_path: Path):
    info = describe(tmp_path / "gone.py", "python")
    assert info.options == ()


def test_describe_is_cached(tmp_path: Path):
    path = write(tmp_path, ARGPARSE)
    assert describe(path, "python") is describe(path, "python")


def test_describe_refreshes_when_the_file_changes(tmp_path: Path):
    path = write(
        tmp_path, 'import argparse\np=argparse.ArgumentParser()\np.add_argument("--a")\n'
    )
    assert len(describe(path, "python").options) == 1

    import os
    import time

    time.sleep(0.01)
    path.write_text(
        'import argparse\np=argparse.ArgumentParser()\np.add_argument("--a")\np.add_argument("--b")\n'
    )
    os.utime(path, (time.time() + 1, time.time() + 1))
    assert len(describe(path, "python").options) == 2


# -- argument splitting -----------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", []),
        ("   ", []),
        ("-v", ["-v"]),
        ("-v --mode audit", ["-v", "--mode", "audit"]),
        ('--out "my file.txt"', ["--out", "my file.txt"]),
        ("--name 'a b' c", ["--name", "a b", "c"]),
    ],
)
def test_split_arguments(text, expected):
    tokens, problem = split_arguments(text)
    assert tokens == expected
    assert problem is None


def test_split_arguments_survives_broken_quoting():
    tokens, problem = split_arguments('--out "unclosed')
    assert tokens  # still usable, so the run is not blocked
    assert problem is not None
