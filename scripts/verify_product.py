"""Verify the immutable file manifest of the protected product repository."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path, PurePosixPath
import re
import sys


MANIFEST_NAME = "PRODUCT_SHA256SUMS"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?")
FORBIDDEN_CACHE_PARTS = {".mypy_cache", ".pytest_cache", ".ruff_cache", "__pycache__"}
FORBIDDEN_PRODUCT_SUFFIXES = {
    ".db",
    ".db-shm",
    ".db-wal",
    ".jks",
    ".key",
    ".keystore",
    ".log",
    ".p12",
    ".pem",
    ".pfx",
    ".pyc",
    ".pyo",
    ".sqlite",
    ".sqlite3",
}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _reject_symlinks(root: Path) -> None:
    symlinks = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if ".git" not in path.relative_to(root).parts and path.is_symlink()
    )
    if symlinks:
        raise ValueError(f"symlinks are forbidden in the product: {symlinks[:10]}")


def _product_files(root: Path) -> set[str]:
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
        and ".git" not in path.relative_to(root).parts
        and path.name != MANIFEST_NAME
    }


def _validate_product_tree(root: Path, actual_files: set[str]) -> None:
    version_path = root / "VERSION"
    if not version_path.is_file():
        raise ValueError("VERSION is missing")
    version = version_path.read_text(encoding="utf-8").strip()
    if not VERSION_RE.fullmatch(version):
        raise ValueError("VERSION is not a supported product version")
    forbidden = {"gate.env", "api_token", "mqtt_password", "tls_key"}
    leaked = sorted(
        relative for relative in actual_files if PurePosixPath(relative).name in forbidden
    )
    if leaked:
        raise ValueError(f"customer secret/config leaked into product: {leaked}")
    unsafe_artifacts = sorted(
        relative
        for relative in actual_files
        if set(PurePosixPath(relative).parts) & FORBIDDEN_CACHE_PARTS
        or PurePosixPath(relative).suffix.casefold() in FORBIDDEN_PRODUCT_SUFFIXES
    )
    if unsafe_artifacts:
        raise ValueError(
            f"cache, secret or customer-state artifacts are forbidden: "
            f"{unsafe_artifacts[:10]}"
        )
    if not any(
        relative.startswith("runtime/") and relative.endswith(".so")
        for relative in actual_files
    ):
        raise ValueError("protected Linux runtime extensions are missing")


def verify(root: Path) -> None:
    if root.is_symlink():
        raise ValueError("product root must not be a symlink")
    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f"product root is not a directory: {root}")
    _reject_symlinks(root)
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file():
        raise ValueError(f"{MANIFEST_NAME} is missing")

    expected: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        manifest_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        digest, separator, relative_name = raw_line.partition("  ")
        pure = PurePosixPath(relative_name)
        if (
            not separator
            or not SHA256_RE.fullmatch(digest)
            or not relative_name
            or pure.is_absolute()
            or ".." in pure.parts
            or pure.as_posix() != relative_name
            or "\r" in relative_name
            or "\n" in relative_name
        ):
            raise ValueError(f"invalid checksum entry on line {line_number}")
        normalized = pure.as_posix()
        if normalized in expected:
            raise ValueError(f"duplicate checksum entry: {normalized}")
        expected[normalized] = digest

    actual_files = _product_files(root)
    if set(expected) != actual_files:
        missing = sorted(actual_files - set(expected))
        extra = sorted(set(expected) - actual_files)
        raise ValueError(
            f"checksum inventory mismatch; missing={missing[:10]} extra={extra[:10]}"
        )

    for relative_name, expected_digest in sorted(expected.items()):
        path = root.joinpath(*PurePosixPath(relative_name).parts)
        actual_digest = _sha256_file(path)
        if actual_digest != expected_digest:
            raise ValueError(f"checksum mismatch: {relative_name}")

    _validate_product_tree(root, actual_files)


def write_manifest(root: Path) -> Path:
    """Atomically create the product inventory after all safety scans pass."""
    if root.is_symlink():
        raise ValueError("product root must not be a symlink")
    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f"product root is not a directory: {root}")
    _reject_symlinks(root)

    manifest_path = root / MANIFEST_NAME
    temporary_path = root / f"{MANIFEST_NAME}.tmp"
    if temporary_path.exists():
        raise ValueError(f"stale temporary checksum file exists: {temporary_path.name}")

    actual_files = _product_files(root)
    _validate_product_tree(root, actual_files)

    manifest_definition = root / "release" / "protected_modules_headless.json"
    runtime_root = root / "runtime"
    # Imports performed by the safety scanner must not mutate the product tree
    # after its inventory was captured.
    sys.dont_write_bytecode = True
    try:
        from scan_headless_release import scan_release
    except ImportError:  # Imported as ``scripts.verify_product`` in tests.
        from .scan_headless_release import scan_release
    failures = scan_release(runtime_root, manifest_definition)
    if failures:
        details = "; ".join(sorted(set(failures))[:10])
        raise ValueError(f"protected runtime scan failed: {details}")

    lines = [
        f"{_sha256_file(root.joinpath(*PurePosixPath(relative).parts))}  {relative}"
        for relative in sorted(actual_files)
    ]
    try:
        temporary_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        temporary_path.replace(manifest_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
    verify(root)
    return manifest_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", type=Path, default=Path.cwd())
    parser.add_argument(
        "--write",
        action="store_true",
        help=(
            "atomically write PRODUCT_SHA256SUMS after the protected runtime "
            "and product tree pass all safety checks"
        ),
    )
    args = parser.parse_args()
    try:
        if args.write:
            manifest_path = write_manifest(args.root)
        else:
            verify(args.root)
    except (OSError, UnicodeError, ValueError) as exc:
        raise SystemExit(f"Product verification failed: {exc}") from exc
    if args.write:
        print(f"Protected product checksum written and verified: {manifest_path}")
        return
    print("Protected product checksum verified")


if __name__ == "__main__":
    main()
