"""Validate utils from text module."""
from subprocess import CompletedProcess

from ansiblelint.runner import Runner

SYNTAX_CHECK_STDERR = """\
[WARNING]: No inventory was parsed, only implicit localhost is available
[WARNING]: provided hosts list is empty, only localhost is available. Note that the implicit localhost does not match 'all'
ERROR! conflicting action statements: debug, always_run

The error appears to be in '/foo/example.yml': line 47, column 7, but may
be elsewhere in the file depending on the exact syntax problem.

The offending line appears to be:


    - name: always run
      ^ here
"""  # noqa: E501


def test_runner_parse_ansible_syntax_check() -> None:
    """Validate parsing of ansible output."""
    run = CompletedProcess(
        [],
        returncode=4,
        stdout="",
        stderr=SYNTAX_CHECK_STDERR)
    result = Runner._parse_ansible_syntax_check(run)
    print(result[0])
    assert result[0].linenumber == 47
    assert result[0].column == 7
    assert result[0].message == "conflicting action statements: debug, always_run"
    # We internaly convert absolute paths returned by ansible into paths
    # relative to current directory.
    assert result[0].filename.endswith("/foo/example.yml")
