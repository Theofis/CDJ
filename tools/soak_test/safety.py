"""Fail-closed media protection. A hash audit is not a write blocker."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def inside(path, root: Path) -> bool:
    return Path(path).resolve().is_relative_to(root.resolve())


def require_read_only(root: Path) -> dict:
    root = root.resolve(strict=True)
    if os.name == "nt":
        drive = root.drive
        if len(drive) != 2 or not drive[0].isalpha() or drive[1] != ":":
            raise RuntimeError("Strict USB protection requires a local drive letter")
        script = (
            "$ErrorActionPreference='Stop'; "
            f"Get-Partition -DriveLetter '{drive[0]}' | Get-Disk | "
            "Select-Object Number,UniqueId,IsReadOnly,IsBoot,IsSystem,BusType | ConvertTo-Json -Compress"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode:
            raise RuntimeError("Cannot verify disk write protection: " + result.stderr.strip())
        evidence = json.loads(result.stdout)
        if not isinstance(evidence, dict) or evidence.get("IsReadOnly") is not True:
            raise RuntimeError(
                f"USB source {root} is NOT OS read-only. Test refused before starting CDJ. "
                "Use a hardware write blocker or set the identified USB disk read-only in an "
                "administrator terminal. No automatic disk changes are made."
            )
        return evidence
    if not os.statvfs(root).f_flag & os.ST_RDONLY:
        raise RuntimeError(f"{root} must be mounted read-only before running the test")
    return {"mount": str(root), "read_only": True, "device": root.stat().st_dev}


def install_write_guard(root: Path, violations: list[str]) -> None:
    """Additional Python audit barrier; OS protection also covers native code.

    Installed only in the disposable worker, never in the interactive app.
    """
    root = root.resolve()
    mutations = {
        "os.remove": (0,), "os.rmdir": (0,), "os.mkdir": (0,),
        "os.rename": (0, 1), "os.link": (0, 1), "os.symlink": (1,),
        "os.truncate": (0,), "os.chmod": (0,), "os.chown": (0,),
        "os.utime": (0,),
    }

    def check(event, args):
        paths = []
        if event == "open":
            path, mode, flags = args
            if (mode and any(c in mode for c in "wax+")) or (
                flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
            ):
                paths.append(path)
        elif event in mutations:
            paths = [args[i] for i in mutations[event]]
        for path in paths:
            if isinstance(path, (str, bytes, os.PathLike)):
                if inside(os.fsdecode(path), root):
                    message = f"USB_WRITE_ATTEMPT: {event}: {os.fsdecode(path)}"
                    violations.append(message)
                    raise PermissionError(message)

    sys.addaudithook(check)


def inventory(root: Path) -> dict:
    """Hash all regular files, including audio; detect additions and removals.

    Walk errors are fatal. Links/junctions are rejected, never silently skipped.
    Access time is deliberately excluded (reading can update it on writable media).
    """
    root = root.resolve(strict=True)
    files = {}
    def fail(error):
        raise error
    for directory, dirs, names in os.walk(root, onerror=fail, followlinks=False):
        for name in sorted(dirs + names):
            path = Path(directory) / name
            if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
                raise RuntimeError(f"Linked source entry is not supported: {path}")
        for name in sorted(names):
            path = Path(directory) / name
            before = path.stat()
            with path.open("rb") as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
            after = path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise RuntimeError(f"Source changed during inventory: {path}")
            files[path.relative_to(root).as_posix()] = {
                "size": after.st_size, "mtime_ns": after.st_mtime_ns, "sha256": digest,
            }
    return files


def compare(before: dict, after: dict) -> dict:
    return {
        "added": sorted(after.keys() - before.keys()),
        "removed": sorted(before.keys() - after.keys()),
        "changed": sorted(key for key in before.keys() & after.keys() if before[key] != after[key]),
    }
