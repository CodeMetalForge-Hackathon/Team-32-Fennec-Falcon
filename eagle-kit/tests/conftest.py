import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eagle.config import load_config, load_keys  # noqa: E402


@pytest.fixture(scope="session")
def cfg():
    return load_config()


@pytest.fixture(scope="session")
def keys(cfg):
    return load_keys(cfg)


@pytest.fixture(scope="session")
def root():
    return ROOT
