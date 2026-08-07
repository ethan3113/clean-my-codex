"""Platform-specific filesystem safety helpers.

POSIX builds enforce owner-only modes. Windows builds use per-account profile
directories and inherited NTFS ACLs; chmod is not treated as an ACL boundary.
"""

from __future__ import annotations

import os
from pathlib import Path


POSIX_PERMISSIONS = os.name == "posix" and hasattr(os, "getuid")
PERMISSION_MODEL = "posix-owner-mode" if POSIX_PERMISSIONS else "account-profile-acl"


def is_link_like(path: Path) -> bool:
    """Return true for symbolic links and Windows directory junctions."""
    path = Path(path)
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction and is_junction())
    except OSError:
        return True


def set_private_mode(path: Path, mode: int) -> None:
    """Apply a POSIX mode where it is a meaningful security control."""
    if POSIX_PERMISSIONS:
        os.chmod(path, mode)


def file_mode(path: Path, default: int) -> int:
    """Return a restorable POSIX mode or a stable Windows manifest default."""
    if not POSIX_PERMISSIONS:
        return default
    return Path(path).stat().st_mode & 0o777


def owned_by_current_account(path: Path) -> bool:
    """Validate ownership on POSIX; Windows ownership is delegated to ACLs."""
    if not POSIX_PERMISSIONS:
        return True
    return Path(path).stat().st_uid == os.getuid()


def private_permissions(path: Path) -> bool:
    """Validate owner-only POSIX access or the Windows profile-ACL model."""
    path = Path(path)
    if is_link_like(path):
        return False
    if not POSIX_PERMISSIONS:
        return True
    value = path.stat()
    return value.st_uid == os.getuid() and not value.st_mode & 0o077


def private_file(path: Path) -> bool:
    path = Path(path)
    return path.is_file() and not is_link_like(path) and private_permissions(path)


def private_directory(path: Path) -> bool:
    path = Path(path)
    return path.is_dir() and not is_link_like(path) and private_permissions(path)
