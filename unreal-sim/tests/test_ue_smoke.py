"""Runs the Unreal side (eagle_sim.ue) end to end against tests/fake_unreal.py.

This proves the Unreal glue code runs and drives the mission: build the scene, stream terrain,
place the forest, start, fly to HOLD, cut cameras, send CoT. It cannot prove the real editor
renders it; that is checked by hand (see the README run-through).
"""
import socket
import sys
import xml.etree.ElementTree as ET

import pytest

import fake_unreal


@pytest.fixture()
def ue(monkeypatch):
    fake_unreal.reset()
    monkeypatch.setitem(sys.modules, "unreal", fake_unreal)
    for name in [n for n in sys.modules if n.startswith("eagle_sim.ue")]:
        monkeypatch.delitem(sys.modules, name)
    from eagle_sim.ue import app, menu
    yield app, menu
    app.stop(quiet=True)
    for name in [n for n in sys.modules if n.startswith("eagle_sim.ue")]:
        sys.modules.pop(name, None)


def run_until(pred, frames, dt=0.1):
    for _ in range(frames):
        fake_unreal.tick(dt)
        if pred():
            return True
    return False


def test_menu_registers_every_entry(ue):
    app, menu = ue
    assert menu.register()
    sub = fake_unreal.ToolMenus.get().find_menu(menu.MENU)
    entries = {n: e for sec in sub.sections.values() for n, e in sec.items()}
    assert len(entries) == sum(len(items) for _, _, items in menu.SECTIONS)
    for e in entries.values():
        kind, code = e.command
        assert kind == fake_unreal.ToolMenuStringCommandType.PYTHON
        call = code.split("A.", 1)[1].split("(", 1)[0]
        assert callable(getattr(app, call)), call


def test_build_start_and_fly_to_hold(ue):
    app, menu = ue
    from eagle_sim.core import layout, settings
    from eagle_sim.core.mission import HOLD

    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("127.0.0.1", 0))
    rx.settimeout(0.05)
    port = rx.getsockname()[1]
    app.S.overrides = {"COT_TARGETS": [("127.0.0.1", port)], "TIME_SCALE": 20.0}

    # ---- build: terrain streams in, then the props are placed and the level saved
    app.build_scene()
    assert app.S.builder is not None, fake_unreal.DIALOGS
    assert run_until(lambda: app.S.builder is None, 600), "build never finished"
    assert not [d for d in fake_unreal.DIALOGS], fake_unreal.DIALOGS
    world = fake_unreal._ED.world
    assert world.path == "/Game/EagleSim/Maps/Taiwan"
    assert fake_unreal._ED.saved >= 1
    assert abs(world.georef.origin[2] - fake_unreal.ground_hae(0, 0)) < 1.0   # origin moved to the ground

    cfg = settings.load()
    trees = layout.make_trees(cfg)
    roles = {}
    for a in world.actors:
        for t in a.get_editor_property("tags"):
            if str(t).startswith("ES:"):
                roles[str(t)[3:]] = roles.get(str(t)[3:], 0) + 1
    assert roles["tree"] == sum(1 + len(t.blobs) for t in trees)
    assert roles["operator"] == 3 and roles["animal"] == 6 and roles["eagle_part"] == 6
    for key in ("thermal", "chase", "scene", "overview"):
        assert roles["cam_" + key] == 1
    assert roles["cell_label"] == 3
    thermal = [a for a in world.actors if a.label == "EAGLE-1 thermal camera"][0]
    blend = thermal.camera_component.get_editor_property("post_process_settings").weighted_blendables
    assert blend.array[0].object.get_name() == "M_EagleThermal"
    assert thermal.camera_component.fov == cfg.CAM_HFOV_DEG
    stencils = {a.label: a.static_mesh_component.stencil for a in world.actors
                if isinstance(a, fake_unreal.StaticMeshActor) and a.label.startswith(("IP-1", "Muntjac"))}
    assert set(v for k, v in stencils.items() if k.startswith("IP-1")) == {1}
    assert set(v for k, v in stencils.items() if k.startswith("Muntjac")) == {2}

    # ---- start and fly
    app.start()
    assert app.S.mission is not None, fake_unreal.DIALOGS
    views = set()
    eagle = [a for a in world.actors if a.label == "EAGLE-1"][0]
    start_loc = (eagle.loc.x, eagle.loc.y, eagle.loc.z)

    def held():
        views.add(app.S.view)
        m = app.S.mission
        return m is not None and m.phase == HOLD and m.t - m._phase_t0 > 25.0

    assert run_until(held, 4000), (app.S.mission.phase, app.S.mission.events[-4:])
    m = app.S.mission
    assert (eagle.loc.x, eagle.loc.y, eagle.loc.z) != start_loc
    assert {"chase", "thermal", "scene", "overview"} <= views, views
    assert fake_unreal._ED.pilot is not None and fake_unreal._ED.pilot.label == "Overview camera"
    texts = {a.label: a.text_render.text for a in world.actors if isinstance(a, fake_unreal.TextRenderActor)}
    assert texts["Label operator"] == "IP-1 PERSON DETECTED · ID UNVERIFIED"
    assert texts["OSD thermal 0"].startswith("EAGLE-1  HOLD")
    assert "One-way bird" in texts["OSD overview 1"]
    assert any("CONFIRMED" in e[2] for e in m.events)
    # CoT carries bird observations, with no automatic friendly identity classification.
    types = []
    while True:
        try:
            data, _ = rx.recvfrom(65535)
        except socket.timeout:
            break
        root = ET.fromstring(data)
        types.append(root.get("type"))
        lat = float(root.find("point").get("lat"))
        assert abs(lat - cfg.SITE_LAT) < 0.05
    rx.close()
    assert {"a-f-A-M-F-Q", "a-u-G"} <= set(types), set(types)

    assert "a-f-G-U-C" not in types

    # ---- stop frees the viewport and the tick
    app.stop()
    assert fake_unreal._ED.pilot is None
    assert not fake_unreal._CALLBACKS


def test_start_without_a_scene_explains_what_to_do(ue):
    app, menu = ue
    app.start()
    assert app.S.mission is None
    assert fake_unreal.DIALOGS and "Build" in fake_unreal.DIALOGS[-1][1]


def test_test_cot_reports_targets(ue):
    app, menu = ue
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("127.0.0.1", 0))
    rx.settimeout(1.0)
    app.S.overrides = {"COT_TARGETS": [("127.0.0.1", rx.getsockname()[1])]}
    app.test_cot()
    data, _ = rx.recvfrom(65535)
    rx.close()
    assert ET.fromstring(data).get("uid") == "EAGLE-TEST"
    assert "1 of 1" in fake_unreal.DIALOGS[-1][1]
