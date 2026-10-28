"""The rule catalogue and docs/rules.md stay in step (brief guardrail 3)."""

import re
from pathlib import Path

from waterforge.rules.catalogue import RULES

DOC = (Path(__file__).parents[2] / "docs" / "rules.md").read_text()


def test_every_rule_has_a_section_and_a_table_row():
    for rule in RULES.values():
        assert f"\n### {rule.id}\n" in DOC, f"docs/rules.md has no section for {rule.id}"
        row = re.search(rf"^\| \[{rule.id}\]\(#{rule.id.lower()}\) \| (.*)$", DOC, re.M)
        assert row, f"docs/rules.md's table has no row for {rule.id}"
        cells = [c.strip() for c in row.group(1).split("|")]
        assert cells[2] == rule.level, rule.id
        assert cells[3].lower() == rule.severity, rule.id
        assert ("(planned)" in cells[0]) == (not rule.implemented), rule.id


def test_no_section_for_an_unknown_rule():
    for rule_id in re.findall(r"^### (W[EG]-\d\d)$", DOC, re.M):
        assert rule_id in RULES


def test_every_rule_has_a_source():
    for rule in RULES.values():
        assert rule.source and rule.family in ("analysis", "gis")
        assert rule.id.startswith("WE" if rule.family == "analysis" else "WG")
