"""
Ephemeral build directory (hybrid deploy): generated website/audio/RSS live here, then upload to S3.

- NEWZYX_EPHEMERAL=1 (default): tempfile directory per process; safe for Pi SD cards.
- NEWZYX_EPHEMERAL=0: use repo root (legacy local website/).
- NEWZYX_WORKSPACE=/path: explicit workspace (never auto-deleted after a run).
"""
import os
import shutil
import tempfile

from newzyx.config import PROJECT_ROOT

_TMP_PREFIX = "newzyx_"

_workspace_root = None
_workspace_is_ephemeral_tmp = False


def project_website_dir():
    """Static template and artwork under the repo (read-only inputs)."""
    return os.path.join(PROJECT_ROOT, "website")


def _temp_root():
    return tempfile.gettempdir()


def cleanup_orphaned_temp_workspaces(keep_path=None):
    """
    Remove leftover /tmp/newzyx_* dirs from killed or crashed runs.

    Safe for the usual single daily job; skips the active workspace if provided.
    """
    keep = os.path.abspath(keep_path) if keep_path else None
    root = _temp_root()
    try:
        names = os.listdir(root)
    except OSError:
        return 0

    removed = 0
    for name in names:
        if not name.startswith(_TMP_PREFIX):
            continue
        path = os.path.join(root, name)
        if keep and os.path.abspath(path) == keep:
            continue
        if not os.path.isdir(path):
            continue
        shutil.rmtree(path, ignore_errors=True)
        removed += 1
    return removed


def init_workspace(ephemeral=None, explicit_path=None):
    """
    Call once at pipeline start. If unset, workspace defaults to PROJECT_ROOT until this runs.
    """
    global _workspace_root, _workspace_is_ephemeral_tmp
    # Clear leftovers from prior killed runs before creating a fresh temp dir.
    cleanup_orphaned_temp_workspaces()
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
    """Remove only tempfile dirs from init; never deletes explicit WORKSPACE or PROJECT_ROOT."""
    global _workspace_root, _workspace_is_ephemeral_tmp
    path = _workspace_root
    was_tmp = _workspace_is_ephemeral_tmp
    _workspace_root = None
    _workspace_is_ephemeral_tmp = False
    if was_tmp and path and os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)
    # Also sweep any other orphaned newzyx_* temp dirs (e.g. from SIGKILL).
    removed = cleanup_orphaned_temp_workspaces()
    if was_tmp or removed:
        print(
            f"[newzyx] Cleaned workspace temp"
            + (f" (+{removed} orphaned)" if removed else ""),
            flush=True,
        )
