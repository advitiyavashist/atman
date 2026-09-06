#!/usr/bin/env python3
"""Install ticket-board CLI into ~/.claude/tools (flat layout for existing symlinks).

Usage:
  python3 scripts/install.py
  python3 scripts/install.py /path/to/tickets.py
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

source = Path(__file__).resolve().parents[1] / "src" / "ticket_board"
default_target = Path.home() / ".claude" / "tools" / "tickets.py"
target = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else default_target
dest_dir = target.parent
dest_dir.mkdir(parents=True, exist_ok=True)

backup = target.with_suffix(".py.before-t108-safe-clear")
if target.exists() and not backup.exists():
    shutil.copy2(target, backup)

# Flat install: tickets.py + siblings (matches historic ~/.claude/tools layout)
shutil.copy2(source / "cli.py", target)
shutil.copy2(source / "ticket_coordination.py", dest_dir / "ticket_coordination.py")
shutil.copy2(source / "board_backup.py", dest_dir / "board_backup.py")
mode = target.stat().st_mode if target.exists() else 0o755
os.chmod(target, mode | 0o111)

# Ensure ~/.local/bin/tickets points here if missing
local_bin = Path.home() / ".local" / "bin" / "tickets"
if not local_bin.exists():
    local_bin.parent.mkdir(parents=True, exist_ok=True)
    try:
        local_bin.symlink_to(target)
    except OSError:
        pass

print("Installed tickets CLI to", target)
print("Backup:", backup if backup.exists() else "(none)")
print("Safe clear: fixture boards only (.fixture-board + --yes)")
print("Also: tickets board-backup / board-restore")
