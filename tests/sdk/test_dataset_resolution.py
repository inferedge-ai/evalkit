import pytest

from evalkit.client.sync import resolve_dataset
from evalkit.core.workspace import Reference


def test_dataset_and_workspace_combinations():
    assert resolve_dataset("finance@canonical", None, "me") == Reference("finance", "canonical")
    assert resolve_dataset("finance", "canonical", "me") == Reference("finance", "canonical")
    assert resolve_dataset("finance", None, "me") == Reference("finance", "me")
    with pytest.raises(ValueError, match="already names a workspace"):
        resolve_dataset("finance@canonical", "me", "me")
