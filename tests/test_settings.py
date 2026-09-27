"""The install's settings (Settings > General): every one off until a person
turns it on, saved in user/data/settings.json and read back after a restart."""
from __future__ import annotations

import json
import pathlib

import pytest

from boltjar import autostart, settings
from local_client import local_client


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "data" / "settings.json"
    monkeypatch.setattr(settings, "PATH", path)
    home = tmp_path / "account"
    monkeypatch.setattr(autostart, "_account", lambda: (
        "win32", {"APPDATA": str(home / "AppData" / "Roaming")}, home))
    return path


def test_every_setting_is_off_on_a_fresh_install(store):
    assert settings.load() == {"start_ollama": False, "resume_workflows": False}
    assert not store.exists()  # reading writes nothing


def test_the_api_serves_every_setting_off_on_a_fresh_install(store):
    with local_client() as client:
        body = client.get("/api/settings").json()
    assert body["settings"] == {"start_ollama": False, "resume_workflows": False,
                                "launch_with_system": False}
    assert body["here"] is True  # a browser on this computer
    assert body["local_only"] == ["launch_with_system", "start_ollama"]


def test_a_change_persists_across_a_restart(store):
    with local_client() as client:
        body = client.patch("/api/settings", json={"resume_workflows": True}).json()
    assert body["settings"]["resume_workflows"] is True
    assert json.loads(store.read_text(encoding="utf-8")) == {"resume_workflows": True}
    # a new server process reads the file again
    assert settings.load() == {"start_ollama": False, "resume_workflows": True}
    with local_client() as client:
        assert client.get("/api/settings").json()["settings"]["resume_workflows"] is True


@pytest.mark.parametrize("body", [
    {"no_such_setting": True},
    {"start_ollama": "yes"},
    {"start_ollama": 1},
    {"resume_workflows": True, "start_ollama": None},
    {"launch_with_system": "on"},
])
def test_a_bad_change_is_refused_and_nothing_is_written(store, body):
    with local_client() as client:
        r = client.patch("/api/settings", json=body)
    assert r.status_code == 400
    assert "error" in r.json()
    assert not store.exists()


def test_a_name_this_boltjar_does_not_know_stays_on_disk(store):
    store.parent.mkdir(parents=True)
    store.write_text(json.dumps({"from_a_newer_boltjar": 3, "start_ollama": True}), encoding="utf-8")
    assert settings.load() == {"start_ollama": True, "resume_workflows": False}
    settings.update({"resume_workflows": True})
    saved = json.loads(store.read_text(encoding="utf-8"))
    assert saved == {"from_a_newer_boltjar": 3, "start_ollama": True, "resume_workflows": True}


def test_a_broken_file_reads_as_the_defaults(store):
    store.parent.mkdir(parents=True)
    store.write_text("{not json", encoding="utf-8")
    assert settings.load() == {"start_ollama": False, "resume_workflows": False}
    store.write_text(json.dumps({"start_ollama": "true"}), encoding="utf-8")  # wrong type
    assert settings.get("start_ollama") is False


def test_a_write_leaves_no_temporary_file_behind(store):
    settings.update({"start_ollama": True})
    assert [p.name for p in store.parent.iterdir()] == ["settings.json"]


def test_paths_under_the_home_folder_show_a_tilde(tmp_path):
    home = tmp_path / "home"
    shown = settings.shown_path(home / "Library" / "LaunchAgents" / "link.boltjar.plist", home)
    assert pathlib.PurePath(shown) == pathlib.PurePath("~", "Library", "LaunchAgents", "link.boltjar.plist")
    assert settings.shown_path(tmp_path / "elsewhere", home) == str(tmp_path / "elsewhere")


def _unreadable(path: pathlib.Path):
    """Path.read_text that fails on `path` the way Windows does while another
    program (an antivirus, a backup, an indexer) holds the file open."""
    real = pathlib.Path.read_text

    def read_text(self, *args, **kwargs):
        if self == path:
            raise PermissionError(13, "The process cannot access the file")
        return real(self, *args, **kwargs)

    return read_text


def test_settings_that_cannot_be_read_are_never_written_over(store, monkeypatch):
    store.parent.mkdir(parents=True)
    store.write_text(json.dumps({"start_ollama": True, "from_a_newer_boltjar": 3}), encoding="utf-8")
    before = store.read_bytes()
    with monkeypatch.context() as m:
        m.setattr(pathlib.Path, "read_text", _unreadable(store))
        with pytest.raises(OSError):
            settings.update({"resume_workflows": True})
        with local_client() as client:
            changed = client.patch("/api/settings", json={"resume_workflows": True})
            shown = client.get("/api/settings")
    assert changed.status_code == 500 and "could not" in changed.json()["error"]
    assert shown.status_code == 500 and "could not read the settings" in shown.json()["error"]
    assert store.read_bytes() == before  # the other settings and the newer one survive


def test_a_broken_settings_file_is_set_aside_before_a_write(store):
    store.parent.mkdir(parents=True)
    store.write_text("{not json", encoding="utf-8")
    settings.update({"start_ollama": True})
    assert (store.parent / "settings.json.bad").read_text(encoding="utf-8") == "{not json"
    assert json.loads(store.read_text(encoding="utf-8")) == {"start_ollama": True}


def test_no_message_sends_a_person_to_connections():
    """The gear opens Settings (AI Providers, Secrets): no message names the
    Connections window, which the editor no longer shows."""
    import re

    root = pathlib.Path(__file__).resolve().parent.parent
    files = [*root.joinpath("boltjar").rglob("*.py"), *root.joinpath("editor", "src").rglob("*.ts*")]
    stale = [f"{path.relative_to(root)}:{n}" for path in files
             for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
             if re.search(r"\b(in|open) Connections\b", line)]
    assert stale == []
