import json
from pathlib import Path

import pytest
import yaml

FIX = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).parent.parent


@pytest.fixture
def fixture():
    def load(name):
        text = (FIX / name).read_text()
        return json.loads(text) if name.endswith(".json") else text
    return load


@pytest.fixture
def profile():
    return yaml.safe_load((ROOT / "profile.yaml").read_text())
