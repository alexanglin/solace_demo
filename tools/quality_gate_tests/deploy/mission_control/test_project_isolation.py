"""The mission-control dashboard reuses the default project's broker and durable store."""

from __future__ import annotations

import re

from tools.quality_gate_tests.support import REPOSITORY_ROOT


def _recipe(text: str, name: str) -> str:
    match = re.search(rf"(?m)^{re.escape(name)}(?: \*ARGS)?:\n(?P<body>(?:    [^\n]*\n)+)", text)
    if match is None:
        message = f"missing just recipe: {name}"
        raise ValueError(message)
    return match.group("body")


def test_every_mission_control_recipe_reuses_the_compose_project() -> None:
    # Arrange
    text = (REPOSITORY_ROOT / "justfile").read_text(encoding="utf-8")
    recipe_names = (
        "mission-control-up",
        "mission-control-down",
        "mission-control-logs",
        "mission-control-ps",
    )

    # Act
    compose = (REPOSITORY_ROOT / "deploy" / "compose.yaml").read_text(encoding="utf-8")
    bodies = tuple(_recipe(text, name) for name in recipe_names)

    # Assert
    assert re.search(r"(?m)^name: aerial-rescue-mesh$", compose) is not None
    assert "mission_control_project" not in text
    assert all("--project-name" not in body for body in bodies)
    assert "--project-name" not in _recipe(text, "up")
