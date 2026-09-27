"""Launch with system: one entry in the account's own startup place, written
for this install and deleted exactly, never a system service. Every test writes
into a stand-in account under tmp_path; nothing is registered for real."""
from __future__ import annotations

import pathlib
import plistlib
import re
import subprocess
import sys

import pytest

from boltjar import autostart
from local_client import local_client

# a folder name with every character a quoting slip trips on
NASTY = "Ada Lovelace (x86) & Co 100% Café"


def account(monkeypatch, tmp_path, platform: str, **env) -> pathlib.Path:
    home = tmp_path / "home"
    if platform == "win32":
        env.setdefault("APPDATA", str(home / "AppData" / "Roaming"))
    monkeypatch.setattr(autostart, "_account", lambda: (platform, env, home))
    return home


def install(tmp_path) -> pathlib.Path:
    root = tmp_path / NASTY / "Boltjar"
    root.mkdir(parents=True)
    return root


# ---------------------------------------------------------------- where it lives
@pytest.mark.parametrize("platform, parts", [
    ("win32", ("AppData", "Roaming", "Microsoft", "Windows", "Start Menu", "Programs", "Startup",
               "Boltjar.cmd")),
    ("darwin", ("Library", "LaunchAgents", "link.boltjar.plist")),
    ("linux", (".config", "autostart", "boltjar.desktop")),
])
def test_the_entry_is_written_where_the_system_looks_and_removed_exactly(monkeypatch, tmp_path,
                                                                        platform, parts):
    home = account(monkeypatch, tmp_path, platform)
    root = install(tmp_path)
    path = home.joinpath(*parts)
    neighbour = path.parent / "someone-else.desktop"
    neighbour.parent.mkdir(parents=True)
    neighbour.write_text("not ours", encoding="utf-8")

    assert autostart.status(root)["enabled"] is False
    shown = autostart.enable(root)
    assert path.is_file()
    assert shown["enabled"] is True and shown["other"] is None
    assert pathlib.PurePath(shown["where"]) == pathlib.PurePath("~", *parts)
    assert sorted(p.name for p in path.parent.iterdir()) == sorted([path.name, neighbour.name])

    assert autostart.disable(root)["enabled"] is False
    assert not path.exists()
    assert neighbour.read_text(encoding="utf-8") == "not ours"  # only the one file goes
    assert path.parent.is_dir()


def test_xdg_config_home_moves_the_linux_entry(monkeypatch, tmp_path):
    config = tmp_path / "cfg"
    account(monkeypatch, tmp_path, "linux", XDG_CONFIG_HOME=str(config))
    autostart.enable(install(tmp_path))
    assert (config / "autostart" / "boltjar.desktop").is_file()


def test_an_entry_for_another_install_is_named_and_never_deleted(monkeypatch, tmp_path):
    home = account(monkeypatch, tmp_path, "win32")
    root = install(tmp_path)
    other = tmp_path / "old copy" / "Boltjar"
    other.mkdir(parents=True)
    autostart.enable(other)
    path = autostart.entry_path("win32", {"APPDATA": str(home / "AppData" / "Roaming")}, home)
    before = path.read_bytes()

    shown = autostart.status(root)
    assert shown["enabled"] is False
    assert shown["other"] == str(other)  # outside the home folder: shown whole
    assert autostart.disable(root)["enabled"] is False
    assert path.read_bytes() == before  # not ours to delete

    assert autostart.enable(root)["enabled"] is True  # one Boltjar starts at login
    assert autostart.status(other)["enabled"] is False


def test_the_state_is_read_from_disk(monkeypatch, tmp_path):
    home = account(monkeypatch, tmp_path, "darwin")
    root = install(tmp_path)
    autostart.enable(root)
    (home / "Library" / "LaunchAgents" / "link.boltjar.plist").unlink()  # removed by hand
    assert autostart.status(root)["enabled"] is False


# ---------------------------------------------------------------- Windows
def test_the_windows_entry_runs_start_bat_minimized_without_a_browser(tmp_path):
    root = pathlib.PureWindowsPath(r"C:\Users\Ada\Apps (x86)\Bolt & Jar 100% Café")
    text = autostart.entry_bytes("win32", root).decode("utf-8")
    assert not text.startswith("\ufeff")  # a byte order mark breaks the first line
    assert text.endswith("\r\n") and "\n" not in text.replace("\r\n", "")
    lines = text.split("\r\n")
    assert lines[0] == "@echo off"
    assert 'cd /d "C:\\Users\\Ada\\Apps (x86)\\Bolt & Jar 100%% Café" || exit /b 1' in lines
    assert lines[-2] == 'start "Boltjar" /min cmd /c .\\start.bat --no-browser'
    assert autostart.target_of("win32", text.encode("utf-8")) == str(root)


@pytest.mark.skipif(sys.platform != "win32", reason="runs cmd.exe")
def test_cmd_reads_the_install_folder_back_exactly(tmp_path):
    """The .cmd as written, up to its last line, run by cmd.exe itself: `cd`
    prints where it landed. The last line (which would start Boltjar) is
    replaced, so nothing is launched."""
    root = install(tmp_path)
    lines = autostart.windows_lines(root)
    assert lines[-1].startswith('start "Boltjar"')
    probe = tmp_path / "probe.cmd"
    probe.write_bytes(("\r\n".join(lines[:-1] + ["cd"]) + "\r\n").encode("utf-8"))
    run = subprocess.run(["cmd", "/d", "/c", str(probe)], capture_output=True, timeout=30)
    assert run.returncode == 0, run.stderr
    assert run.stdout.decode("utf-8").strip() == str(root)


# ---------------------------------------------------------------- macOS
def test_the_macos_entry_is_a_launch_agent_that_runs_at_login(monkeypatch, tmp_path):
    root = pathlib.PurePosixPath(f"/Users/ada/{NASTY}/Boltjar")
    entry = plistlib.loads(autostart.entry_bytes("darwin", root))
    assert entry["Label"] == "link.boltjar"
    assert entry["RunAtLoad"] is True
    assert entry["ProgramArguments"] == ["/bin/sh", f"{root}/start.sh", "--no-browser"]
    assert entry["WorkingDirectory"] == str(root)
    assert entry["StandardOutPath"] == entry["StandardErrorPath"] == f"{root}/user/logs/boltjar.log"
    assert "KeepAlive" not in entry  # it starts once at login, never respawns


def test_turning_it_on_on_macos_makes_the_log_folder(monkeypatch, tmp_path):
    account(monkeypatch, tmp_path, "darwin")
    root = install(tmp_path)
    autostart.enable(root)
    assert (root / "user" / "logs").is_dir()  # launchd does not create it


# ---------------------------------------------------------------- Linux
def _desktop_exec_args(line: str) -> list[str]:
    """Exec= as the Desktop Entry spec reads it: string escapes first, then the
    quoting, then %% becomes %."""
    value = line.split("=", 1)[1]
    value = re.sub(r"\\(.)", lambda m: {"s": " ", "n": "\n", "t": "\t", "r": "\r"}.get(m.group(1), m.group(1)), value)
    args = []
    for token in re.findall(r'"((?:[^"\\]|\\.)*)"|(\S+)', value):
        quoted, bare = token
        arg = re.sub(r'\\(["`$\\])', r"\1", quoted) if quoted else bare
        args.append(arg.replace("%%", "%"))
    return args


def test_the_linux_entry_quotes_the_path_for_the_desktop(tmp_path):
    root = pathlib.PurePosixPath('/home/ada/it\'s "quoted" $HOME `x` back\\slash & (x86) 100% Café')
    text = autostart.entry_bytes("linux", root).decode("utf-8")
    lines = text.splitlines()
    assert lines[0] == "[Desktop Entry]"
    assert "Type=Application" in lines and "Terminal=false" in lines
    exec_line = next(line for line in lines if line.startswith("Exec="))
    # start.sh --no-browser, its output appended to the log: both paths reach sh
    # as $0 and $1, never inside the script
    assert _desktop_exec_args(exec_line) == [
        "/bin/sh", "-c", 'exec /bin/sh "$0" --no-browser >>"$1" 2>&1',
        f"{root}/start.sh", f"{root}/user/logs/boltjar.log"]
    assert autostart.target_of("linux", text.encode("utf-8")) == str(root)


@pytest.mark.skipif(sys.platform == "win32", reason="runs /bin/sh")
def test_sh_runs_the_linux_entry_with_its_output_in_the_log(tmp_path):
    """The Exec line as a desktop reads it, run by sh itself against a stub
    start.sh in a folder with a nasty name."""
    root = install(tmp_path)
    (root / "start.sh").write_text('echo "args: $*"\necho "to stderr" >&2\n', encoding="utf-8")
    (root / "user" / "logs").mkdir(parents=True)
    text = autostart.entry_bytes("linux", root).decode("utf-8")
    args = _desktop_exec_args(next(line for line in text.splitlines() if line.startswith("Exec=")))
    run = subprocess.run(args, capture_output=True, timeout=30, cwd=root)
    assert run.returncode == 0, run.stderr
    assert run.stdout == b"" and run.stderr == b""
    log = (root / "user" / "logs" / "boltjar.log").read_text(encoding="utf-8")
    assert "args: --no-browser" in log and "to stderr" in log


def test_turning_it_on_on_linux_makes_the_log_folder(monkeypatch, tmp_path):
    account(monkeypatch, tmp_path, "linux")
    root = install(tmp_path)
    autostart.enable(root)
    assert (root / "user" / "logs").is_dir()  # the redirect cannot create it


# ---------------------------------------------------------------- the API
def test_the_settings_api_turns_it_on_and_off(monkeypatch, tmp_path):
    home = account(monkeypatch, tmp_path, "linux")
    path = home / ".config" / "autostart" / "boltjar.desktop"
    with local_client() as client:
        body = client.patch("/api/settings", json={"launch_with_system": True}).json()
        assert body["settings"]["launch_with_system"] is True
        assert body["autostart"]["where"].replace("\\", "/") == "~/.config/autostart/boltjar.desktop"
        assert path.is_file()
        assert f"Path={autostart.ROOT}".replace("\\", "\\\\") in path.read_text(encoding="utf-8")
        body = client.patch("/api/settings", json={"launch_with_system": False}).json()
        assert body["settings"]["launch_with_system"] is False
    assert not path.exists()


def test_a_remote_peer_cannot_turn_it_on(monkeypatch, tmp_path):
    home = account(monkeypatch, tmp_path, "linux")
    with local_client(client=("192.168.1.20", 50000)) as client:
        shown = client.get("/api/settings").json()
        assert shown["here"] is False and "launch_with_system" in shown["local_only"]
        r = client.patch("/api/settings", json={"launch_with_system": True})
    assert r.status_code == 403
    assert "only on this computer" in r.json()["error"]
    assert not (home / ".config" / "autostart").exists()


def test_a_request_passed_on_by_a_proxy_cannot_turn_it_off(monkeypatch, tmp_path):
    home = account(monkeypatch, tmp_path, "linux")
    autostart.enable(autostart.ROOT)
    with local_client() as client:
        r = client.patch("/api/settings", json={"launch_with_system": False},
                         headers={"X-Forwarded-For": "203.0.113.9"})
    assert r.status_code == 403
    assert (home / ".config" / "autostart" / "boltjar.desktop").is_file()


def test_a_remote_peer_cannot_turn_on_start_ollama_with_boltjar(monkeypatch, tmp_path):
    """It makes every later launch start a program on this computer."""
    from boltjar import settings
    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    account(monkeypatch, tmp_path, "linux")
    with local_client(client=("192.168.1.20", 50000)) as client:
        assert "start_ollama" in client.get("/api/settings").json()["local_only"]
        r = client.patch("/api/settings", json={"start_ollama": True, "resume_workflows": True})
    assert r.status_code == 403
    assert "Start Ollama with Boltjar" in r.json()["error"]
    assert "only on this computer" in r.json()["error"]
    assert not settings.PATH.exists()  # nothing of the change was made


def test_a_remote_peer_may_still_change_resume_workflows(monkeypatch, tmp_path):
    """A browser that reaches the editor can turn graphs On already."""
    from boltjar import settings
    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    account(monkeypatch, tmp_path, "linux")
    with local_client(client=("192.168.1.20", 50000)) as client:
        r = client.patch("/api/settings", json={"resume_workflows": True})
    assert r.status_code == 200
    assert r.json()["settings"]["resume_workflows"] is True
