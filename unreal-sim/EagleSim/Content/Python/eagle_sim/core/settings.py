"""Load config.py as a fresh namespace, so edits apply without restarting Unreal."""
import importlib
import types

from . import config


def load(**overrides):
    """Re-read config.py and return its settings, with optional overrides for this run."""
    importlib.reload(config)
    cfg = types.SimpleNamespace(**{k: getattr(config, k) for k in dir(config) if k.isupper()})
    for key, value in overrides.items():
        if not hasattr(cfg, key):
            raise KeyError("unknown setting %s" % key)
        setattr(cfg, key, value)
    return cfg
