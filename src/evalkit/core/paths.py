from __future__ import annotations


class UnsafePathError(ValueError):
    pass


def is_safe_path(path: str) -> bool:
    return all(segment not in ("", ".", "..") for segment in path.lstrip("/").split("/"))


def require_safe_path(path: str) -> str:
    if not is_safe_path(path):
        raise UnsafePathError(f"unsafe path {path!r}")
    return path.lstrip("/")


def is_safe_run_id(run_id: str) -> bool:
    return "/" not in run_id and is_safe_path(run_id)


def require_safe_run_id(run_id: str) -> str:
    if not is_safe_run_id(run_id):
        raise UnsafePathError(f"unsafe run id {run_id!r}")
    return run_id
