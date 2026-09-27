"""Tests for the FILES family: the sandboxed FileStore helper and the thin
core.file.* node wrappers. Covers a write+read roundtrip, append, idempotent
delete, list, and (critically) that a path-traversal attempt is blocked."""
import asyncio

import pytest

from boltjar.file_store import FileStore, PathEscapeError, _resolve
import boltjar.nodes.core  # noqa: F401  (registers the core nodes)


# ---------------------------------------------------------------- FileStore unit
def test_write_read_roundtrip(tmp_path):
    fs = FileStore(root=tmp_path)
    rel = fs.write("notes/hello.txt", "hi there")
    assert rel == "notes/hello.txt"
    assert fs.read("notes/hello.txt") == "hi there"
    # parent dirs were created under the sandbox
    assert (tmp_path / "notes" / "hello.txt").is_file()


def test_read_missing_is_empty(tmp_path):
    fs = FileStore(root=tmp_path)
    assert fs.read("nope.txt") == ""


def test_write_creates_parent_dirs(tmp_path):
    fs = FileStore(root=tmp_path)
    fs.write("a/b/c/deep.json", "{}")
    assert (tmp_path / "a" / "b" / "c" / "deep.json").read_text() == "{}"


def test_append(tmp_path):
    fs = FileStore(root=tmp_path)
    fs.write("log.txt", "one\n")
    fs.append("log.txt", "two\n")
    fs.append("fresh.txt", "first\n")  # append creates an absent file
    assert fs.read("log.txt") == "one\ntwo\n"
    assert fs.read("fresh.txt") == "first\n"


def test_delete_idempotent(tmp_path):
    fs = FileStore(root=tmp_path)
    fs.write("gone.txt", "bye")
    assert (tmp_path / "gone.txt").is_file()
    fs.delete("gone.txt")
    assert not (tmp_path / "gone.txt").exists()
    # deleting again is a silent no-op, not an error
    fs.delete("gone.txt")
    fs.delete("never-existed.txt")


def test_list(tmp_path):
    fs = FileStore(root=tmp_path)
    fs.write("dir/b.txt", "b")
    fs.write("dir/a.txt", "a")
    fs.write("dir/sub/c.txt", "c")
    # sorted entry names directly under the dir (the subdir name, not its file)
    assert fs.list("dir") == ["a.txt", "b.txt", "sub"]
    # root list, plus a missing dir -> []
    assert fs.list("") == ["dir"]
    assert fs.list("does/not/exist") == []


# ------------------------------------------------------------- path-safety / escapes
@pytest.mark.parametrize("evil", [
    "../escape.txt",
    "../../escape.txt",
    "notes/../../escape.txt",
    "a/b/../../../escape.txt",
])
def test_traversal_is_blocked(tmp_path, evil):
    fs = FileStore(root=tmp_path)
    with pytest.raises(PathEscapeError):
        fs.write(evil, "pwned")
    with pytest.raises(PathEscapeError):
        fs.read(evil)
    # and the escape target was never created outside the sandbox
    assert not (tmp_path.parent / "escape.txt").exists()


def test_absolute_path_is_clamped_inside_sandbox(tmp_path):
    # an absolute input is stripped to its tail and joined UNDER the root, never
    # honoured as a real absolute path (no jump to /etc or C:\).
    fs = FileStore(root=tmp_path)
    rel = fs.write("/etc/passwd", "x")
    assert rel == "etc/passwd"
    assert (tmp_path / "etc" / "passwd").is_file()


def test_nested_relative_path_stays_inside(tmp_path):
    # a legit deep relative path resolves fine (it does not escape)
    target = _resolve(tmp_path, "a/b/c.txt")
    assert str(tmp_path.resolve()) in str(target)


# --------------------------------------------------------------------- node wrappers
def _file_store_patch(monkeypatch, tmp_path):
    from boltjar import server
    fs = FileStore(root=tmp_path)
    monkeypatch.setattr(server, "FILE_STORE", fs)
    return fs


def _make(node_id, config):
    """Instantiate a registered node with its _node_cfg, like the runtime does."""
    from boltjar.sdk import NODE_REGISTRY
    spec = NODE_REGISTRY[node_id]
    inst = spec.cls()
    inst._node_cfg = config
    inst._node_id = "n"
    return inst


def test_write_node_then_read_node(tmp_path, monkeypatch):
    _file_store_patch(monkeypatch, tmp_path)
    w = _make("core.file.write", {"path": "out/data.txt"})
    out = asyncio.run(w.run(trigger="go", content="payload"))
    assert out["trigger"] is True
    assert out["path"] == "out/data.txt"

    r = _make("core.file.read", {"path": "out/data.txt"})
    assert r.run() == {"text": "payload"}


def test_write_node_path_input_overrides_knob(tmp_path, monkeypatch):
    _file_store_patch(monkeypatch, tmp_path)
    w = _make("core.file.write", {"path": "knob.txt"})
    out = asyncio.run(w.run(trigger="go", path="wired.txt", content="via wire"))
    assert out["path"] == "wired.txt"

    r = _make("core.file.read", {"path": "wired.txt"})
    assert r.run() == {"text": "via wire"}


def test_append_node(tmp_path, monkeypatch):
    _file_store_patch(monkeypatch, tmp_path)
    a = _make("core.file.append", {"path": "j.txt"})
    asyncio.run(a.run(trigger="go", content="a"))
    asyncio.run(a.run(trigger="go", content="b"))
    r = _make("core.file.read", {"path": "j.txt"})
    assert r.run() == {"text": "ab"}


def test_delete_node_idempotent(tmp_path, monkeypatch):
    _file_store_patch(monkeypatch, tmp_path)
    asyncio.run(_make("core.file.write", {"path": "d.txt"}).run(trigger="go", content="x"))
    d = _make("core.file.delete", {"path": "d.txt"})
    out = asyncio.run(d.run(trigger="go"))
    assert out["trigger"] is True
    # second delete: still done, no error (idempotent)
    assert asyncio.run(d.run(trigger="go"))["trigger"] is True
    assert _make("core.file.read", {"path": "d.txt"}).run() == {"text": ""}


def test_list_node(tmp_path, monkeypatch):
    _file_store_patch(monkeypatch, tmp_path)
    asyncio.run(_make("core.file.write", {"path": "x/a.txt"}).run(trigger="go", content="a"))
    asyncio.run(_make("core.file.write", {"path": "x/b.txt"}).run(trigger="go", content="b"))
    lst = _make("core.file.list", {"path": "x"})
    assert lst.run() == {"json": ["a.txt", "b.txt"]}
    # missing dir -> []
    assert _make("core.file.list", {"path": "nope"}).run() == {"json": []}


def test_read_node_blocks_traversal_as_data(tmp_path, monkeypatch):
    # the node never raises on an escape: it surfaces the error AS DATA.
    _file_store_patch(monkeypatch, tmp_path)
    r = _make("core.file.read", {"path": "../../secret.txt"})
    out = r.run()
    assert out["text"].startswith("<file error:")


def test_write_node_blocks_traversal(tmp_path, monkeypatch):
    # the write node DOES surface the escape (it raises, caught by the runtime as
    # a node_error), and nothing is written outside the sandbox.
    _file_store_patch(monkeypatch, tmp_path)
    w = _make("core.file.write", {"path": "../escape.txt"})
    with pytest.raises(PathEscapeError):
        asyncio.run(w.run(trigger="go", content="pwned"))
    assert not (tmp_path.parent / "escape.txt").exists()
