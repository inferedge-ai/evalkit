import json
from importlib.resources import files

_NOTES = json.loads(files("evalkit.contracts").joinpath("notes.json").read_text())

ENDPOINT_NOTE: str = _NOTES["endpoint_note"]
PERSONAL_KEY_NOTE: str = _NOTES["personal_key_note"]
