#!/usr/bin/env python3
"""Read-only inventory and safety checks for local files and directory trees.

The command emits JSON on stdout. It never extracts archives, follows links,
or writes to the inspected paths.
"""

from __future__ import annotations

import argparse
import importlib.util
import importlib.metadata
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import sys
import zipfile


FILE_ATTRIBUTE_REPARSE_POINT = 0x400
ARCHIVE_SUFFIXES = {".zip", ".jar", ".whl", ".egg", ".docx", ".xlsx", ".pptx"}
NESTED_ARCHIVE_SUFFIXES = {".zip", ".jar", ".whl", ".egg"}


def parse_size(value: str) -> int:
    text = value.strip().lower()
    units = {"b": 1, "k": 1024, "kb": 1024, "kib": 1024,
             "m": 1024**2, "mb": 1024**2, "mib": 1024**2,
             "g": 1024**3, "gb": 1024**3, "gib": 1024**3}
    for suffix in sorted(units, key=len, reverse=True):
        if text.endswith(suffix):
            return int(float(text[:-len(suffix)]) * units[suffix])
    return int(text)


def is_reparse_point(path: Path, follow_symlinks: bool = False) -> bool:
    try:
        info = path.stat(follow_symlinks=follow_symlinks)
    except OSError:
        return False
    attrs = getattr(info, "st_file_attributes", 0)
    return bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT)


def link_reason(path: Path) -> str | None:
    try:
        if path.is_symlink():
            return "symlink"
    except OSError:
        return "unreadable-link"
    if is_reparse_point(path):
        return "reparse-point-or-junction"
    return None


def suffix(path_name: str) -> str:
    return Path(path_name).suffix.lower()


def archive_report(path: Path, limits: dict[str, int]) -> dict:
    report = {
        "path": str(path),
        "entries": [],
        "issues": [],
        "truncated": False,
        "entry_count": 0,
        "uncompressed_bytes": 0,
    }
    try:
        # Reject oversized archives before loading their central directory.
        if path.stat().st_size > limits["max_file_bytes"]:
            report["issues"].append("archive-file-size-limit")
            return report
        with zipfile.ZipFile(path) as archive:
            seen: set[str] = set()
            seen_casefold: set[str] = set()
            for index, item in enumerate(archive.infolist()):
                report["entry_count"] += 1
                if index >= limits["max_archive_entries"]:
                    report["truncated"] = True
                    report["issues"].append("archive-entry-count-limit")
                    break
                name = item.filename
                # ZIP names conventionally use '/', but reject Windows-style
                # traversal separators as well.
                normalized_name = name.replace("\\", "/")
                normalized = PurePosixPath(normalized_name)
                path_parts = normalized.parts
                drive_absolute = len(normalized_name) >= 2 and normalized_name[1] == ":"
                traversal = (normalized.is_absolute() or drive_absolute
                             or normalized_name.startswith("/")
                             or ".." in path_parts)
                duplicate = name in seen or name.casefold() in seen_casefold
                seen.add(name)
                seen_casefold.add(name.casefold())
                mode = (item.external_attr >> 16) & 0xFFFF
                is_link = stat.S_ISLNK(mode)
                nested = suffix(name) in NESTED_ARCHIVE_SUFFIXES
                compression_ratio = None
                if item.compress_size:
                    compression_ratio = round(item.file_size / item.compress_size, 2)
                report["uncompressed_bytes"] += item.file_size
                if report["uncompressed_bytes"] > limits["max_archive_bytes"]:
                    report["truncated"] = True
                    report["issues"].append("archive-uncompressed-size-limit")
                if item.file_size > limits["max_archive_entry_bytes"]:
                    report["issues"].append("archive-entry-size-limit")
                if compression_ratio is not None and compression_ratio > 1000:
                    report["issues"].append("extreme-compression-ratio")
                if traversal:
                    report["issues"].append("archive-path-traversal")
                if is_link:
                    report["issues"].append("archive-symlink-entry")
                if duplicate:
                    report["issues"].append("duplicate-archive-entry")
                if nested:
                    report["issues"].append("nested-archive-not-recursed")
                report["entries"].append({
                    "name": name,
                    "directory": item.is_dir(),
                    "size": item.file_size,
                    "compressed_size": item.compress_size,
                    "nested_archive": nested,
                    "path_traversal": traversal,
                    "symlink": is_link,
                    "duplicate": duplicate,
                    "compression_ratio": compression_ratio,
                })
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        report["issues"].append(f"archive-read-failed:{type(exc).__name__}")
    return report


def inventory(paths: list[str], limits: dict[str, int]) -> dict:
    result = {
        "limits": limits,
        "roots": [],
        "files": [],
        "archives": [],
        "skipped": [],
        "issues": [],
        "summary": {"files": 0, "bytes": 0, "truncated": False},
    }
    identities: set[tuple[int, int]] = set()

    def add_skip(path: Path, reason: str) -> None:
        result["skipped"].append({"path": str(path), "reason": reason})

    def add_file(path: Path, relative: str, depth: int) -> bool:
        if result["summary"]["files"] >= limits["max_files"]:
            result["summary"]["truncated"] = True
            result["issues"].append("file-count-limit")
            return False
        try:
            info = path.stat(follow_symlinks=False)
        except OSError as exc:
            add_skip(path, f"stat-failed:{type(exc).__name__}")
            return True
        if not stat.S_ISREG(info.st_mode):
            add_skip(path, "not-regular-file")
            return True
        size = info.st_size
        if size > limits["max_file_bytes"]:
            add_skip(path, "per-file-size-limit")
            return True
        if result["summary"]["bytes"] + size > limits["max_total_bytes"]:
            result["summary"]["truncated"] = True
            result["issues"].append("total-byte-limit")
            return False
        identity = (getattr(info, "st_dev", 0), getattr(info, "st_ino", 0))
        duplicate_identity = identity != (0, 0) and identity in identities
        if identity != (0, 0):
            identities.add(identity)
        entry = {"path": str(path), "relative_path": relative, "depth": depth,
                 "size": size, "extension": suffix(path.name),
                 "duplicate_identity": duplicate_identity}
        result["files"].append(entry)
        result["summary"]["files"] += 1
        result["summary"]["bytes"] += size
        if suffix(path.name) in ARCHIVE_SUFFIXES:
            result["archives"].append(archive_report(path, limits))
        return True

    def walk(root: Path) -> None:
        root = root.absolute()
        result["roots"].append(str(root))
        reason = link_reason(root)
        if reason:
            add_skip(root, reason)
            return
        if root.is_file():
            add_file(root, root.name, 0)
            return
        if not root.is_dir():
            add_skip(root, "missing-or-not-directory")
            return
        stack: list[tuple[Path, int, str]] = [(root, 0, "")]
        while stack:
            current, depth, relative = stack.pop()
            if depth > limits["max_depth"]:
                add_skip(current, "directory-depth-limit")
                result["summary"]["truncated"] = True
                continue
            try:
                children = sorted(os.scandir(current), key=lambda item: item.name.casefold())
            except OSError as exc:
                add_skip(current, f"scan-failed:{type(exc).__name__}")
                continue
            for child in children:
                child_path = Path(child.path)
                child_relative = str(Path(relative) / child.name) if relative else child.name
                reason = link_reason(child_path)
                if reason:
                    add_skip(child_path, reason)
                    continue
                try:
                    is_dir = child.is_dir(follow_symlinks=False)
                except OSError:
                    add_skip(child_path, "type-check-failed")
                    continue
                if is_dir:
                    if depth >= limits["max_depth"]:
                        add_skip(child_path, "directory-depth-limit")
                        result["summary"]["truncated"] = True
                    else:
                        stack.append((child_path, depth + 1, child_relative))
                elif not add_file(child_path, child_relative, depth + 1):
                    return

    for item in paths:
        walk(Path(item))
        if result["summary"]["truncated"] and "file-count-limit" in result["issues"]:
            break
    return result


def runtime_info() -> dict:
    optional = {
        "magika": "magika",
        "speech_recognition": "speech_recognition",
        "youtube_transcript_api": "youtube_transcript_api",
        "azure_content_understanding": "azure.ai.contentunderstanding",
        "azure_document_intelligence": "azure.ai.documentintelligence",
    }
    backends = {}
    for name, module in optional.items():
        try:
            backends[name] = importlib.util.find_spec(module) is not None
        except (ImportError, ModuleNotFoundError, ValueError):
            backends[name] = False
    try:
        version = importlib.metadata.version("markitdown")
    except importlib.metadata.PackageNotFoundError:
        version = None
    executable_dir = Path(sys.executable).parent
    cli_candidates = [
        shutil.which("markitdown"),
        str(executable_dir / "Scripts" / "markitdown.exe"),
        str(executable_dir / "Scripts" / "markitdown"),
        str(executable_dir / "bin" / "markitdown"),
    ]
    cli = next((candidate for candidate in cli_candidates
                if candidate and Path(candidate).exists()), None)
    return {"python": sys.executable, "markitdown_version": version,
            "markitdown_cli": cli if Path(cli).exists() else None,
            "optional_backends": backends}


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description="Read-only corpus inventory for MarkItDown workflows")
    command.add_argument("paths", nargs="*", help="Files or directories to inspect")
    command.add_argument("--runtime-info", action="store_true", help="Print runtime and backend availability")
    command.add_argument("--max-depth", type=int, default=8)
    command.add_argument("--max-files", type=int, default=2000)
    command.add_argument("--max-total-bytes", type=parse_size, default=parse_size("1GiB"))
    command.add_argument("--max-file-bytes", type=parse_size, default=parse_size("256MiB"))
    command.add_argument("--max-archive-entries", type=int, default=1000)
    command.add_argument("--max-archive-bytes", type=parse_size, default=parse_size("512MiB"))
    command.add_argument("--max-archive-entry-bytes", type=parse_size, default=parse_size("128MiB"))
    return command


def main() -> int:
    args = parser().parse_args()
    if args.runtime_info:
        print(json.dumps(runtime_info(), ensure_ascii=True, indent=2))
        return 0
    if not args.paths:
        parser().error("provide at least one file or directory, or use --runtime-info")
    limits = {
        "max_depth": args.max_depth,
        "max_files": args.max_files,
        "max_total_bytes": args.max_total_bytes,
        "max_file_bytes": args.max_file_bytes,
        "max_archive_entries": args.max_archive_entries,
        "max_archive_bytes": args.max_archive_bytes,
        "max_archive_entry_bytes": args.max_archive_entry_bytes,
    }
    print(json.dumps(inventory(args.paths, limits), ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
