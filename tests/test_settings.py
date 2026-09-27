"""The install's settings (Settings > General): every one off until a person
turns it on, saved in user/data/settings.json and read back after a restart."""
from __future__ import annotations

import json
import pathlib

import pytest

from boltjar import settings


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "data" / "settings.json"
    monkeypatch.setattr(settings, "PATH", path)
    return path


def test_every_setting_is_off_on_a_fresh_install(store):
    assert settings.load() == {"start_ollama": False, "resume_workflows": False}
    assert not store.exists()  # reading writes nothing


def test_a_change_persists_across_a_restart(store):
    assert settings.update({"resume_workflows": True})["resume_workflows"] is True
    assert json.loads(store.read_text(encoding="utf-8")) == {"resume_workflows": True}
    # a new server process reads the file again
    assert settings.load() == {"start_ollama": False, "resume_workflows": True}


@pytest.mark.parametrize("changes", [
    {"no_such_setting": True},
    {"start_ollama": "yes"},
    {"start_ollama": 1},
    {"resume_workflows": True, "start_ollama": None},
])
def test_a_bad_change_is_refused_and_nothing_is_written(store, changes):
    with pytest.raises(ValueError):
        settings.update(changes)
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
