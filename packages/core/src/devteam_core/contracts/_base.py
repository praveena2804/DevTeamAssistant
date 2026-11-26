"""Building blocks shared by every contract (HS-06, HC-20).

Every contract:
- rejects unknown fields (``extra="forbid"``), so an agent cannot smuggle extra claims into a record;
- is immutable (``frozen=True``); changes are made with ``model_copy(update=...)`` and re-validated;
- carries ``schema_version`` so stored data can be migrated when a contract changes.

Identifier and path types are constrained here once, so the same rule applies everywhere and appears
in the exported JSON Schema.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

SCHEMA_VERSION: Literal[1] = 1


class Contract(BaseModel):
    """Base class of every shared contract."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True, use_enum_values=False)

    schema_version: Literal[1] = SCHEMA_VERSION


# ------------------------------------------------------------------------------------------- identifiers

_ULID = r"[0-7][0-9A-HJKMNP-TV-Z]{25}"


def ulid_id_pattern(prefix: str) -> str:
    """Regex for ``<prefix>_<ULID>`` identifiers produced by :func:`devteam_core.ids.new_id`."""
    return rf"^{prefix}_{_ULID}$"


def _id(prefix: str) -> StringConstraints:
    return StringConstraints(pattern=ulid_id_pattern(prefix))


ProjectId = Annotated[str, _id("prj")]
DocumentId = Annotated[str, _id("doc")]
RunId = Annotated[str, _id("run")]
TaskId = Annotated[str, _id("tsk")]
PlanId = Annotated[str, _id("pln")]
PrincipalId = Annotated[str, _id("prn")]  # human principal (Part B)
SpecVersionId = Annotated[str, _id("spv")]
ArtifactId = Annotated[str, _id("art")]
TestRunId = Annotated[str, _id("trn")]
ApprovalId = Annotated[str, _id("apr")]
GapId = Annotated[str, _id("gap")]
QuestionId = Annotated[str, _id("cq")]
AnswerId = Annotated[str, _id("ca")]
DecisionId = Annotated[str, _id("dec")]
FeasibilityItemId = Annotated[str, _id("fea")]
FeasibilityReportId = Annotated[str, _id("fsr")]  # Part G
RoleId = Annotated[str, _id("rol")]
PermissionId = Annotated[str, _id("prm")]
EntityId = Annotated[str, _id("ent")]
WorkflowId = Annotated[str, _id("wfl")]
IntegrationId = Annotated[str, _id("itg")]
TechnologyNeedId = Annotated[str, _id("tec")]
CriterionId = Annotated[str, _id("acc")]
ConstraintId = Annotated[str, _id("con")]
EvidenceId = Annotated[str, _id("evd")]
ChangeRequestId = Annotated[str, _id("chg")]
SecretId = Annotated[str, _id("sec")]
EventId = Annotated[str, _id("evt")]
ToolCallId = Annotated[str, _id("tcl")]
ModelRequestId = Annotated[str, _id("mrq")]
AuditEventId = Annotated[str, _id("aud")]

# Chunk IDs are content-derived by the Part B chunker: ``chk_`` + 26 lowercase hex characters.
ChunkId = Annotated[str, StringConstraints(pattern=r"^chk_[0-9a-f]{26}$")]

# Stable requirement keys (Spec §6.2): FR-xxx, NFR-xxx, BR-xxx; unique within one analysis version (D-14 (a)).
REQUIREMENT_KEY_PATTERN = r"^(FR|NFR)-[0-9]{3,4}$"
RULE_KEY_PATTERN = r"^BR-[0-9]{3,4}$"
RequirementKey = Annotated[str, StringConstraints(pattern=REQUIREMENT_KEY_PATTERN)]
RuleKey = Annotated[str, StringConstraints(pattern=RULE_KEY_PATTERN)]
TraceKey = Annotated[str, StringConstraints(pattern=r"^(FR|NFR|BR)-[0-9]{3,4}$")]

# Any record reference: a ULID-style ID, a chunk ID or a stable requirement/rule key.
RecordRef = Annotated[
    str,
    StringConstraints(
        pattern=rf"^([a-z][a-z0-9]{{1,11}}_{_ULID}|chk_[0-9a-f]{{26}}|(FR|NFR|BR)-[0-9]{{3,4}})$"
    ),
]

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
TraceIdHex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]

# ------------------------------------------------------------------------------------------------- text

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2_000)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20_000)]

# --------------------------------------------------------------------------------------- relative paths

_WINDOWS_RESERVED = re.compile(r"^(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?$", re.IGNORECASE)
_FORBIDDEN_CHARS = set('<>:"|?*\\') | {chr(c) for c in range(32)}
MAX_PATH_CHARS = 240


def check_relative_path(value: str, *, allow_directory: bool) -> str:
    """Structural check for a workspace-relative path written with ``/`` separators.

    This is the *contract-level* check: it guarantees that a path carried in a task, handoff or
    agent output can never be absolute, climb out with ``..``, address a drive, UNC share or NTFS
    alternate data stream, or use a reserved Windows device name. The Controlled File Service (C.3)
    repeats the check against the real filesystem (symlinks, junctions, case).
    """
    if not value or len(value) > MAX_PATH_CHARS:
        raise ValueError("path must be 1-240 characters")
    if value != value.strip():
        raise ValueError("path must not start or end with whitespace")
    if "%" in value:
        raise ValueError("percent-encoded paths are not accepted")
    bad = _FORBIDDEN_CHARS.intersection(value)
    if bad:
        raise ValueError("path contains a forbidden character (drive, stream, backslash or control)")
    if value.startswith("/"):
        raise ValueError("path must be relative to the workspace")
    body = value[:-1] if allow_directory and value.endswith("/") else value
    if not body:
        raise ValueError("path is empty")
    for segment in body.split("/"):
        if segment in {"", ".", ".."}:
            raise ValueError("path segments must not be empty, '.' or '..'")
        if segment.endswith((".", " ")):
            raise ValueError("path segments must not end with a dot or space")
        if _WINDOWS_RESERVED.match(segment):
            raise ValueError("path uses a reserved Windows device name")
    return value


def _file_path(value: str) -> str:
    if value.endswith("/"):
        raise ValueError("a file path must not end with '/'")
    return check_relative_path(value, allow_directory=False)


def _path_scope(value: str) -> str:
    return check_relative_path(value, allow_directory=True)


RelPath = Annotated[str, AfterValidator(_file_path), Field(max_length=MAX_PATH_CHARS)]
PathScope = Annotated[str, AfterValidator(_path_scope), Field(max_length=MAX_PATH_CHARS)]


def path_in_scope(path: str, scope: str) -> bool:
    """True if ``path`` equals the file ``scope`` or lies under the directory ``scope`` (ending ``/``).

    Comparison is case-insensitive because the target filesystem (NTFS) is.
    """
    p, s = path.casefold(), scope.casefold()
    return p.startswith(s) if s.endswith("/") else p == s


# ----------------------------------------------------------------------------------------------- time


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware (UTC)")
    return value


AwareDatetime = Annotated[datetime, AfterValidator(_aware)]
