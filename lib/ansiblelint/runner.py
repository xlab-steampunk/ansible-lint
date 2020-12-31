"""Runner implementation."""
import logging
import os
import re
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, FrozenSet, Generator, List, Optional, Set

import ansiblelint.file_utils
import ansiblelint.skip_utils
from ansiblelint._internal.rules import BaseRule, LoadingFailureRule, RuntimeErrorRule
from ansiblelint.errors import MatchError
from ansiblelint.rules.AnsibleSyntaxCheckRule import AnsibleSyntaxCheckRule
from ansiblelint.text import strip_ansi_escape
from ansiblelint.utils import find_children

if TYPE_CHECKING:
    from ansiblelint.rules import RulesCollection

    # https://github.com/PyCQA/pylint/issues/3240
    # pylint: disable=unsubscriptable-object
    CompletedProcess = subprocess.CompletedProcess[Any]
else:
    CompletedProcess = subprocess.CompletedProcess


_logger = logging.getLogger(__name__)


@dataclass
class LintResult:
    """Class that tracks result of linting."""

    matches: List[MatchError]
    files: Set[str]


class Runner(object):
    """Runner class performs the linting process."""

    _ansible_syntax_check_re = re.compile(
        r"^ERROR! (?P<title>[^\n]*)\n\nThe error appears to be in "
        r"'(?P<filename>.*)': line (?P<line>\d+), column (?P<column>\d+)",
        re.MULTILINE | re.S | re.DOTALL)

    def __init__(
            self,
            rules: "RulesCollection",
            playbook: str,
            tags: FrozenSet[Any] = frozenset(),
            skip_list: Optional[FrozenSet[Any]] = frozenset(),
            exclude_paths: List[str] = [],
            verbosity: int = 0,
            checked_files: Optional[Set[str]] = None) -> None:
        """Initialize a Runner instance."""
        self.rules = rules
        self.playbooks = set()
        # assume role if directory
        if os.path.isdir(playbook):
            self.playbooks.add((os.path.join(playbook, ''), 'role'))
            self.playbook_dir = playbook
        else:
            self.playbook_dir = os.path.dirname(playbook)
            if playbook.endswith("meta/main.yml"):
                file_type = "meta"
            else:
                file_type = "playbook"
            self.playbooks.add((playbook, file_type))
        self.tags = tags
        self.skip_list = skip_list
        self._update_exclude_paths(exclude_paths)
        self.verbosity = verbosity
        if checked_files is None:
            checked_files = set()
        self.checked_files = checked_files

    def _update_exclude_paths(self, exclude_paths: List[str]) -> None:
        if exclude_paths:
            # These will be (potentially) relative paths
            paths = ansiblelint.file_utils.expand_paths_vars(exclude_paths)
            # Since ansiblelint.utils.find_children returns absolute paths,
            # and the list of files we create in `Runner.run` can contain both
            # relative and absolute paths, we need to cover both bases.
            self.exclude_paths = paths + [os.path.abspath(p) for p in paths]
        else:
            self.exclude_paths = []

    def is_excluded(self, file_path: str) -> bool:
        """Verify if a file path should be excluded."""
        # Any will short-circuit as soon as something returns True, but will
        # be poor performance for the case where the path under question is
        # not excluded.
        return any(file_path.startswith(path) for path in self.exclude_paths)

    def run(self) -> List[MatchError]:
        """Execute the linting process."""
        files = list()
        for playbook in self.playbooks:
            if self.is_excluded(playbook[0]) or playbook[1] == 'role':
                continue
            files.append({'path': ansiblelint.file_utils.normpath(playbook[0]),
                          'type': playbook[1],
                          # add an absolute path here, so rules are able to validate if
                          # referenced files exist
                          'absolute_directory': os.path.dirname(playbook[0])})
        matches = set(self._emit_matches(files))

        # remove duplicates from files list
        files = [value for n, value in enumerate(files) if value not in files[:n]]

        # remove files that have already been checked
        files = [x for x in files if x['path'] not in self.checked_files]
        for file in files:
            _logger.debug(
                "Examining %s of type %s",
                ansiblelint.file_utils.normpath(file['path']),
                file['type'])

            # we should bother checking playbooks only if they pass Ansible syntax-check
            if file['type'] == 'playbook':
                result = subprocess.run(
                    ['ansible-playbook', '--syntax-check', file['path']],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    shell=False,  # needed when command is a list
                    universal_newlines=True,
                    check=False
                )
                if result.returncode != 0:

                    matches = matches.union(
                        self._parse_ansible_syntax_check(result))
                    # continue
                    # For the moment we do not jump at next file, but in the
                    # future we would likely want to avoid running internal
                    # parsing if ansible-check fails.

            matches = matches.union(
                self.rules.run(file, tags=set(self.tags),
                               skip_list=self.skip_list))
        # update list of checked files
        self.checked_files.update([x['path'] for x in files])

        return sorted(matches)

    def _emit_matches(self, files: List) -> Generator[MatchError, None, None]:
        visited: Set = set()
        while visited != self.playbooks:
            for arg in self.playbooks - visited:
                try:
                    for child in find_children(arg, self.playbook_dir):
                        if self.is_excluded(child['path']):
                            continue
                        self.playbooks.add((child['path'], child['type']))
                        files.append(child)
                except MatchError as e:
                    e.rule = LoadingFailureRule()
                    yield e
                visited.add(arg)

    @staticmethod
    def _parse_ansible_syntax_check(run: CompletedProcess) -> List[MatchError]:
        """Convert ansible syntax check output into a list of MatchError(s)."""
        result = []
        if run.returncode != 0:
            message = None
            filename = None
            linenumber = 0
            column = None

            stderr = strip_ansi_escape(run.stderr)
            stdout = strip_ansi_escape(run.stdout)
            if stderr:
                details = stderr
                if stdout:
                    details += "\n" + stdout
            else:
                details = stdout

            m = Runner._ansible_syntax_check_re.search(stderr)
            if m:
                message = m.groupdict()['title']
                # Ansible returns absolute paths
                filename = m.groupdict()['filename']
                linenumber = int(m.groupdict()['line'])
                column = int(m.groupdict()['column'])

            if run.returncode == 4:
                rule: BaseRule = AnsibleSyntaxCheckRule()
            else:
                rule = RuntimeErrorRule()
                if not message:
                    message = (
                        "Ansible --syntax-check reported unexpected "
                        f"error code {run.returncode}")

            result.append(MatchError(
                message=message,
                filename=filename,
                linenumber=linenumber,
                column=column,
                rule=rule,
                details=details
                ))
        return result
