"""Sandboxed filesystem I/O under a single workspace root.

Every path a node hands in is treated as RELATIVE to the store's root and is
resolved + verified to land INSIDE that root before any read or write. A path
that escapes the sandbox (``..`` segments, an absolute path, a drive letter, a
symlink that points out) is rejected with ``PathEscapeError``. This mirrors the
``_safe_key`` traversal-proofing in ``sqlite_store`` but for nested file paths:
the real, resolved path must be the root or a descendant of it.

The nodes (``core.file.read`` / ``write`` / ``append`` / ``delete`` / ``list``)
are thin wrappers over this module. Reads are lean: a missing file reads as ""
and a missing dir lists as []. Writes create parent dirs under the sandbox.
"""
from __future__ import annotations

import pathlib


class PathEscapeError(ValueError):
    """A requested path resolved outside the sandbox root and was rejected."""


def within(root: pathlib.Path, path: pathlib.Path) -> bool:
    """Whether ``path`` is ``root`` or a descendant of it, compared as given:
    resolve both first to compare the real paths (symlinks followed)."""
    return path == root or root in path.parents


def _resolve(root: pathlib.Path, path: str) -> pathlib.Path:
    """Resolve ``path`` relative to ``root`` and verify it stays inside.

    The root itself is resolved first so the containment check compares two real
    paths (handling symlinks / case / ``.`` and ``..`` in the root). An absolute
    or drive-qualified input is still joined under the root: ``Path(root, "/etc")``
    on POSIX would jump to ``/etc``, so we strip a leading separator / drive and
    treat every input as root-relative, then confirm the resolved result is the
    root or a descendant.
    """
    root = root.resolve()
    raw = pathlib.PurePath(path or "")
    # Drop any anchor (drive + leading slash) so the path can only be relative;
    # an absolute input like "/etc/passwd" or "C:\\win" becomes its tail parts.
    parts = [p for p in raw.parts if p not in (raw.anchor, "/", "\\")]
    candidate = (root / pathlib.PurePath(*parts)) if parts else root
    resolved = candidate.resolve()
    if not within(root, resolved):
        raise PathEscapeError(f"path escapes the sandbox: {path!r}")
    return resolved


class FileStore:
    """Sandboxed file operations under ``root`` (default ``user/data/files``)."""

    def __init__(self, root: pathlib.Path | str = "user/data/files") -> None:
        self.root = pathlib.Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _rel(self, resolved: pathlib.Path) -> str:
        """The POSIX-style path of ``resolved`` relative to the root (forward
        slashes, so the value reads the same on every platform)."""
        rel = resolved.resolve().relative_to(self.root.resolve())
        return rel.as_posix()

    def read(self, path: str) -> str:
        """Read a file's text. A missing file (or a directory) reads as ""."""
        target = _resolve(self.root, path)
        if not target.is_file():
            return ""
        return target.read_text(encoding="utf-8")

    def write(self, path: str, content: str) -> str:
        """Write ``content`` to ``path`` (creating parent dirs). Returns the
        resolved relative path actually written."""
        target = _resolve(self.root, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("" if content is None else str(content), encoding="utf-8")
        return self._rel(target)

    def append(self, path: str, content: str) -> str:
        """Append ``content`` to ``path`` (creating it + parent dirs if absent).
        Returns the resolved relative path."""
        target = _resolve(self.root, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as fh:
            fh.write("" if content is None else str(content))
        return self._rel(target)

    def delete(self, path: str) -> str:
        """Delete the file at ``path`` if present (idempotent: a missing file is
        a no-op). Returns the resolved relative path. Refuses to delete a
        directory (use list/read semantics; dir removal is out of scope)."""
        target = _resolve(self.root, path)
        if target.is_file():
            target.unlink()
        return self._rel(target)

    def list(self, path: str = "") -> list[str]:
        """List entry names directly under ``path`` within the sandbox. A
        missing directory (or a path that is a file) lists as []. Names are
        sorted; directories are unmarked (a plain name list)."""
        target = _resolve(self.root, path)
        if not target.is_dir():
            return []
        return sorted(entry.name for entry in target.iterdir())
