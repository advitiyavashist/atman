"""Worker / Reviewer / Master permission presets (T-192).

The frozen T-178 contract has no `preset` field and no `PermissionPreset`
schema, and adding one would be a contract change. It does not need one: the
enforcement vocabulary is already frozen under different names.
`RegisterRunnerRequest` and `RunnerLease` carry `permission_policy`
(`prompt` | `allowlist` | `deny_all`), `allowlisted_worktree`,
`runtime_profile`, `concurrency` (pinned to 1) and `budget`; and
`CreateEnrollmentRequest` already carries the operator's `role`. A preset is
therefore a SERVER-SIDE mapping from the role the operator chose onto fields
the contract already specifies -- no new route, no new field, no new
credential (T-224 holds: operatorSession creates, the agent token registers).

WHY THE MAPPING IS AN EXACT MATCH AND NOT A HEURISTIC. `role` is free-form
(maxLength 40) and the live board uses `backend`, `infra`, `console`,
`verification` and others. It would be easy to sniff those strings -- treat
anything containing "review" as a reviewer, say. That is precisely the shape
of a trust heuristic that reads as a control and behaves as an opt-out: a new
role named `code-review-tooling` would silently acquire reviewer permissions
because of a substring. So the mapping is exact: a role that is literally
`worker`, `reviewer` or `master` selects that preset, and EVERY other role
gets the default. There is no partial matching and no case where an
unrecognised role widens anything.

WHY THE DEFAULT IS `worker` AND NOT THE STRICTEST PRESET. Fail-closed is the
right instinct, but defaulting unknown roles to `deny_all` would mean every
role the board actually uses today (backend, infra, console, ...) enrols
unable to do its job, and the predictable response to that is an operator
turning enforcement off. `worker` is the default because it is the most
restrictive preset that still lets an agent work: writes confined to its own
allowlisted worktree, and never `prompt`.

WORKTREE CONFINEMENT IS ONLY AS STRONG AS WHAT THE OPERATOR SUPPLIED, and
that is a deliberate limit rather than a gap left open. `worktree` is OPTIONAL
in the frozen `CreateEnrollmentRequest`. An earlier draft of this module made
it mandatory for the worker preset, which turned every previously valid
enrolment into a 400 -- a contract regression wearing a security hat. So when
an operator supplies a worktree it is recorded and enforced, and when they do
not there is no approved directory to check a runner against. The permission
POLICY is enforced either way, so an unconfined agent is still never `prompt`.

NO PRESET EVER GRANTS `prompt`, which is the deliberate reading of this
ticket's "no prompt-only permissions". `prompt` is unbounded-with-a-human: it
does not constrain what may be asked for, it only moves the decision to
whoever is watching, and an unattended overnight agent has nobody watching.
The enum in the contract states no ordering between its three values; ranking
`prompt` as the broadest is this module's judgement and is called out in the
T-192 review notes rather than hidden here.
"""

# Ordered by breadth, narrowest first. `_RANK` is the whole ordering: a runner
# may never register a policy that ranks above what its preset approved.
_RANK = {"deny_all": 0, "allowlist": 1, "prompt": 2}

DEFAULT_PRESET = "worker"

PRESETS = {
    # Writes code. Confined to the one worktree the operator allowlisted for
    # it; `allowlist` rather than `prompt` so an unattended run cannot widen
    # itself by asking.
    "worker": {
        "permission_policy": "allowlist",
        "runtime_profile": "claude-code-worker",
    },
    # Reads and judges. A reviewer that cannot write cannot accidentally
    # "fix" the thing it was asked to assess, which is the failure this
    # preset exists to make impossible rather than merely discouraged.
    "reviewer": {
        "permission_policy": "deny_all",
        "runtime_profile": "claude-code-reviewer",
    },
    # Routes work and merges. Still `allowlist`: holding master authority is
    # not a reason to hold broader RUNTIME permissions, and conflating the
    # two is how a routing seat quietly becomes the most privileged process
    # on the machine.
    "master": {
        "permission_policy": "allowlist",
        "runtime_profile": "claude-code-master",
    },
}


def resolve(role):
    """The preset name for an operator-chosen `role`. Never raises.

    Exact match only -- see the module docstring on why this is not a
    substring test. An unknown or missing role gets DEFAULT_PRESET.
    """
    if isinstance(role, str) and role in PRESETS:
        return role
    return DEFAULT_PRESET


def policy_for(preset):
    return PRESETS[preset]["permission_policy"]


def profile_for(preset):
    return PRESETS[preset]["runtime_profile"]


def is_broader(requested, approved):
    """Does `requested` grant more than `approved`?

    Unknown values are treated as broader than anything known rather than as
    equal to it: a policy this module does not recognise is not a policy it
    can vouch for. The route validates the enum before reaching here, so this
    is a floor, not the primary check.
    """
    return _RANK.get(requested, max(_RANK.values()) + 1) > _RANK.get(approved, -1)
