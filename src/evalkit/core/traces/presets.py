from importlib.resources import files

_PRESETS = files("evalkit.core.traces").joinpath("mapping_presets")


def list_preset_names() -> list[str]:
    return sorted(
        entry.name.removesuffix(".json")
        for entry in _PRESETS.iterdir()
        if entry.name.endswith(".json")
    )


def read_preset(name: str) -> str:
    if name not in list_preset_names():
        raise KeyError(name)
    return _PRESETS.joinpath(f"{name}.json").read_text()
