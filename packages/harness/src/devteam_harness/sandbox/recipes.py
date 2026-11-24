"""The ten allowlisted recipes (HC-14/HC-06; Part E plan §3.4; TS-05).

A recipe is a **fixed argument list** run by the image's launcher, never a shell string and never a package
manager. The only caller-supplied values are validated arguments (an enum for test selection and a slug for
a migration message); unknown recipes and unknown arguments are refused. Layout inside the container
(materialised starter kit, plan §3.6): ``/work/backend`` (FastAPI app ``app``), ``/work/tests/backend``,
``/work/frontend``; outputs under ``/work/.devteam/out``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from devteam_core.contracts.sandbox import RecipeSpec
from devteam_harness.errors import PolicyDeniedError

OUT = "/work/.devteam/out"
_BASE_ENV = {"CI": "1", "HOME": "/work/.devteam/home", "NO_COLOR": "1"}
_PY_ENV = {
    **_BASE_ENV,
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONPATH": "/work/backend:/opt/devteam/tools",
    "COVERAGE_FILE": "/work/.devteam/.coverage",
}
_VITE = "/node_modules/vite/bin/vite.js"
_CONFIG = ("--configLoader", "runner")  # Vite would otherwise write a temp file into the read-only image


class _NoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PytestArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    select: Literal["all", "api", "smoke"] = "all"


class AutogenerateArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    message: str = Field(pattern=r"^[a-z0-9_]{1,40}$")


@dataclass(frozen=True, slots=True)
class Recipe:
    name: str
    image: Literal["python_api", "node_web"]
    tool: Literal["request_build", "request_test_run", "request_migration_check"]
    args_model: type[BaseModel]
    build: Callable[[BaseModel], tuple[str, ...]]
    workdir: str
    env: Mapping[str, str]
    timeout_s: int
    reports_tests: bool = False
    outputs: tuple[str, ...] = ()

    def spec(self, args: Mapping[str, Any] | None = None) -> RecipeSpec:
        """Validate the arguments and return the fixed command. Raises ``PolicyDeniedError`` if invalid."""
        try:
            parsed = self.args_model.model_validate(dict(args or {}))
        except ValidationError:
            raise PolicyDeniedError(
                "recipe.bad_arguments", f"invalid arguments for recipe {self.name}", "deny.recipe_arguments"
            ) from None
        return RecipeSpec(
            recipe=self.name,
            image=self.image,
            argv=self.build(parsed),
            workdir=self.workdir,
            env=dict(self.env),
            timeout_s=self.timeout_s,
            reports_tests=self.reports_tests,
            outputs=self.outputs,
            tool=self.tool,
        )


_MARKERS = {"all": "not db", "api": "api and not db", "smoke": "smoke and not db"}


def _pytest(markers: str, junit: str, coverage: str) -> tuple[str, ...]:
    return (
        "python", "-m", "pytest", "-p", "devteam_req", "-p", "no:cacheprovider", "-q", "-m", markers,
        f"--junitxml={OUT}/{junit}", "--cov=app", f"--cov-report=xml:{OUT}/{coverage}",
        "--rootdir=/work", "/work/tests/backend",
    )  # fmt: skip


def _pytest_api(args: BaseModel) -> tuple[str, ...]:
    if not isinstance(args, PytestArgs):
        raise TypeError("pytest_api arguments expected")
    return _pytest(_MARKERS[args.select], "junit.xml", "coverage.xml")


def _autogenerate(args: BaseModel) -> tuple[str, ...]:
    if not isinstance(args, AutogenerateArgs):
        raise TypeError("alembic_autogenerate arguments expected")
    return ("python", "-m", "devteam_migration_check", "autogenerate", args.message)


RECIPES: dict[str, Recipe] = {
    recipe.name: recipe
    for recipe in (
        Recipe(
            "pytest_api",
            "python_api",
            "request_test_run",
            PytestArgs,
            _pytest_api,
            "/work",
            {**_PY_ENV, "DATABASE_URL": "sqlite:////work/.devteam/api-test.db"},
            300,
            True,
            (f"{OUT}/junit.xml", f"{OUT}/coverage.xml"),
        ),
        Recipe(
            "pytest_db",
            "python_api",
            "request_test_run",
            _NoArgs,
            lambda _a: _pytest("db", "junit.xml", "coverage.xml"),
            "/work",
            {**_PY_ENV, "DATABASE_URL": "sqlite:////work/.devteam/db-test.db"},
            300,
            True,
            (f"{OUT}/junit.xml", f"{OUT}/coverage.xml"),
        ),
        Recipe(
            "alembic_upgrade",
            "python_api",
            "request_migration_check",
            _NoArgs,
            lambda _a: ("python", "-m", "devteam_migration_check", "roundtrip"),
            "/work/backend",
            {**_PY_ENV, "DATABASE_URL": "sqlite:////work/.devteam/migration.db"},
            180,
            False,
            (f"{OUT}/migration.json",),
        ),
        Recipe(
            "alembic_autogenerate",
            "python_api",
            "request_migration_check",
            AutogenerateArgs,
            _autogenerate,
            "/work/backend",
            {**_PY_ENV, "DATABASE_URL": "sqlite:////work/.devteam/autogen.db"},
            180,
            False,
            (f"{OUT}/migration.json", f"{OUT}/proposed_revision.py"),
        ),
        Recipe(
            "ruff_check",
            "python_api",
            "request_build",
            _NoArgs,
            lambda _a: (
                "python",
                "-m",
                "ruff",
                "check",
                "--no-cache",
                "--output-format",
                "json",
                f"--output-file={OUT}/ruff.json",
                "backend/app",
                "tests/backend",
            ),
            "/work",
            _PY_ENV,
            120,
            False,
            (f"{OUT}/ruff.json",),
        ),
        Recipe(
            "bandit_scan",
            "python_api",
            "request_build",
            _NoArgs,
            lambda _a: (
                "python",
                "-m",
                "bandit",
                "-q",
                "-r",
                "backend/app",
                "-f",
                "json",
                "-o",
                f"{OUT}/bandit.json",
            ),
            "/work",
            _PY_ENV,
            120,
            False,
            (f"{OUT}/bandit.json",),
        ),
        Recipe(
            "static_validation",
            "python_api",
            "request_build",
            _NoArgs,
            lambda _a: ("python", "-m", "devteam_static_check", "frontend"),
            "/work",
            _PY_ENV,
            60,
        ),
        Recipe(
            "tsc_check",
            "node_web",
            "request_build",
            _NoArgs,
            lambda _a: ("node", "/node_modules/typescript/bin/tsc", "--noEmit", "-p", "tsconfig.json"),
            "/work/frontend",
            {**_BASE_ENV, "NODE_ENV": "production"},
            300,
        ),
        Recipe(
            "vitest_run",
            "node_web",
            "request_test_run",
            _NoArgs,
            lambda _a: (
                "node",
                "/node_modules/vitest/vitest.mjs",
                "run",
                *_CONFIG,
                "--reporter=junit",
                f"--outputFile={OUT}/junit.xml",
            ),
            "/work/frontend",
            {**_BASE_ENV, "NODE_ENV": "test"},
            300,
            True,
            (f"{OUT}/junit.xml",),
        ),
        Recipe(
            "npm_build",
            "node_web",
            "request_build",
            _NoArgs,
            lambda _a: ("node", _VITE, "build", *_CONFIG, "--outDir", f"{OUT}/dist", "--emptyOutDir"),
            "/work/frontend",
            {**_BASE_ENV, "NODE_ENV": "production"},
            600,
            False,
            (f"{OUT}/dist",),
        ),
    )
}


def get_recipe(name: str) -> Recipe:
    try:
        return RECIPES[name]
    except KeyError:
        raise PolicyDeniedError("recipe.unknown", f"unknown recipe {name!r}", "deny.recipe_unknown") from None


def recipes_for_tool(tool: str) -> tuple[str, ...]:
    return tuple(name for name, recipe in RECIPES.items() if recipe.tool == tool)


__all__ = ["RECIPES", "AutogenerateArgs", "PytestArgs", "Recipe", "get_recipe", "recipes_for_tool"]
