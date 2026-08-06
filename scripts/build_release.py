from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import stat
import tempfile
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

try:
    from .audit_release import audit_tree, format_findings
except ImportError:  # Direct script execution.
    from audit_release import audit_tree, format_findings


ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST_PATH = ROOT / "PUBLIC_RELEASE_FILES.txt"
VERSION_PATTERN = re.compile(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?")


@dataclass(frozen=True)
class ReleasePayload:
    relative: Path
    content: bytes
    mode: int


def app_version(root: Path = ROOT) -> str:
    source = (root / "clean_my_codex" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^APP_VERSION\s*=\s*["\']([^"\']+)["\']', source, re.MULTILINE)
    if not match:
        raise RuntimeError("APP_VERSION was not found")
    version = match.group(1)
    if not VERSION_PATTERN.fullmatch(version):
        raise RuntimeError(f"APP_VERSION is not a safe release version: {version}")
    return version


def load_allowlist(path: Path = ALLOWLIST_PATH) -> list[Path]:
    entries: list[Path] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "\\" in line or any(ord(character) < 32 for character in line):
            raise RuntimeError(f"Unsafe cross-platform release allowlist entry: {line!r}")
        relative = Path(line)
        if relative.is_absolute() or ".." in relative.parts:
            raise RuntimeError(f"Unsafe release allowlist entry: {line}")
        entries.append(relative)
    if len(entries) != len(set(entries)):
        raise RuntimeError("PUBLIC_RELEASE_FILES.txt contains duplicate entries")
    return entries


def _copy_allowlisted(root: Path, stage: Path, entries: list[Path]) -> None:
    root_resolved = root.resolve()
    for relative in entries:
        lexical_source = root_resolved / relative
        current = root_resolved
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise RuntimeError(f"Release source contains a symbolic link: {relative}")
        source = lexical_source.resolve()
        try:
            source.relative_to(root_resolved)
        except ValueError as exc:
            raise RuntimeError(f"Release source escaped the project root: {relative}") from exc
        if not source.is_file():
            raise RuntimeError(f"Release source is missing or not a regular file: {relative}")
        destination = stage / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _capture_stage(stage: Path, entries: list[Path]) -> list[ReleasePayload]:
    expected = {entry.as_posix() for entry in entries}
    actual = {
        path.relative_to(stage).as_posix()
        for path in stage.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    if actual != expected:
        raise RuntimeError("Release stage inventory changed before capture")

    payloads: list[ReleasePayload] = []
    for relative in sorted(entries, key=lambda value: value.as_posix()):
        source = stage / relative
        before = source.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise RuntimeError(f"Release stage contains an unsafe file: {relative}")
        content = source.read_bytes()
        after = source.lstat()
        stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_mode")
        if any(getattr(before, field) != getattr(after, field) for field in stable_fields):
            raise RuntimeError(f"Release file changed while being captured: {relative}")
        payloads.append(
            ReleasePayload(
                relative=relative,
                content=content,
                mode=stat.S_IMODE(after.st_mode),
            )
        )

    final_actual = {
        path.relative_to(stage).as_posix()
        for path in stage.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    if final_actual != expected:
        raise RuntimeError("Release stage inventory changed during capture")
    return payloads


def _audit_payloads(payloads: list[ReleasePayload]) -> None:
    with tempfile.TemporaryDirectory(prefix="clean-my-codex-release-audit-") as temporary:
        root = Path(temporary)
        for payload in payloads:
            destination = root / payload.relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(payload.content)
            os.chmod(destination, payload.mode)
        findings = audit_tree(root)
        if findings:
            raise RuntimeError(format_findings(findings))


def _write_deterministic_zip(
    release_name: str,
    payloads: list[ReleasePayload],
    archive_path: Path,
) -> None:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{archive_path.name}.",
        suffix=".tmp",
        dir=archive_path.parent,
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        with zipfile.ZipFile(
            temporary_path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as archive:
            for payload in payloads:
                relative = (Path(release_name) / payload.relative).as_posix()
                if "\\" in relative or ".." in Path(relative).parts:
                    raise RuntimeError(f"Unsafe ZIP member path: {relative}")
                info = zipfile.ZipInfo(relative, date_time=(2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = payload.mode << 16
                archive.writestr(info, payload.content)
        if archive_path.is_symlink():
            raise RuntimeError("Release archive cannot be a symbolic link")
        os.replace(temporary_path, archive_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


@contextmanager
def _release_lock(output: Path):
    lock_path = output / ".clean-my-codex-release.lock"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except FileExistsError as exc:
        raise RuntimeError("Another release build is already using this output directory") from exc
    try:
        os.close(descriptor)
        yield
    finally:
        if lock_path.is_file() and not lock_path.is_symlink():
            lock_path.unlink()


def build_release(
    root: Path = ROOT,
    output_dir: Path | None = None,
    replace_existing: bool = False,
) -> dict[str, Path | str | int]:
    root = root.resolve()
    output = (output_dir or (root / "release")).expanduser().resolve()
    version = app_version(root)
    release_name = f"clean-my-codex-v{version}"
    stage = output / release_name
    archive_path = output / f"{release_name}.zip"
    checksum_path = output / f"{release_name}.zip.sha256"

    if stage.parent != output or archive_path.parent != output or checksum_path.parent != output:
        raise RuntimeError("Release outputs must be direct children of the output directory")
    if output == root or root.is_relative_to(output):
        raise RuntimeError("The release output cannot be the project root or one of its parents")
    if stage == root or root.is_relative_to(stage):
        raise RuntimeError("The release stage cannot overlap the source tree")

    output.mkdir(parents=True, exist_ok=True)
    with _release_lock(output):
        if stage.is_symlink() or archive_path.is_symlink() or checksum_path.is_symlink():
            raise RuntimeError("Release outputs cannot be symbolic links")
        existing_outputs = [path for path in (stage, archive_path, checksum_path) if path.exists()]
        if existing_outputs and not replace_existing:
            names = ", ".join(path.name for path in existing_outputs)
            raise RuntimeError(f"Release outputs already exist ({names}); pass --replace to rebuild them")
        if stage.exists():
            if not stage.is_dir():
                raise RuntimeError(f"Release stage is not a directory: {stage}")
            shutil.rmtree(stage)
        for generated in (archive_path, checksum_path):
            if generated.exists():
                generated.unlink()
        stage.mkdir(parents=True)

        entries = load_allowlist(root / "PUBLIC_RELEASE_FILES.txt")
        _copy_allowlisted(root, stage, entries)
        payloads = _capture_stage(stage, entries)
        _audit_payloads(payloads)

        _write_deterministic_zip(release_name, payloads, archive_path)
        digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
        checksum_descriptor, checksum_name = tempfile.mkstemp(
            prefix=f".{checksum_path.name}.",
            suffix=".tmp",
            dir=checksum_path.parent,
        )
        checksum_temporary = Path(checksum_name)
        try:
            with os.fdopen(checksum_descriptor, "w", encoding="ascii") as handle:
                handle.write(f"{digest}  {archive_path.name}\n")
                handle.flush()
                os.fsync(handle.fileno())
            if checksum_path.is_symlink():
                raise RuntimeError("Release checksum cannot be a symbolic link")
            os.replace(checksum_temporary, checksum_path)
        finally:
            if checksum_temporary.exists():
                checksum_temporary.unlink()
    return {
        "name": release_name,
        "version": version,
        "stage": stage,
        "archive": archive_path,
        "checksum": checksum_path,
        "sha256": digest,
        "file_count": len(entries),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build an audited Clean My Codex source release")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = build_release(output_dir=args.output_dir, replace_existing=args.replace)
    except RuntimeError as exc:
        print(exc)
        return 1
    print(f"Release: {result['stage']}")
    print(f"Archive: {result['archive']}")
    print(f"SHA-256: {result['sha256']}")
    print(f"Files: {result['file_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
