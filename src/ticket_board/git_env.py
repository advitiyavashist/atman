"""Environment isolation for local Git probes and worker launchers."""

import os
from typing import Dict, Mapping, Optional


def clean_git_env(environ: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    """Copy an environment without inherited Git overrides.

    Pair with an explicit cwd or ``git -C``. Intended for independent local
    repository operations, not a Git hook that must retain its caller's state.
    All GIT_* keys are removed, including config/discovery overrides; unrelated
    settings (PATH, HOME, credentials for non-Git clients) remain untouched.
    """
    source = os.environ if environ is None else environ
    return {key: value for key, value in source.items() if not key.startswith("GIT_")}
