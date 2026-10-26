from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

LEFT_OUT_NAMES = {".env"}
LEFT_OUT_SUFFIXES = {".whl", ".pyc"}


def bundled(path: Path) -> bool:
    return (
        path.is_file()
        and path.name not in LEFT_OUT_NAMES
        and path.suffix not in LEFT_OUT_SUFFIXES
        and "__pycache__" not in path.parts
    )


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict) -> None:
        skills = Path(self.root) / "skills"
        for path in sorted(skills.rglob("*")):
            if bundled(path):
                target = f"evalkit/bundled/skills/{path.relative_to(skills).as_posix()}"
                build_data["force_include"][str(path)] = target
