from pathlib import Path

import pytest

from scripts import hermetic_preflight as hp


def test_committed_preflight_freeze_is_exact_and_includes_bootstrap() -> None:
    lines, versions = hp.load_frozen_requirements()

    assert lines
    assert all(hp.EXACT_REQUIREMENT.fullmatch(line) for line in lines)
    assert versions["pip"] == "25.3"
    assert versions["setuptools"] == "75.9.1"
    assert versions["wheel"] == "0.45.1"
    assert versions["pytest"] == hp.PINNED_PYTEST


@pytest.mark.parametrize(
    "bad_line",
    (
        "pip>=24,<26",
        "pytest==8.4.2; python_version >= '3.9'",
        "wheel @ https://example.invalid/wheel.whl",
    ),
)
def test_preflight_freeze_rejects_non_exact_requirements(
    tmp_path: Path, bad_line: str
) -> None:
    freeze = tmp_path / "requirements.txt"
    freeze.write_text(
        "\n".join(
            (
                "pip==25.3",
                "setuptools==75.9.1",
                "wheel==0.45.1",
                "pytest==8.4.2",
                bad_line,
            )
        )
        + "\n"
    )

    with pytest.raises(SystemExit, match="not an exact name==version pin"):
        hp.load_frozen_requirements(freeze)


def test_preflight_freeze_rejects_missing_bootstrap_pin(tmp_path: Path) -> None:
    freeze = tmp_path / "requirements.txt"
    freeze.write_text(
        "setuptools==75.9.1\nwheel==0.45.1\npytest==8.4.2\n",
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="missing pip"):
        hp.load_frozen_requirements(freeze)


def test_bootstrap_version_verification_fails_on_drift() -> None:
    expected = {"pip": "25.3", "setuptools": "75.9.1", "wheel": "0.45.1"}

    hp.require_expected_versions(expected, expected, "source")
    with pytest.raises(SystemExit, match="bootstrap versions drifted"):
        hp.require_expected_versions(
            {**expected, "wheel": "0.44.0"}, expected, "wheel"
        )
