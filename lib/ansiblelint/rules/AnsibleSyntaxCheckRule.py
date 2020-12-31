"""Rule definition for ansible syntax check."""

from ansiblelint.rules import AnsibleLintRule


class AnsibleSyntaxCheckRule(AnsibleLintRule):
    """Ansible syntax check report failure."""

    id = "911"
    shortdesc = "Ansible syntax check failed"
    description = "Running ansible-playbook --syntax-check ... reported an error."
    severity = "VERY_HIGH"
    tags = ["core"]
    version_added = "v5.0.0"
