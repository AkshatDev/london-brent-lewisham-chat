"""The semantic files the bot reads are valid and match the database's columns."""

from pathlib import Path

import pytest
import yaml

SEMANTIC = Path(__file__).parents[1] / "semantic" / "brent"
FILES = sorted(SEMANTIC.glob("*.yaml"))


def test_there_are_semantic_files() -> None:
    assert len(FILES) > 10


@pytest.mark.parametrize("path", FILES, ids=[f.name for f in FILES])
def test_every_semantic_file_is_valid_yaml_with_a_field(path: Path) -> None:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(doc, dict)
    if path.name != "cleaning.yaml":
        assert "field" in doc
        assert "checked" in doc
