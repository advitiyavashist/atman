"""T-178: the contract pack validates.

Every contract fixture in tests/fixtures/ is validated against the OpenAPI
component schema named for it in tests/fixtures/manifest.json. This is the
acceptance bar for the freeze: a fixture that drifts from the contract fails
here, and so does a contract change that invalidates a published fixture.

Raw adapter-input fixtures, such as recorded Claude hook payloads, live under
tests/fixtures/claude_hooks/ and are validated by adapter-specific tests before
they are converted into contract HookEventRequest envelopes.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

# A bare checkout can run the CLI tests without the contract toolchain, so the
# deps are an optional extra. CI sets TICKET_BOARD_CONTRACTS_REQUIRED=1 so that a
# missing extra there is a failure, not a silent skip -- otherwise the acceptance
# bar could pass by never running.
_REQUIRED = os.environ.get("TICKET_BOARD_CONTRACTS_REQUIRED") == "1"
_HINT = "install the contract toolchain: pip install -e '.[contracts]'"

try:
    import yaml
    import jsonschema  # noqa: F401
except ImportError as exc:  # pragma: no cover - environment-dependent
    if _REQUIRED:
        raise RuntimeError(
            f"TICKET_BOARD_CONTRACTS_REQUIRED=1 but the contract toolchain is missing "
            f"({exc}). {_HINT}"
        ) from exc
    pytest.skip(_HINT, allow_module_level=True)

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

REPO = Path(__file__).resolve().parents[1]
SPEC_PATH = REPO / "docs" / "contracts" / "openapi.yaml"
FIXTURES = REPO / "tests" / "fixtures"
MANIFEST_PATH = FIXTURES / "manifest.json"
# tests/fixtures/claude_hooks/ (T-214) holds real, captured Claude Code hook
# payloads used as adapter-parsing ground truth. They are not contract
# fixtures -- they were never listed in manifest.json and carry no paired
# schema -- so the frozen-tree agreement check below must not treat them as
# unlisted files. They are validated separately by
# tests/adapters/test_real_hook_fixtures.py, which already asserts no leaked
# operator paths or identifiers.
RAW_ADAPTER_FIXTURE_DIRS = {FIXTURES / "claude_hooks"}

SPEC_URI = "urn:ticket-board:openapi"


def _load_spec() -> dict:
    with SPEC_PATH.open() as fh:
        return yaml.safe_load(fh)


SPEC = _load_spec()
MANIFEST = json.loads(MANIFEST_PATH.read_text())
REGISTRY = Registry().with_resource(
    SPEC_URI, Resource.from_contents(SPEC, default_specification=DRAFT202012)
)


def _validator(schema_name: str) -> Draft202012Validator:
    return Draft202012Validator(
        {"$ref": f"{SPEC_URI}#/components/schemas/{schema_name}"}, registry=REGISTRY
    )


def _fixture_paths() -> list[Path]:
    return sorted(
        p
        for p in FIXTURES.rglob("*.json")
        if p != MANIFEST_PATH and not any(p.is_relative_to(raw_dir) for raw_dir in RAW_ADAPTER_FIXTURE_DIRS)
    )


def _rel(path: Path) -> str:
    return path.relative_to(FIXTURES).as_posix()


# ---------------------------------------------------------------- the spec

def test_spec_parses_and_is_openapi_31():
    assert SPEC["openapi"].startswith("3.1"), SPEC["openapi"]
    assert SPEC["components"]["schemas"], "no component schemas"
    assert SPEC["paths"], "no paths"


def test_every_ref_resolves():
    """A dangling $ref would make the published contract unusable downstream."""
    missing = []

    def walk(node, where):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "$ref" and isinstance(value, str):
                    target = SPEC
                    try:
                        for part in value.lstrip("#/").split("/"):
                            target = target[part]
                    except (KeyError, TypeError):
                        missing.append(f"{where}: {value}")
                else:
                    walk(value, f"{where}/{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{where}[{index}]")

    walk(SPEC, "")
    assert not missing, "unresolved $refs:\n" + "\n".join(missing)


def test_every_component_schema_is_referenced():
    """An orphan schema means a record the API never actually exposes."""
    referenced = set()

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "$ref" and isinstance(value, str):
                    referenced.add(value.rsplit("/", 1)[-1])
                else:
                    walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(SPEC)
    orphans = sorted(set(SPEC["components"]["schemas"]) - referenced)
    assert not orphans, f"unreferenced component schemas: {orphans}"


def test_every_component_schema_is_valid_json_schema():
    for name, schema in SPEC["components"]["schemas"].items():
        Draft202012Validator.check_schema(schema)


def test_every_operation_has_an_operation_id():
    seen = {}
    methods = {"get", "post", "put", "patch", "delete"}
    for route, item in SPEC["paths"].items():
        for method, operation in item.items():
            if method not in methods:
                continue
            op_id = operation.get("operationId")
            assert op_id, f"{method.upper()} {route} has no operationId"
            assert op_id not in seen, f"duplicate operationId {op_id}: {seen[op_id]} and {route}"
            seen[op_id] = route


# ------------------------------------------------------------- the fixtures

def test_manifest_and_fixture_tree_agree():
    on_disk = {_rel(p) for p in _fixture_paths()}
    in_manifest = set(MANIFEST)
    assert on_disk == in_manifest, (
        f"unlisted fixtures: {sorted(on_disk - in_manifest)}; "
        f"missing files: {sorted(in_manifest - on_disk)}"
    )


def test_manifest_names_real_schemas():
    known = set(SPEC["components"]["schemas"])
    unknown = sorted({s for s in MANIFEST.values() if s not in known})
    assert not unknown, f"manifest names schemas the spec does not define: {unknown}"


@pytest.mark.parametrize("rel", sorted(MANIFEST), ids=lambda r: r)
def test_fixture_validates_against_its_schema(rel):
    payload = json.loads((FIXTURES / rel).read_text())
    errors = sorted(_validator(MANIFEST[rel]).iter_errors(payload), key=lambda e: list(e.path))
    assert not errors, "\n".join(
        f"{rel}: {'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}" for e in errors
    )


# ------------------------------------------------- screen and state coverage

REQUIRED_SCREEN_SCHEMAS = {
    "OverviewResponse",
    "TicketListResponse",
    "TicketDetailResponse",
    "AgentListResponse",
    "ActivityResponse",
    "MasterPanelResponse",
    "ChannelListResponse",
    "MemberListResponse",
    "MessageListResponse",
}


def test_every_screen_has_at_least_one_fixture():
    covered = set(MANIFEST.values())
    missing = sorted(REQUIRED_SCREEN_SCHEMAS - covered)
    assert not missing, f"screens with no fixture: {missing}"


def test_empty_state_is_covered_for_every_listing_screen():
    """Fixtures must cover the empty state, not just the happy path."""
    listing = {
        "OverviewResponse",
        "TicketListResponse",
        "AgentListResponse",
        "ActivityResponse",
        "ChannelListResponse",
        "MessageListResponse",
    }
    with_empty = set()
    for rel, schema in MANIFEST.items():
        if schema not in listing:
            continue
        if "empty_state" in json.loads((FIXTURES / rel).read_text()):
            with_empty.add(schema)
    missing = sorted(listing - with_empty)
    assert not missing, f"no empty-state fixture for: {missing}"


def test_offline_blocked_and_review_states_are_covered():
    blob = "\n".join((FIXTURES / rel).read_text() for rel in MANIFEST)
    for needle in (
        '"state": "offline"',            # offline agent
        '"runner_offline"',              # offline runner
        '"manual_resume_required"',      # hook-only connection
        '"state": "blocked"',            # explicitly blocked ticket
        '"dependency_blocked": true',    # derived dependency block
        '"state": "review"',             # ticket awaiting review
        '"state": "accepted"',           # review accepted
        '"state": "rejected"',           # review rejected
        '"state": "stale"',              # stale event stream
        '"reason": "cursor_expired"',    # SSE replay gap
    ):
        assert needle in blob, f"no fixture covers {needle}"


def test_error_fixtures_cover_every_error_code():
    """Each declared error code needs a published example."""
    declared = set(SPEC["components"]["schemas"]["ErrorCode"]["enum"])
    published = set()
    for rel, schema in MANIFEST.items():
        if schema != "ErrorResponse":
            continue
        published.add(json.loads((FIXTURES / rel).read_text())["error"]["code"])
    missing = sorted(declared - published)
    assert not missing, f"error codes with no fixture: {missing}"


def test_error_status_matches_the_code_family():
    families = {
        400: {"malformed_request"},
        401: {"unauthenticated"},
        403: {"forbidden_scope", "agent_token_insufficient", "not_channel_member",
              "membership_revoked", "sender_identity_rejected"},
        404: {"not_found"},
        429: {"rate_limited"},
    }
    for rel, schema in MANIFEST.items():
        if schema != "ErrorResponse":
            continue
        error = json.loads((FIXTURES / rel).read_text())["error"]
        expected = families.get(error["status"])
        if expected is not None:
            assert error["code"] in expected, f"{rel}: {error['code']} with status {error['status']}"


# ------------------------------------------------------------------ secrets

SECRET_PATTERNS = [
    # Bearer token, including the JSON-escaped whitespace a raw file read sees
    # when a fixture embeds a literal "\n"/"\r"/"\t" between the scheme and
    # the token instead of a real space. Whitespace is deliberately restricted
    # to same-line space/tab (not \s, which includes newlines) so this does
    # not fire on OpenAPI's unrelated `scheme: bearer` / `bearerFormat:` pair.
    re.compile(r"\bBearer(?:[ \t]|\\[nrt])+[A-Za-z0-9._-]{8,}", re.I),
    re.compile(r"\bsk[-_][A-Za-z0-9_-]{8,}"),
    re.compile(r"\bAKIA[0-9A-Za-z]{16}\b", re.I),  # case-insensitive on purpose
    re.compile(r"\bghp_[A-Za-z0-9]{8,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgho_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox[abpr]-[A-Za-z0-9-]{10,}\b", re.I),  # Slack token
    re.compile(r"\bAIza[A-Za-z0-9_-]{35}\b"),  # Google API key
    re.compile(r"\b[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\b"),  # JWT, long segments
    re.compile(r"\beyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{2,}\b"),  # JWT, short segments -- anchored on the "{" header prefix
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),  # PEM private key block
    re.compile(r"://[^\s/:@]+:[^\s/:@]+@"),  # user:pass@host in a URL
    # AWS secret access key: 40 chars of base64 charset. Matched only when the
    # blob mixes upper, lower and digit -- a plain lowercase-hex git SHA (the
    # other common 40-char string in this repo) never does, so this stays
    # narrow rather than flagging every commit hash.
    re.compile(
        r"(?<![A-Za-z0-9+/=])"
        r"(?=[A-Za-z0-9+/=]{40}(?![A-Za-z0-9+/=]))"
        r"(?=[A-Za-z0-9+/=]*[A-Z])(?=[A-Za-z0-9+/=]*[a-z])(?=[A-Za-z0-9+/=]*[0-9])"
        r"[A-Za-z0-9+/=]{40}"
    ),
]

SECRET_BEARING_FIELDS = ("code", "token", "api_key", "secret", "password",
                          "private_key", "access_token")
PLACEHOLDERS = {
    "<enrollment-code-not-in-fixtures>",
    "<agent-token-not-in-fixtures>",
    "<invite-code-not-in-fixtures>",
}


_TEXT_SUFFIXES = {".py", ".json", ".md", ".yaml", ".yml", ".html", ".txt"}


def _text_files(root: Path) -> list[Path]:
    return sorted(
        p for p in root.rglob("*")
        if p.is_file() and p.suffix in _TEXT_SUFFIXES
        and "__pycache__" not in p.parts
    )


def _extra_credential_scan_paths() -> list[Path]:
    """Fixtures are the primary blast radius for a leaked credential, but a
    planted secret would land just as easily in the spec's `example:` values
    or in hand-written docs and acceptance code. Scan those too.

    Restricted to known text suffixes (and `__pycache__` excluded) so a stray
    compiled `.pyc` from an unrelated local test run does not blow up the
    scan with a binary-decode error.
    """
    paths = [SPEC_PATH]
    paths += _text_files(REPO / "docs")
    paths += _text_files(REPO / "tests" / "acceptance")
    return paths


# Synthetic values that are shaped like a credential but are not one -- the
# acceptance harness sends these to its own local stub to exercise credential
# header wiring, so they are never a real secret. Exact strings only, so a
# genuine credential that happens to reuse a placeholder-y word still fails.
SAFE_PLACEHOLDER_SECRETS = {
    "Bearer stub-agent-token",
}


def _assert_no_credentials(text, where):
    for pattern in SECRET_PATTERNS:
        for match in pattern.finditer(text):
            found = match.group(0)
            assert found in SAFE_PLACEHOLDER_SECRETS, (
                f"{where} matches {pattern.pattern}: {found!r}"
            )


def test_no_fixture_contains_anything_shaped_like_a_credential():
    for rel in MANIFEST:
        _assert_no_credentials((FIXTURES / rel).read_text(), rel)
    for path in _extra_credential_scan_paths():
        _assert_no_credentials(path.read_text(), path.relative_to(REPO))


@pytest.mark.parametrize("credential", [
    "sk-live_" + "a1B2c3D4" * 3,
    "sk-proj_" + "a1B2c3D4" * 3,
    "sk_test_" + "a1B2c3D4" * 3,
    "AKIA" + "A1B2C3D4" * 2,
    "sk-" + "a1B2c3D4" * 3,
    "ghp_" + "a1B2c3D4" * 3,
    "Bearer " + "a1B2c3D4" * 3,
    ".".join(["a1B2c3D4" * 2] * 3),
], ids=["sk-live", "sk-proj", "sk-test", "aws-akia",
        "legacy-sk", "github", "bearer", "jwt"])
def test_fixture_scanner_rejects_planted_credentials(tmp_path, monkeypatch, credential):
    """Exercise the actual fixture scan without changing the frozen fixtures."""
    rel = "planted.json"
    fixture = tmp_path / rel
    monkeypatch.setitem(globals(), "FIXTURES", tmp_path)
    monkeypatch.setitem(globals(), "MANIFEST", {rel: "unused-by-credential-scan"})

    # The same throwaway fixture passes until a credential is planted in a
    # free-text field, independently of the code/token placeholder check.
    fixture.write_text(json.dumps({"description": "synthetic fixture"}))
    test_no_fixture_contains_anything_shaped_like_a_credential()
    fixture.write_text(json.dumps({"description": f"synthetic {credential} value"}))
    with pytest.raises(AssertionError, match=r"planted\.json matches"):
        test_no_fixture_contains_anything_shaped_like_a_credential()


def test_secret_bearing_fields_use_placeholders():
    """`code` and `token` are returned once by the real API and never stored here.

    ErrorResponse fixtures are exempt: their `code` is the ErrorCode enum, which
    the schema already constrains to a closed set of non-secret values.
    """
    def check(node, rel, where=""):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in SECRET_BEARING_FIELDS and isinstance(value, str):
                    assert value in PLACEHOLDERS, f"{rel}:{where}/{key} carries a literal secret"
                check(value, rel, f"{where}/{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                check(value, rel, f"{where}[{index}]")

    for rel, schema in MANIFEST.items():
        if schema == "ErrorResponse":
            continue
        check(json.loads((FIXTURES / rel).read_text()), rel)


# --------------------------------------- operator home paths in the test tree

# A home directory that names a real person. `/Users/<operator>` and
# `/home/agent` are the scrubbed placeholder forms the captured adapter
# fixtures standardised on; any other first segment names whoever happened to
# run the capture, which is what T-210 exists to keep out of a shared repo.
OPERATOR_HOME_RE = re.compile(
    r"/(?:Users|home)/([A-Za-z0-9_.-]+)(?:/[A-Za-z0-9_.<>-]+)*"
)
PLACEHOLDER_HOME_SEGMENTS = {"agent", "operator", "runner", "user",
                             # synthetic handle in the error-leak test
                             "someone"}

# Literals that still name an operator and are owned by a ticket other than
# T-210. Empty, and worth keeping that way: entries are for a literal nobody
# can fix yet, never for one that is an edit away. Keyed by (repo-relative
# path, the path with the handle replaced by `<operator>`) so the table names
# nobody itself, and so a NEW literal -- even one in an already-listed file --
# is still caught rather than covered.
KNOWN_OPERATOR_PATH_DEBTS = {}


def _anonymise_home(matched: str, handle: str) -> str:
    """Replace the home segment with `<operator>`, for keying and reporting.

    The match always begins `/Users/<handle>` or `/home/<handle>`, so replacing
    the first occurrence of the handle rewrites exactly that segment.
    """
    return matched.replace(handle, "<operator>", 1)


def _operator_path_violations(paths, root):
    """Every (rel, matched) naming a real operator home, known debts excluded.

    The raw match is reported so the failure is actionable, but the debt table
    is keyed on the anonymised form so this file names no operator itself.
    """
    found = []
    for path in paths:
        rel = str(path.relative_to(root))
        for match in OPERATOR_HOME_RE.finditer(path.read_text()):
            handle = match.group(1)
            if handle in PLACEHOLDER_HOME_SEGMENTS:
                continue
            if (rel, _anonymise_home(match.group(0), handle)) in KNOWN_OPERATOR_PATH_DEBTS:
                continue
            found.append((rel, match.group(0)))
    return found


def test_no_test_file_names_a_real_operator_home():
    """The guard that keeps T-210's scrub from regressing.

    A sweep fixes today's literals; this fails the build on tomorrow's. Use a
    placeholder (`/Users/<operator>`, `/home/agent`), build the path under
    `tmp_path`, or read it from an environment variable -- see
    `TICKET_BOARD_REAL_BOARD` in tests/storage/test_legacy_import.py.

    A literal that genuinely belongs to another in-flight ticket goes in
    KNOWN_OPERATOR_PATH_DEBTS with the ticket that owns it, so the exception is
    reviewable and scoped to one exact path rather than to a whole file.
    """
    violations = _operator_path_violations(_text_files(REPO / "tests"), REPO)
    assert not violations, "test files name a real operator home: " + ", ".join(
        "{} -> {}".format(rel, found) for rel, found in violations
    )


def test_operator_home_guard_catches_a_planted_literal(tmp_path):
    """The guard bites, and the placeholder forms stay usable.

    Every path here is built by interpolation rather than written out, so that
    exercising the guard does not plant the literal it exists to forbid.
    """
    planted = tmp_path / "planted.py"
    handle = "somebody"

    planted.write_text('BOARD = "/Users/%s/Downloads/steer"\nCWD = "/home/agent/p"\n'
                       % "<operator>")
    assert _operator_path_violations([planted], tmp_path) == []

    planted.write_text('BOARD = "/Users/%s/Downloads/steer/.tickets"\n' % handle)
    assert _operator_path_violations([planted], tmp_path) == [
        ("planted.py", "/Users/%s/Downloads/steer/.tickets" % handle)
    ]

    # A debt is keyed to one exact path, so a different literal in the same
    # file is still caught rather than waved through.
    planted.write_text('OTHER = "/Users/%s/Downloads/tickets"\n' % handle)
    assert _operator_path_violations([planted], tmp_path) == [
        ("planted.py", "/Users/%s/Downloads/tickets" % handle)
    ]
