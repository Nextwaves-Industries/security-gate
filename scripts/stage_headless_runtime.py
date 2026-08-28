"""Create the explicit, source-minimized tree copied into the Linux image."""

from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import re
import shutil
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MODULE_RE = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*$")
LINUX_EXTENSION_RE_TEMPLATE = (
    r"^{stem}(?:\.cpython-311-[A-Za-z0-9_.-]*linux[A-Za-z0-9_.-]*|\.abi3)?\.so$"
)


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read headless manifest {path}: {exc}") from exc
    if manifest.get("schema_version") != 1:
        raise ValueError("Headless manifest schema_version must be 1")
    if manifest.get("python_abi") != "cp311":
        raise ValueError("Headless manifest python_abi must be cp311")
    if manifest.get("platform") != "linux-x86_64":
        raise ValueError("Headless manifest platform must be linux-x86_64")

    modules: list[str] = []
    for key in ("modules", "required_native_modules"):
        values = manifest.get(key)
        if not isinstance(values, list) or not values:
            raise ValueError(f"Headless manifest {key} must be a non-empty list")
        for value in values:
            if not isinstance(value, str) or not MODULE_RE.fullmatch(value):
                raise ValueError(f"Invalid Python module in {key}: {value!r}")
            modules.append(value)
        if len(values) != len(set(values)):
            raise ValueError(f"Headless manifest {key} contains duplicates")
    if len(modules) != len(set(modules)):
        raise ValueError("Protected and required-native module lists overlap")

    runtime_files = manifest.get("runtime_python_files")
    if not isinstance(runtime_files, list) or not runtime_files:
        raise ValueError(
            "Headless manifest runtime_python_files must be a non-empty list"
        )
    normalized_files: list[str] = []
    for value in runtime_files:
        if not isinstance(value, str):
            raise ValueError(f"Invalid runtime Python path: {value!r}")
        pure = PurePosixPath(value)
        if (
            pure.is_absolute()
            or ".." in pure.parts
            or pure.suffix != ".py"
            or not pure.parts
        ):
            raise ValueError(f"Unsafe runtime Python path: {value!r}")
        normalized_files.append(pure.as_posix())
    if len(normalized_files) != len(set(normalized_files)):
        raise ValueError("Headless manifest runtime_python_files contains duplicates")
    manifest["runtime_python_files"] = normalized_files
    return manifest


def module_relative_base(module_name: str) -> Path:
    return Path(*module_name.split("."))


def find_native_extension(native_root: Path, module_name: str) -> Path:
    base = native_root / module_relative_base(module_name)
    pattern = re.compile(
        LINUX_EXTENSION_RE_TEMPLATE.format(stem=re.escape(base.name)),
        re.IGNORECASE,
    )
    candidates = sorted(
        path
        for path in base.parent.glob(f"{base.name}*.so")
        if path.is_file() and pattern.fullmatch(path.name)
    )
    if len(candidates) != 1:
        names = ", ".join(path.name for path in candidates) or "none"
        raise ValueError(
            f"Expected exactly one CPython 3.11 Linux artifact for "
            f"{module_name}; found {names}"
        )
    candidate = candidates[0]
    if candidate.is_symlink():
        raise ValueError(f"Native artifact must not be a symlink: {candidate}")
    return candidate


def stage_runtime(
    native_root: Path,
    output: Path,
    manifest_path: Path,
    *,
    source_root: Path = ROOT,
) -> Path:
    native_root = native_root.resolve()
    source_root = source_root.resolve()
    if output.is_symlink():
        raise ValueError(f"Runtime output must not be a symlink: {output}")
    output = output.resolve()
    if (
        output == Path(output.anchor)
        or output in {source_root, native_root}
        or output in source_root.parents
        or output in native_root.parents
    ):
        raise ValueError(f"Unsafe runtime output directory: {output}")
    manifest = load_manifest(manifest_path.resolve())

    # Staging is reproducible even when a local build directory is reused. A
    # stale file must never survive because it happens not to match a blacklist.
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    for relative_name in manifest["runtime_python_files"]:
        relative = Path(*PurePosixPath(relative_name).parts)
        source = source_root / relative
        if not source.is_file() or source.is_symlink():
            raise ValueError(f"Runtime Python source is missing or unsafe: {source}")
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    native_modules = [
        *manifest["modules"],
        *manifest["required_native_modules"],
    ]
    for module_name in native_modules:
        source = find_native_extension(native_root, module_name)
        module_base = module_relative_base(module_name)
        destination = output / module_base.parent / source.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=ROOT / "release" / "protected_modules_headless.json",
    )
    args = parser.parse_args()
    try:
        output = stage_runtime(args.native, args.output, args.manifest)
    except ValueError as exc:
        raise SystemExit(f"Headless runtime staging failed: {exc}") from exc
    print(f"Headless runtime staged: {output}")


if __name__ == "__main__":
    main()
