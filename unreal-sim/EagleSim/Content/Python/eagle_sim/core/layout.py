"""Scenario layout shared by the Unreal scene and the tests. Pure Python, metres.

Heights here are relative to the local ground. The engine supplies real ground heights.
"""
import math
import random

from . import config as C


class Tree:
    """A stylized broadleaf tree: one trunk and a few canopy spheres (blobs)."""

    def __init__(self, x, y, height, trunk_r, blobs, cover=False):
        self.x, self.y = x, y
        self.height = height
        self.trunk_r = trunk_r
        self.blobs = blobs          # [(dx, dy, dz_above_ground, radius), ...]
        self.cover = cover

    def top(self):
        """Highest canopy point, relative to ground: (dx, dy, dz)."""
        dx, dy, dz, r = max(self.blobs, key=lambda b: b[2] + b[3])
        return dx, dy, dz + r


def _tree(rng, x, y, cover=False):
    h = rng.uniform(13.0, 22.0)
    blobs = []
    for _ in range(4 if cover else 3):
        r = rng.uniform(2.8, 5.0)
        dx, dy = rng.uniform(-2.5, 2.5), rng.uniform(-2.5, 2.5)
        dz = h - 0.6 * r + rng.uniform(-1.5, 1.0)
        blobs.append((dx, dy, dz, r))
    return Tree(x, y, h, rng.uniform(0.2, 0.4), blobs, cover)


def make_trees(cfg=C):
    rng = random.Random(cfg.SEED)
    ox, oy = cfg.OPERATOR_EN
    ax, ay = cfg.ANIMAL_EN
    trees = []
    for k in range(cfg.COVER_TREES):                     # canopy that overhangs the person
        a = 2 * math.pi * k / max(cfg.COVER_TREES, 1) + rng.uniform(-0.4, 0.4)
        d = rng.uniform(4.0, 7.0)
        trees.append(_tree(rng, ox + d * math.cos(a), oy + d * math.sin(a), cover=True))
    cx = sum(c[0] for c in cfg.SEARCH_CELLS_EN) / len(cfg.SEARCH_CELLS_EN)
    cy = sum(c[1] for c in cfg.SEARCH_CELLS_EN) / len(cfg.SEARCH_CELLS_EN)
    while len(trees) < cfg.TREES + cfg.COVER_TREES:
        r = cfg.TREE_RADIUS_M * math.sqrt(rng.random())
        a = rng.uniform(0, 2 * math.pi)
        x, y = cx + r * math.cos(a), cy + r * math.sin(a)
        if math.hypot(x - ox, y - oy) < 9.0 or math.hypot(x - ax, y - ay) < 6.0:
            continue
        trees.append(_tree(rng, x, y))
    return trees


def body_points(cx, cy, ground_z, yaw_deg, offsets=(0.75, 0.0, -0.6), height=0.2):
    """Ray targets along a lying body (head, torso, legs)."""
    yaw = math.radians(yaw_deg)
    return [(cx + s * math.cos(yaw), cy + s * math.sin(yaw), ground_z + height) for s in offsets]


def operator_points(cfg, ground_z):
    ox, oy = cfg.OPERATOR_EN
    return body_points(ox, oy, ground_z, cfg.OPERATOR_YAW_DEG)


def animal_points(cfg, ground_z):
    ax, ay = cfg.ANIMAL_EN
    return body_points(ax, ay, ground_z, 0.0, offsets=(0.4, 0.0, -0.4), height=0.6)


def perches(trees, ground):
    """Tops of the cover trees as (x, y, z). ground(x, y) gives ground height."""
    out = []
    for t in trees:
        if t.cover:
            dx, dy, dz = t.top()
            out.append((t.x + dx, t.y + dy, ground(t.x, t.y) + dz))
    return out
