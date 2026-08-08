"""
Ephemeral build directory (hybrid deploy): generated website/audio/RSS live here, then upload to S3.

- NEWZYX_EPHEMERAL=1 (default): tempfile directory per process; safe for Pi SD cards.
- NEWZYX_EPHEMERAL=0: use repo root (legacy local website/).
- NEWZYX_WORKSPACE=/path: explicit workspace (never auto-deleted after a run).

Finished / orphaned temp workspaces are moved to data/workspace_archive/ (not deleted).
"""
import os
import shutil
import tempfile
from datetime import datetime

from newzyx.config import PROJECT_ROOT

_TMP_PREFIX = "newzyx_"

_workspace_root = None
_workspace_is_ephemeral_tmp = False


def project_website_dir():
    """Static template and artwork under the repo (read-only inputs)."""
    return os.path.join(PROJECT_ROOT, "website")


def archive_dir():
    """Where finished/orphaned ephemeral workspaces are kept."""
    override = os.environ.get("NEWZYX_ARCHIVE_DIR", "").strip()
    if override:
        path = os.path.abspath(override)
    else:
        path = os.path.join(PROJECT_ROOT, "data", "workspace_archive")
    os.makedirs(path, exist_ok=True)
    return path


def _temp_root():
    return tempfile.gettempdir()


def _archive_workspace(path, reason="run"):
    """
    Move a temp workspace into the archive folder. Returns dest path, or None on skip/fail.
    Never deletes the source if the move fails.
    """
    if not path or not os.path.isdir(path):
        return None
    abs_path = os.path.abspath(path)
    # Never archive the project root or an explicit non-temp workspace outside /tmp.
    if abs_path == os.path.abspath(PROJECT_ROOT):
        return None

    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    base = os.path.basename(abs_path.rstrip(os.sep)) or "workspace"
    dest_name = f"{stamp}_{reason}_{base}"
    dest = os.path.join(archive_dir(), dest_name)
    # Avoid collisions if two cleanups happen in the same second.
    n = 1
    while os.path.exists(dest):
        dest = os.path.join(archive_dir(), f"{dest_name}_{n}")
        n += 1

    try:
        shutil.move(abs_path, dest)
        return dest
    except OSError as e:
        print(f"[newzyx] Warning: could not archive {abs_path}: {e}", flush=True)
        return None


def archive_orphaned_temp_workspaces(keep_path=None):
    """
    Move leftover /tmp/newzyx_* dirs from killed or crashed runs into the archive.

    Safe for the usual single daily job; skips the active workspace if provided.
    Returns the number of workspaces archived.
    """
    keep = os.path.abspath(keep_path) if keep_path else None
    root = _temp_root()
    try:
        names = os.listdir(root)
    except OSError:
        return 0

    archived = 0
    for name in names:
        if not name.startswith(_TMP_PREFIX):
            continue
        path = os.path.join(root, name)
        if keep and os.path.abspath(path) == keep:
            continue
        if not os.path.isdir(path):
            continue
        if _archive_workspace(path, reason="orphan"):
            archived += 1
    return archived


# Back-compat alias used by older call sites / mental model.
cleanup_orphaned_temp_workspaces = archive_orphaned_temp_workspaces


def init_workspace(ephemeral=None, explicit_path=None):
    """
    Call once at pipeline start. If unset, workspace defaults to PROJECT_ROOT until this runs.
    """
    global _workspace_root, _workspace_is_ephemeral_tmp
    # Archive leftovers from prior killed runs before creating a fresh temp dir.
    archive_orphaned_temp_workspaces()
    _workspace_is_ephemeral_tmp = False
    if explicit_path:
        _workspace_root = os.path.abspath(explicit_path)
        os.makedirs(_workspace_root, exist_ok=True)
        return _workspace_root
    if ephemeral is None:
        raw = os.environ.get("NEWZYX_EPHEMERAL", "1").strip().lower()
        ephemeral = raw not in ("0", "false", "no")
    if ephemeral:
        _workspace_root = tempfile.mkdtemp(prefix=_TMP_PREFIX)
        _workspace_is_ephemeral_tmp = True
    else:
        _workspace_root = PROJECT_ROOT
    return _workspace_root


def init_workspace_from_env():
    """Preferred entry: honors NEWZYX_WORKSPACE, else NEWZYX_EPHEMERAL."""
    explicit = os.environ.get("NEWZYX_WORKSPACE", "").strip()
    if explicit:
        return init_workspace(explicit_path=explicit)
    return init_workspace()


def get_workspace():
    """Active build root (ephemeral dir or PROJECT_ROOT)."""
    if _workspace_root is not None:
        return _workspace_root
    return PROJECT_ROOT


def generated_website_dir():
    """website/ subtree for this run's outputs (HTML, MP3, feed.xml)."""
    return os.path.join(get_workspace(), "website")


def cleanup_workspace():
    """Archive this run's tempfile workspace; never deletes PROJECT_ROOT or NEWZYX_WORKSPACE."""
    global _workspace_root, _workspace_is_ephemeral_tmp
    path = _workspace_root
    was_tmp = _workspace_is_ephemeral_tmp
    _workspace_root = None
    _workspace_is_ephemeral_tmp = False

    archived_current = None
    if was_tmp and path and os.path.isdir(path):
        archived_current = _archive_workspace(path, reason="run")

    orphaned = archive_orphaned_temp_workspaces()
    if archived_current or orphaned:
        msg = "[newzyx] Archived workspace"
        if archived_current:
            msg += f" → {archived_current}"
        if orphaned:
            msg += f" (+{orphaned} orphaned)"
        print(msg, flush=True)
