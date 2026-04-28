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

_workspace_root = None
_workspace_is_ephemeral_tmp = False


def project_website_dir():
    """Static template and artwork under the repo (read-only inputs)."""
    return os.path.join(PROJECT_ROOT, "website")


def init_workspace(ephemeral=None, explicit_path=None):
    """
    Call once at pipeline start. If unset, workspace defaults to PROJECT_ROOT until this runs.
    """
    global _workspace_root, _workspace_is_ephemeral_tmp
    _workspace_is_ephemeral_tmp = False
    if explicit_path:
        _workspace_root = os.path.abspath(explicit_path)
        os.makedirs(_workspace_root, exist_ok=True)
        return _workspace_root
    if ephemeral is None:
        raw = os.environ.get("NEWZYX_EPHEMERAL", "1").strip().lower()
        ephemeral = raw not in ("0", "false", "no")
    if ephemeral:
        _workspace_root = tempfile.mkdtemp(prefix="newzyx_")
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
