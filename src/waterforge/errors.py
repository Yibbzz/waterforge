"""
Exceptions raised by WaterForge.

Catch WaterForgeError to handle anything the engine rejects. Every error
has a plain-language message; `guide` names the section of the docs that
explains the fix, where there is one.

    WaterForgeError
    ├── InputError (also a ValueError)     bad arguments, mapping or input data
    │   └── MappingError                   the field mapping has problems (all listed)
    └── EpanetError                        EPANET itself failed unexpectedly

Breaking a network rule is not an error: rule violations are issues,
reported as data (see issues.py), and decide the exit code.
"""

GUIDE = "rules.md"


class WaterForgeError(Exception):
    """
    Base class for every error WaterForge raises on purpose.

    `guide` is "<file>.md[#anchor]" in docs/ (a bare anchor means rules.md).
    `issues` lists problems one by one: {"feature": id or None, "message": text}.
    """

    def __init__(self, message: str, guide: str | None = None, issues: list | None = None):
        super().__init__(message)
        self.guide = guide if guide is None or ".md" in guide else f"{GUIDE}#{guide}"
        self.issues = list(issues or [])

    def __str__(self) -> str:
        message = super().__str__()
        if self.guide:
            message += f"\n(see docs/{self.guide})"
        return message


class InputError(WaterForgeError, ValueError):
    """An argument, the mapping or the input data can't be used."""


class MappingError(InputError):
    """The field mapping has one or more problems; all are listed."""

    def __init__(self, problems: list[str], source: str = "mapping"):
        self.problems = list(problems)
        super().__init__(
            f"{len(self.problems)} problem(s) in the {source}:\n  - "
            + "\n  - ".join(self.problems),
            guide="mapping.md",
            issues=[{"feature": None, "message": p} for p in self.problems],
        )


class EpanetError(WaterForgeError):
    """EPANET failed in a way that isn't a rule violation (a crash, a missing file)."""
