"""The semantic layer loads the Brent YAMLs, checks them, and builds the prompt text."""

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from london_brent_lewisham_chat.semantic import (
    RECORD_FLAGS,
    SemanticError,
    SemanticLayer,
    load_semantic,
)

SEMANTIC = Path(__file__).parents[1] / "semantic"
PROMPT_TOKEN_BUDGET = 4000  # ~4 characters per token; keeps each question affordable


@pytest.fixture(scope="module")
def brent() -> SemanticLayer:
    return load_semantic("brent", SEMANTIC)


@pytest.fixture
def copy(tmp_path: Path) -> Path:
    """A writable copy of the Brent folder, for breaking one file per test."""
    shutil.copytree(SEMANTIC / "brent", tmp_path / "brent")
    return tmp_path


def edit_yaml(path: Path, change: Callable[[dict[str, Any]], None]) -> None:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


# --- The real Brent folder ---------------------------------------------------------


def test_the_brent_folder_loads(brent: SemanticLayer) -> None:
    assert brent.borough == "brent"
    assert "status.yaml" in brent.fields
    assert brent.live.request_fields
    assert brent.cleaning_rules["derived"]


def test_every_returned_field_is_explained_by_an_existing_file(
    brent: SemanticLayer,
) -> None:
    for ref in brent.live.returned:
        assert (SEMANTIC / "brent" / ref.see).is_file(), ref
        if ref.see == RECORD_FLAGS:
            assert ref.field in brent.flags.flags, ref


def test_allowed_values_come_from_the_field_yaml(brent: SemanticLayer) -> None:
    listed = yaml.safe_load((SEMANTIC / "brent" / "decision.yaml").read_text())
    assert brent.allowed_values("decision") == tuple(listed["values"])
    with pytest.raises(KeyError, match="description"):
        brent.allowed_values("description")  # free text: no list of values


def test_the_prompt_leaves_out_rules_that_only_hold_in_the_database(
    brent: SemanticLayer,
) -> None:
    prompt = brent.prompt_context()
    # borough.yaml and address.yaml claim every record was checked by location:
    # true in the database (location_check), not for live records.
    assert "checked by location" not in prompt
    # "site" as a plain word is not the database-only site column: rule kept.
    assert "usually means the same site" in prompt
    # The live rule names ward_2022 on purpose and stays.
    assert "Live records have no ward_2022" in prompt


def test_the_prompt_carries_meanings_flags_live_rules_and_the_uncovered_line(
    brent: SemanticLayer,
) -> None:
    prompt = brent.prompt_context()
    for heading in ("## Fields", "## Flags on records", "## Live data rules"):
        assert heading in prompt
    assert '"Validated in <year>" means valid_date in that year' in prompt
    assert "- status_stale:" in prompt
    assert "say it is not covered yet rather than guess" in prompt
    assert "### location_check" not in prompt  # database-only file left out


def test_the_prompt_is_stable_and_within_budget(brent: SemanticLayer) -> None:
    prompt = brent.prompt_context()
    assert prompt == brent.prompt_context()
    assert len(prompt) / 4 < PROMPT_TOKEN_BUDGET


# --- Broken folders stop the app with a message naming the problem ---------------


def test_a_missing_borough_folder_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(SemanticError, match="semantic folder not found"):
        load_semantic("brent", tmp_path)


def test_a_missing_required_file_is_an_error(copy: Path) -> None:
    (copy / "brent" / "live_api.yaml").unlink()
    with pytest.raises(SemanticError, match="missing .*live_api.yaml"):
        load_semantic("brent", copy)


def test_a_file_that_is_not_valid_yaml_is_named(copy: Path) -> None:
    (copy / "brent" / "status.yaml").write_text("field: status\nmeaning: [unclosed\n")
    with pytest.raises(SemanticError, match="invalid .*status.yaml"):
        load_semantic("brent", copy)


def test_a_field_file_without_a_meaning_is_named(copy: Path) -> None:
    edit_yaml(copy / "brent" / "decision.yaml", lambda d: d.pop("meaning"))
    with pytest.raises(SemanticError, match="invalid .*decision.yaml"):
        load_semantic("brent", copy)


def test_a_typo_in_live_api_yaml_is_rejected(copy: Path) -> None:
    edit_yaml(copy / "brent" / "live_api.yaml", lambda d: d.update(uncoverd="x"))
    with pytest.raises(SemanticError, match="invalid .*live_api.yaml"):
        load_semantic("brent", copy)


def test_a_see_to_a_missing_file_is_an_error(copy: Path) -> None:
    def point_to_nowhere(data: dict[str, Any]) -> None:
        data["returned"][0]["see"] = "nowhere.yaml"

    edit_yaml(copy / "brent" / "live_api.yaml", point_to_nowhere)
    with pytest.raises(SemanticError, match="see nowhere.yaml does not exist"):
        load_semantic("brent", copy)


def test_a_filter_naming_an_unknown_flag_is_an_error(copy: Path) -> None:
    def unknown_flag(data: dict[str, Any]) -> None:
        data["default_filters"][0]["exclude_flags"] = ["no_such_flag"]

    edit_yaml(copy / "brent" / "live_api.yaml", unknown_flag)
    with pytest.raises(SemanticError, match="no rule 'no_such_flag'"):
        load_semantic("brent", copy)


def test_a_filter_naming_an_unknown_status_is_an_error(copy: Path) -> None:
    def unknown_status(data: dict[str, Any]) -> None:
        pending = next(f for f in data["default_filters"] if f["id"] == "pending")
        pending["status_in"] = ["Application Recieved"]  # typo

    edit_yaml(copy / "brent" / "live_api.yaml", unknown_status)
    with pytest.raises(SemanticError, match="'Application Recieved' not in status"):
        load_semantic("brent", copy)
