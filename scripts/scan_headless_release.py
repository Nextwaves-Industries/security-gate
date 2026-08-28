"""Fail a container release stage that exposes source, secrets or desktop code."""

from __future__ import annotations

import argparse
import ast
from pathlib import Path, PurePosixPath
import struct

try:
    from stage_headless_runtime import (
        find_native_extension,
        load_manifest,
        module_relative_base,
    )
except ImportError:  # Imported as ``scripts.scan_headless_release`` in tests.
    from .stage_headless_runtime import (
        find_native_extension,
        load_manifest,
        module_relative_base,
    )


FORBIDDEN_PARTS = {
    "_internal",
    "controllers",
    "pyqt6",
    "qfluentwidgets",
    "sdk_app",
    "views",
}
FORBIDDEN_IMPORT_ROOTS = {
    "controllers",
    "pyqt6",
    "qfluentwidgets",
    "sdk_app",
    "views",
}
FORBIDDEN_FILENAMES = {
    ".env",
    "api_token",
    "calibration_root_key",
    "config.json",
    "customer_config.json",
    "mqtt_credentials.json",
    "mqtt_password",
    "tls_key",
}
FORBIDDEN_SECRET_SUFFIXES = {
    ".cer",
    ".crt",
    ".db",
    ".jks",
    ".key",
    ".keystore",
    ".p12",
    ".pem",
    ".pfx",
    ".sqlite",
    ".sqlite3",
}
FORBIDDEN_MODEL_SUFFIXES = {
    ".bin",
    ".joblib",
    ".npy",
    ".npz",
    ".onnx",
    ".pickle",
    ".pkl",
    ".pt",
    ".pth",
}
PRIVATE_KEY_MARKERS = (
    "-----BEGIN PRIVATE KEY-----",
    "-----BEGIN RSA PRIVATE KEY-----",
    "-----BEGIN EC PRIVATE KEY-----",
    "-----BEGIN OPENSSH PRIVATE KEY-----",
)


def _validate_elf_x86_64(path: Path) -> str | None:
    try:
        with path.open("rb") as stream:
            header = stream.read(20)
    except OSError as exc:
        return f"cannot read native extension: {path.name}: {exc}"
    if len(header) < 20 or header[:4] != b"\x7fELF":
        return f"native extension is not ELF: {path.name}"
    if header[4] != 2 or header[5] != 1:
        return f"native extension is not little-endian ELF64: {path.name}"
    byte_order = "<" if header[5] == 1 else ">"
    machine = struct.unpack(f"{byte_order}H", header[18:20])[0]
    if machine != 62:
        return f"native extension is not Linux x86_64: {path.name}"
    return None


def _forbidden_imports(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(path))
    except (OSError, UnicodeError, SyntaxError) as exc:
        return [f"unreadable runtime Python source: {path.name}: {exc}"]
    failures: list[str] = []
    for marker in PRIVATE_KEY_MARKERS:
        if marker in text:
            failures.append(f"embedded private key: {path.name}")
            break
    for node in ast.walk(tree):
        roots: list[str] = []
        if isinstance(node, ast.Import):
            roots = [alias.name.partition(".")[0].casefold() for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots = [node.module.partition(".")[0].casefold()]
        for root in roots:
            if root in FORBIDDEN_IMPORT_ROOTS:
                failures.append(f"desktop import {root!r}: {path.name}")
    return failures


def scan_release(root: Path, manifest_path: Path) -> list[str]:
    root = root.resolve()
    manifest = load_manifest(manifest_path.resolve())
    failures: list[str] = []
    if not root.is_dir():
        return [f"runtime root is not a directory: {root}"]

    allowed_python = set(manifest["runtime_python_files"])
    expected_native: set[str] = set()
    native_modules = [
        *manifest["modules"],
        *manifest["required_native_modules"],
    ]
    for module_name in native_modules:
        try:
            extension = find_native_extension(root, module_name)
        except ValueError as exc:
            failures.append(str(exc))
            continue
        expected_native.add(extension.relative_to(root).as_posix())

    for relative_name in sorted(allowed_python):
        path = root.joinpath(*PurePosixPath(relative_name).parts)
        if not path.is_file() or path.is_symlink():
            failures.append(f"missing runtime Python shell: {relative_name}")

    for path in root.rglob("*"):
        relative = path.relative_to(root)
        relative_name = relative.as_posix()
        if path.is_symlink():
            failures.append(f"symlink is forbidden: {relative_name}")
            continue
        if not path.is_file():
            continue
        parts = {part.casefold() for part in relative.parts}
        if parts & FORBIDDEN_PARTS:
            failures.append(f"desktop artifact: {relative_name}")
        suffix = path.suffix.casefold()
        name = path.name.casefold()
        if name in FORBIDDEN_FILENAMES or suffix in FORBIDDEN_SECRET_SUFFIXES:
            failures.append(f"secret/customer state artifact: {relative_name}")
        if suffix in FORBIDDEN_MODEL_SUFFIXES:
            failures.append(f"plaintext model artifact: {relative_name}")

        if suffix == ".py":
            if relative_name not in allowed_python:
                failures.append(f"unexpected plaintext source: {relative_name}")
            failures.extend(
                f"{failure} ({relative_name})" for failure in _forbidden_imports(path)
            )
        elif suffix == ".so":
            if relative_name not in expected_native:
                failures.append(f"unexpected native extension: {relative_name}")
            elf_failure = _validate_elf_x86_64(path)
            if elf_failure:
                failures.append(f"{elf_failure} ({relative_name})")
        else:
            failures.append(f"unexpected runtime file: {relative_name}")

    for module_name in manifest["modules"]:
        source = root / module_relative_base(module_name).with_suffix(".py")
        if source.exists():
            failures.append(f"protected source: {source.relative_to(root)}")
    return failures


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    try:
        failures = scan_release(args.root, args.manifest)
    except ValueError as exc:
        raise SystemExit(f"Headless release scan failed:\ninvalid manifest: {exc}") from exc
    if failures:
        raise SystemExit(
            "Headless release scan failed:\n" + "\n".join(sorted(set(failures)))
        )
    print(f"Headless release scan passed: {args.root.resolve()}")


if __name__ == "__main__":
    main()
