from __future__ import annotations

import atexit
import multiprocessing as mp
import os
import subprocess
import sys
import time
from multiprocessing.shared_memory import SharedMemory
from pathlib import Path

import magscope
from magscope_motors import configure_scope_with_motors

from egrabber_camera_loader import load_egrabber_camera_class
from scope_config import load_core_settings, load_motors_settings

EGrabberCamera = load_egrabber_camera_class()

_CHILD_LAUNCH_ARG = "--magscope-child"
_STARTUP_STABILITY_SEC = float(os.getenv("MAGSCOPE_STARTUP_STABILITY_SEC", "2.0"))
_RELAUNCH_DELAY_SEC = float(os.getenv("MAGSCOPE_RELAUNCH_DELAY_SEC", "1.0"))
_MAX_LAUNCH_ATTEMPTS = int(os.getenv("MAGSCOPE_MAX_LAUNCH_ATTEMPTS", "2"))
_INSTANCE_LOCKFILE = Path(__file__).resolve().with_name(".magscope_main.pid")


def _running_in_pycharm() -> bool:
    return os.getenv("PYCHARM_HOSTED", "").strip() == "1"


def build_scope() -> magscope.MagScope:
    scope = magscope.MagScope()
    core_settings = load_core_settings()
    if core_settings is not None:
        scope.settings = core_settings
    if EGrabberCamera is not None:
        scope.camera_manager.camera = EGrabberCamera()
    configure_scope_with_motors(
        scope,
        control_column=1,
        add_plots=True,
        motors_settings=load_motors_settings(),
    )
    return scope


def _configure_multiprocessing_executable() -> None:
    """Pin multiprocessing spawn executable to the current interpreter."""
    try:
        mp.set_executable(str(Path(sys.executable).resolve()))
    except Exception:
        pass


def _shared_memory_names_for_scope(scope: magscope.MagScope) -> list[str]:
    buffer_names = ["LiveProfileBuffer", "ProfilesBuffer", "TracksBuffer", "VideoBuffer", *scope._hardware.keys()]
    names: list[str] = []
    for base in buffer_names:
        names.append(base)
        names.append(f"{base} Info")
        names.append(f"{base} Index")
        if base == "VideoBuffer":
            names.append(f"{base} Timestamps")
    return sorted(set(names))


def _unlink_shared_memory_if_exists(name: str) -> None:
    try:
        shm = SharedMemory(name=name, create=False)
    except FileNotFoundError:
        return
    except Exception:
        return
    try:
        shm.close()
    except Exception:
        pass
    try:
        shm.unlink()
    except FileNotFoundError:
        pass
    except Exception:
        pass


def _find_stale_magscope_spawn_pids(*, broad: bool = False) -> list[int]:
    if os.name != "nt":
        return []
    script_path = str(Path(__file__).resolve())
    escaped_script = script_path.replace("'", "''")
    command = (
        "$target=[regex]::Escape('{path}'); "
        "$all=Get-CimInstance Win32_Process; "
        "$parents=@{{}}; foreach($p in $all){{ $parents[[int]$p.ProcessId]=$p }}; "
        "foreach($p in $all){{ "
        "  if($p.Name -notmatch 'python|pythonw'){{ continue }}; "
        "  $cmd=$p.CommandLine; if(-not $cmd){{ continue }}; "
        "  if($cmd -notmatch '--multiprocessing-fork'){{ continue }}; "
        "  if($cmd -notmatch 'from multiprocessing\\.spawn import spawn_main'){{ continue }}; "
        "  $parent=$parents[[int]$p.ParentProcessId]; "
        "  if({broad} -or $null -eq $parent -or (($parent.CommandLine) -and ($parent.CommandLine -match $target))){{ "
        "    [int]$p.ProcessId "
        "  }} "
        "}}"
    ).format(path=escaped_script, broad="$true" if broad else "$false")
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []
    pids: list[int] = []
    for line in result.stdout.splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            pid = int(text)
        except ValueError:
            continue
        if pid > 0 and pid != os.getpid():
            pids.append(pid)
    return sorted(set(pids))


def _kill_stale_magscope_spawn_children(*, broad: bool = False) -> None:
    for pid in _find_stale_magscope_spawn_pids(broad=broad):
        _kill_process_tree(pid)


def _start_scope_with_recovery() -> None:
    scope = build_scope()
    try:
        scope.start()
        return
    except FileExistsError:
        if os.getenv("MAGSCOPE_SHM_RECOVERY_DONE", "") == "1":
            raise
        # On some IDE-managed runs (notably PyCharm), lingering spawn children
        # can survive stop/restart and keep shared-memory segments alive.
        _kill_stale_magscope_spawn_children(broad=True)
        # Recover from stale shared-memory segments left by prior crashed runs.
        for name in _shared_memory_names_for_scope(scope):
            _unlink_shared_memory_if_exists(name)
        time.sleep(0.2)
        env = dict(os.environ)
        env["MAGSCOPE_SHM_RECOVERY_DONE"] = "1"
        script_path = Path(__file__).resolve()
        cmd = [sys.executable, str(script_path), *_child_passthrough_args(), _CHILD_LAUNCH_ARG]
        result = subprocess.run(cmd, cwd=str(script_path.parent), env=env, check=False)
        raise SystemExit(int(result.returncode))


def _child_passthrough_args() -> list[str]:
    return [arg for arg in sys.argv[1:] if arg != _CHILD_LAUNCH_ARG]


def _spawn_scope_child() -> subprocess.Popen[bytes]:
    script_path = Path(__file__).resolve()
    cmd = [sys.executable, str(script_path), *_child_passthrough_args(), _CHILD_LAUNCH_ARG]
    return subprocess.Popen(cmd, cwd=str(script_path.parent))


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return False
        output = (result.stdout or "").strip()
        if not output:
            return False
        if output.lower().startswith("info:"):
            return False
        return True
    try:
        os.kill(pid, 0)
    except (OSError, SystemError):
        return False
    return True


def _kill_process_tree(pid: int) -> None:
    if pid <= 0 or pid == os.getpid():
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            capture_output=True,
            text=True,
        )
        return
    try:
        os.kill(pid, 15)
    except OSError:
        pass


def _find_stale_child_launch_pids() -> list[int]:
    if os.name != "nt":
        return []
    script_path = str(Path(__file__).resolve()).lower().replace("'", "''")
    script_name = Path(__file__).name.lower().replace("'", "''")
    command = (
        "$target='{path}'; "
        "$script=' {name} '; "
        "$script_end=' {name}'; "
        "$script_flag=' {name} --magscope-child'; "
        "Get-CimInstance Win32_Process | ForEach-Object {{ "
        "  if($_.Name -notmatch 'python|pythonw'){{ return }}; "
        "  $cmd=[string]$_.CommandLine; if(-not $cmd){{ return }}; "
        "  $lc=$cmd.ToLowerInvariant(); "
        "  if($lc.Contains('--magscope-child') -and "
        "     ($lc.Contains($target) -or $lc.Contains($script) -or $lc.EndsWith($script_end) -or $lc.Contains($script_flag))){{ "
        "    [int]$_.ProcessId "
        "  }} "
        "}}"
    ).format(path=script_path, name=script_name)
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []
    pids: list[int] = []
    for line in result.stdout.splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            pid = int(text)
        except ValueError:
            continue
        if pid > 0 and pid != os.getpid():
            pids.append(pid)
    return sorted(set(pids))


def _find_other_main_pids() -> list[int]:
    current_pid = os.getpid()
    script_path = str(Path(__file__).resolve())
    pids: list[int] = []

    if os.name != "nt":
        return pids

    escaped_script = script_path.replace("'", "''")
    command = (
        "$target=[regex]::Escape('{path}'); "
        "Get-CimInstance Win32_Process | "
        "Where-Object {{ $_.Name -match 'python|pythonw' -and $_.CommandLine -match $target }} | "
        "Select-Object -ExpandProperty ProcessId"
    ).format(path=escaped_script)
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return pids

    for line in result.stdout.splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            pid = int(text)
        except ValueError:
            continue
        if pid <= 0 or pid == current_pid:
            continue
        pids.append(pid)
    return sorted(set(pids))


def _read_lock_pid() -> int | None:
    try:
        text = _INSTANCE_LOCKFILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except OSError:
        return None
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _write_lock_pid(pid: int) -> None:
    tmp = _INSTANCE_LOCKFILE.with_suffix(".pid.tmp")
    tmp.write_text(str(pid), encoding="utf-8")
    tmp.replace(_INSTANCE_LOCKFILE)


def _release_lock_if_owned() -> None:
    if not _INSTANCE_LOCKFILE.exists():
        return
    pid = _read_lock_pid()
    if pid == os.getpid():
        try:
            _INSTANCE_LOCKFILE.unlink()
        except OSError:
            pass


def _claim_latest_instance() -> None:
    previous_pid = _read_lock_pid()
    current_pid = os.getpid()
    killed_any = False

    for pid in _find_other_main_pids():
        if _pid_exists(pid):
            _kill_process_tree(pid)
            killed_any = True

    if previous_pid is not None and previous_pid != current_pid and _pid_exists(previous_pid):
        _kill_process_tree(previous_pid)
        killed_any = True

    for pid in _find_stale_child_launch_pids():
        if _pid_exists(pid):
            _kill_process_tree(pid)
            killed_any = True

    if killed_any:
        time.sleep(0.5)

    _write_lock_pid(current_pid)
    atexit.register(_release_lock_if_owned)


def _exited_within(process: subprocess.Popen[bytes], timeout_sec: float) -> bool:
    try:
        process.wait(timeout=timeout_sec)
        return True
    except subprocess.TimeoutExpired:
        return False


def _launch_with_single_instance_restart() -> int:
    for attempt in range(1, max(1, _MAX_LAUNCH_ATTEMPTS) + 1):
        child = _spawn_scope_child()

        # Only treat launch as successful after it stays alive for a stability window.
        if not _exited_within(child, _STARTUP_STABILITY_SEC):
            return child.wait()

        # Early exit usually means this run only triggered shutdown of a currently open singleton instance.
        if attempt < _MAX_LAUNCH_ATTEMPTS:
            time.sleep(_RELAUNCH_DELAY_SEC)
            continue

        if child.returncode not in (0, None):
            print(f"MagScope child exited early with code {child.returncode}.", file=sys.stderr)
        return int(child.returncode or 0)

    return 1


if __name__ == "__main__":
    _configure_multiprocessing_executable()
    if _CHILD_LAUNCH_ARG in sys.argv:
        _start_scope_with_recovery()
    elif _running_in_pycharm():
        _claim_latest_instance()
        _start_scope_with_recovery()
    else:
        _claim_latest_instance()
        raise SystemExit(_launch_with_single_instance_restart())
