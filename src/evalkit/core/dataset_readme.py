from dataclasses import dataclass, field
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from evalkit.core.lint import Finding

README_FILENAME = "README.md"
FENCE = "---"


class MetadataLink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    url: str


class MetadataLicense(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    url: str | None = None


class DatasetMetadata(BaseModel):
    model_config = ConfigDict(extra="allow")

    description: str | None = None
    links: list[MetadataLink] | None = None
    license: MetadataLicense | None = None
    attribution: str | None = None
    __pydantic_extra__: dict[str, str] = Field(init=False)


@dataclass(frozen=True)
class DatasetReadme:
    readme: str | None = None
    metadata: DatasetMetadata = field(default_factory=DatasetMetadata)


def _finding(message: str) -> Finding:
    return Finding(severity="error", code="E-README", case_id=README_FILENAME, message=message)


def _split_readme(text: str) -> tuple[Any, str]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != FENCE:
        return {}, text
    end = next((i for i, line in enumerate(lines[1:], start=1) if line.strip() == FENCE), None)
    if end is None:
        raise ValueError(f"the frontmatter has no closing {FENCE}")
    frontmatter = yaml.safe_load("".join(lines[1:end]))
    return ({} if frontmatter is None else frontmatter), "".join(lines[end + 1 :]).lstrip("\n")


def parse_dataset_readme(text: str) -> tuple[DatasetReadme, list[Finding]]:
    try:
        frontmatter, body = _split_readme(text)
    except (ValueError, yaml.YAMLError) as exc:
        return DatasetReadme(), [_finding(f"the frontmatter cannot be read: {exc}")]
    if not isinstance(frontmatter, dict):
        return DatasetReadme(), [_finding("the frontmatter must be a mapping of keys to values")]
    try:
        metadata = DatasetMetadata.model_validate(frontmatter)
    except ValidationError as exc:
        return DatasetReadme(), [
            _finding(f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}")
            for error in exc.errors()
        ]
    return (
        DatasetReadme(readme=body if body.strip() else None, metadata=metadata),
        [],
    )
