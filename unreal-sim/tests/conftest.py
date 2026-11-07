import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "EagleSim", "Content", "Python"))

from eagle_sim.core import settings  # noqa: E402
from eagle_sim.core.headless import HeadlessEngine as FakeEngine  # noqa: E402,F401
from eagle_sim.core.headless import ground, scenario_for  # noqa: E402,F401


def make_cfg(**overrides):
    return settings.load(**overrides)
