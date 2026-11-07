"""Runs when the Unreal Editor opens this project: adds the "Eagle Sim" menu to the main menu bar."""
import unreal

try:
    from eagle_sim.ue import menu as _eagle_menu

    if _eagle_menu.register():
        unreal.log("[EagleSim] menu ready: Eagle Sim > 1. Build Taiwan scene")
except Exception as _exc:  # keep the editor usable whatever happens here
    unreal.log_error("[EagleSim] could not add the menu: %s" % _exc)
