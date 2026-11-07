"""Unreal Editor side of the Eagle demo. Imports `unreal`, so it only runs inside the editor.

menu.py     the "Eagle Sim" menu (registered by Content/Python/init_unreal.py)
app.py      what the menu entries do: build, start, pause, stop, views, test CoT
scene.py    builds the Taiwan level: Cesium terrain, sun, forest, person, animal, eagle, cameras
engine.py   connects the engine-free mission (eagle_sim.core) to the Unreal scene
materials.py   the thermal post-process material and the simple surface materials
util.py     small helpers over the Unreal Python API
"""
