"""The semantic layer: the borough's YAMLs, loaded once and checked, for the bot.

The YAMLs in ``semantic/<borough>/`` are the single source of truth for what fields
mean, which values exist, which rules answers follow and how to cite. This module
reads them at start-up into typed models and hands the app exactly what it needs:
the prompt text, the allowed values for tool arguments, the live-API settings and
the cleaning rules. Nothing here is typed again by hand; an invalid or inconsistent
file stops the app with a message naming the file.
"""

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from .pipeline.clean import load_rules
from .scope import Borough

CLEANING = "cleaning.yaml"
LIVE_API = "live_api.yaml"
SOURCES = "sources.yaml"
SPECIAL_FILES = {CLEANING, LIVE_API, SOURCES}
"""Files with their own shape; every other YAML in the folder describes one field."""


class SemanticError(ValueError):
    """A semantic file is missing, invalid, or inconsistent with the others."""


class _Doc(BaseModel):
    """Base for the field files: keys meant for people are ignored, not rejected."""

    model_config = ConfigDict(extra="ignore", frozen=True)


class KnownIssue(_Doc):
    """A data problem found in the EDA (``known_issues`` in a field file)."""

    id: str
    description: str
    count: int | None = None


class FieldDoc(_Doc):
    """What one field means and the rules answers about it follow."""

    field: str
    meaning: str
    rules: list[str] = []
    values: list[str] = []
    known_issues: list[KnownIssue] = []


class PortalGroup(_Doc):
    """One group of records a person checked on the council's planning portal."""

    ids: list[str]
    checked: list[str] = []
    """When only some ``ids`` were read on the portal; the rest match by value."""
    datahub_had: str | None = None
    portal_says: str | None = None


class Source(_Doc):
    """One source in ``sources.yaml`` and how to cite it."""

    name: str
    cite_as: str
    url: str | None = None
    groups: dict[str, PortalGroup] = {}
    """For a manual portal check: the records it covers, by group."""


class Sources(_Doc):
    """``sources.yaml``: where every value comes from and how to cite it."""

    sources: dict[str, Source]
    rules: list[str] = []


class _Live(BaseModel):
    """Base for ``live_api.yaml``: written for code, so unknown keys are typos."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class FieldRef(_Live):
    """A field and the file that explains it."""

    field: str
    see: str


class LiveRule(_Live):
    """A rule that differs when the bot reads the live API."""

    id: str
    rule: str


class DefaultFilter(_Live):
    """A default filter for one kind of question (``default_filters`` in ``live_api.yaml``)."""

    id: str
    applies_to: str
    filter_on: str | None = None
    status_in: list[str] = []
    exclude_flags: list[str] = []
    exclude_rules: list[str] = []
    date_field: dict[str, str] = {}
    rule: str | None = None


class CitationTemplate(_Live):
    """A ``cite_as`` template with ``<placeholders>``."""

    cite_as: str


class LiveCitation(_Live):
    """How live answers are cited."""

    live_query: CitationTemplate
    live_record: CitationTemplate
    other_sources: str


class LiveApi(_Live):
    """``live_api.yaml``: what of the semantic layer holds for live API data."""

    field: str
    meaning: str
    request_fields: list[str]
    returned: list[FieldRef]
    database_only: list[FieldRef]
    live_rules: list[LiveRule]
    default_filters: list[DefaultFilter]
    flag_conditions: dict[str, str]
    uncovered: str
    citation: LiveCitation
    checked: dict[str, Any]
    reference_prefix_optional: str | None = None
    """A prefix some references are published with and some without (Lewisham's
    "DC/": DC/21/120061 and 21/120061 can be the same application). Lookups try both."""


class SemanticLayer(BaseModel):
    """Everything the bot knows about one borough's data, checked and read-only."""

    model_config = ConfigDict(frozen=True)

    borough: Borough
    fields: dict[str, FieldDoc]
    """Field files by file name, e.g. ``"status.yaml"``."""
    sources: Sources
    live: LiveApi
    cleaning_rules: dict[str, Any]
    """``cleaning.yaml`` as ``clean_record`` expects it."""

    def allowed_values(self, field: str) -> tuple[str, ...]:
        """Return the values a field can take, for a tool argument's choices.

        Args:
            field: The field name, e.g. ``"decision"``.

        Returns:
            The values listed in the field's YAML, in the YAML's order.

        Raises:
            KeyError: If no field file lists values for ``field``.
        """
        for doc in self.fields.values():
            if doc.field == field and doc.values:
                return tuple(doc.values)
        raise KeyError(f"no values listed for field {field!r}")

    def field_doc(self, field: str) -> FieldDoc | None:
        """Return the field file that explains ``field``, if it has its own.

        Args:
            field: The field name, e.g. ``"status_stale"``.

        Returns:
            The field's YAML, or ``None`` (e.g. ``id``, explained in sources.yaml).
        """
        return next((doc for doc in self.fields.values() if doc.field == field), None)

    def portal_check(self) -> Source | None:
        """Return the source that records a person's portal check, if any.

        Returns:
            The source in ``sources.yaml`` with ``groups``, or ``None``.
        """
        return next((s for s in self.sources.sources.values() if s.groups), None)

    def prompt_context(self) -> str:
        """Build the data description the system prompt carries.

        Only what live answers can use: for each file explaining a returned field
        (raw or derived, such as status_stale), its meaning and rules; the live
        rules; the default-filter rules; and the line about what is not covered. The
        order is fixed, so the text is the same on every request.

        A field-file rule that refers to a database-only field is left out: it
        describes the database and would be false for live records. The live rules
        are kept whole; they name such fields on purpose.

        Returns:
            Plain text with short Markdown headings.
        """
        db_only = _database_only_markers(self.live)
        lines = ["## Fields"]
        seen: set[str] = set()
        for ref in self.live.returned:
            if ref.see in seen or ref.see not in self.fields:
                continue
            seen.add(ref.see)
            doc = self.fields[ref.see]
            lines += [f"### {doc.field}", _one_line(doc.meaning)]
            lines += [
                f"- {_one_line(rule)}"
                for rule in doc.rules
                if not _mentions_any(rule, db_only)
            ]

        lines.append("## Live data rules")
        lines += [f"- {_one_line(r.rule)}" for r in self.live.live_rules]
        lines += [
            f"- {f.id}: {_one_line(f.rule)}"
            for f in self.live.default_filters
            if f.rule
        ]
        lines += ["## Not covered", _one_line(self.live.uncovered)]
        return "\n".join(lines)


def load_semantic(borough: Borough, root: Path) -> SemanticLayer:
    """Load and check one borough's semantic YAMLs.

    Args:
        borough: The borough, e.g. ``"brent"``; its files are in ``root/borough``.
        root: The semantic folder (``Settings.semantic_dir``).

    Returns:
        The checked semantic layer.

    Raises:
        SemanticError: If the folder or a required file is missing, a file is not
            valid YAML or doesn't match its shape, or the files disagree (a ``see``
            to a missing file, a filter naming an unknown flag, rule or value).
    """
    folder = root / borough
    if not folder.is_dir():
        raise SemanticError(f"semantic folder not found: {folder}")

    for name in (CLEANING, LIVE_API, SOURCES):
        if not (folder / name).is_file():
            raise SemanticError(f"missing {folder / name}")

    fields = {
        path.name: _parse(FieldDoc, path)
        for path in sorted(folder.glob("*.yaml"))
        if path.name not in SPECIAL_FILES
    }
    layer = SemanticLayer(
        borough=borough,
        fields=fields,
        sources=_parse(Sources, folder / SOURCES),
        live=_parse(LiveApi, folder / LIVE_API),
        cleaning_rules=_read_cleaning(folder / CLEANING),
    )
    problems = _consistency_problems(layer, folder)
    if problems:
        raise SemanticError(
            f"{folder / LIVE_API} disagrees with the other files: "
            + "; ".join(problems)
        )
    return layer


def _parse[M: BaseModel](model: type[M], path: Path) -> M:
    """Read one YAML file into a model, naming the file in any error.

    Args:
        model: The model the file must match.
        path: The YAML file.

    Returns:
        The parsed model.

    Raises:
        SemanticError: If the file is not valid YAML or doesn't match ``model``.
    """
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return model.model_validate(data)
    except (yaml.YAMLError, ValidationError) as exc:
        raise SemanticError(f"invalid {path}: {exc}") from exc


def _read_cleaning(path: Path) -> dict[str, Any]:
    """Read ``cleaning.yaml`` the way the pipeline does.

    Args:
        path: The ``cleaning.yaml`` file.

    Returns:
        The parsed rules.

    Raises:
        SemanticError: If the file is not valid YAML or not a mapping.
    """
    try:
        rules = load_rules(path)
    except yaml.YAMLError as exc:
        raise SemanticError(f"invalid {path}: {exc}") from exc
    if not isinstance(rules, dict):
        raise SemanticError(f"invalid {path}: not a mapping")
    return rules


def _consistency_problems(layer: SemanticLayer, folder: Path) -> list[str]:
    """Check that ``live_api.yaml`` only names things the other files define.

    Args:
        layer: The parsed files.
        folder: The borough's folder, to check that ``see`` files exist.

    Returns:
        One message per problem; empty if the files agree.
    """
    live, problems = layer.live, []
    for ref in live.returned + live.database_only:
        if not (folder / ref.see).is_file():
            problems.append(f"{ref.field}: see {ref.see} does not exist")

    derived = {rule["id"] for rule in layer.cleaning_rules.get("derived", [])}
    record_rules = {rule["id"] for rule in layer.cleaning_rules.get("all_records", [])}
    returned = {ref.field for ref in live.returned}
    statuses = set(layer.allowed_values("status"))
    for f in live.default_filters:
        if f.filter_on and f.filter_on not in returned:
            problems.append(f"filter {f.id}: {f.filter_on} is not a returned field")
        problems += [
            f"filter {f.id}: status {s!r} not in status.yaml"
            for s in f.status_in
            if s not in statuses
        ]
        for flag in f.exclude_flags:
            if flag not in derived:
                problems.append(f"filter {f.id}: no rule {flag!r} in {CLEANING}")
            if flag not in live.flag_conditions:
                problems.append(f"filter {f.id}: no flag_conditions for {flag!r}")
        problems += [
            f"filter {f.id}: no rule {r!r} in {CLEANING}"
            for r in f.exclude_rules
            if r not in record_rules
        ]
    return problems


def _database_only_markers(live: LiveApi) -> set[str]:
    """Return the words by which a YAML text refers to a database-only field.

    The YAMLs name a database column by its identifier (``location_check``,
    ``ward_2022``) or by its file (``location.yaml``). Plain words such as ``site``
    or ``centroid`` are left out: in prose they usually mean the place, not the
    column, and matching them would drop correct rules.

    Args:
        live: The live-API description.

    Returns:
        Database-only identifiers (with an underscore) and the files that explain
        only database-only fields.
    """
    identifiers = {ref.field for ref in live.database_only if "_" in ref.field}
    live_files = {ref.see for ref in live.returned}
    db_files = {ref.see for ref in live.database_only} - live_files
    return identifiers | db_files


def _mentions_any(text: str, markers: set[str]) -> bool:
    """Check whether a text contains any of the markers, as a whole word.

    Whole words, so ``location_check`` doesn't match inside a longer identifier.

    Args:
        text: A rule from a YAML file.
        markers: Identifiers or file names, e.g. ``{"ward_2022", "location.yaml"}``.

    Returns:
        ``True`` if any marker appears as a whole word.
    """
    words = set(re.findall(r"[A-Za-z0-9_.]+", text))
    words |= {word.rstrip(".") for word in words}  # "location.yaml." ends a sentence
    return not words.isdisjoint(markers)


def _one_line(text: str) -> str:
    """Collapse a YAML text block (folded lines, extra spaces) into one line.

    Args:
        text: The text from a YAML file.

    Returns:
        The same words on one line.
    """
    return " ".join(text.split())
