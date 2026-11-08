"""The "Eagle Sim" menu in the Unreal Editor's main menu bar."""
import unreal

MENU = "LevelEditor.MainMenu.EagleSim"

SECTIONS = [
    ("Run", "Run", [
        ("Build", "1. Build Taiwan scene", "build_scene()",
         "Open or create the level, stream real Taiwan terrain from Cesium ion, then plant the forest "
         "and place the person, the muntjac, the eagle and the cameras."),
        ("Start", "2. Start mission", "start()",
         "Fly the mission: ingress, thermal search, confirm orbit, perch, observe with bird sensors, hold. "
         "Sends CoT markers to ATAK."),
        ("Pause", "Pause / resume", "pause()", "Freeze or resume the mission."),
        ("Restart", "Restart mission", "restart()", "Start again from ingress."),
        ("Stop", "Stop", "stop()", "Stop the mission and free the camera."),
    ]),
    ("Views", "Views", [
        ("Auto", "Auto director (cuts by phase)", "auto_director()",
         "Chase on the way in, thermal while searching, perch camera for the contact, overview at the end."),
        ("Thermal", "Eagle thermal camera", "view('thermal')", "White-hot LWIR view from the bird."),
        ("Chase", "Chase camera", "view('chase')", "Follows the bird."),
        ("Perch", "Perch camera", "view('scene')", "Watches the perch tree and the person."),
        ("Overview", "Overview", "view('overview')", "High view over the three search cells."),
        ("Free", "Free camera", "view('free')", "Hand the viewport back to you."),
        ("GameView", "Game view on/off", "toggle_game_view()", "Hide editor icons and camera meshes."),
    ]),
    ("Options", "Options", [
        ("Speed1", "Speed x1", "set_speed(1)", "Real time."),
        ("Speed3", "Speed x3", "set_speed(3)", "Default: the whole mission in about 2 minutes."),
        ("Speed6", "Speed x6", "set_speed(6)", "Fast rehearsal."),
        ("TestCot", "Send test CoT to ATAK", "test_cot()", "One EAGLE-TEST marker to check the network."),
        ("Props", "Re-place forest and props", "rebuild_props()",
         "Respawn everything except the terrain (after editing config.py)."),
        ("Materials", "Rebuild materials", "rebuild_materials()", "Regenerate the thermal and surface materials."),
        ("Reload", "Reload Python scripts", "reload_scripts()", "Pick up edits without restarting the editor."),
        ("Readme", "Open README", "open_readme()", "Setup, run of show and troubleshooting."),
    ]),
]


def register():
    menus = unreal.ToolMenus.get()
    sub = menus.find_menu(MENU)
    if sub is None:
        main = menus.find_menu("LevelEditor.MainMenu")
        if main is None:
            unreal.log_warning("[EagleSim] main menu not found; menu not added")
            return False
        sub = main.add_sub_menu("EagleSim", "EagleSim", "EagleSim", "Eagle Sim",
                                "Hawk-eagle thermal search demo (Code Metal Team 32)")
    for section, label, items in SECTIONS:
        sub.add_section(section, label)
        for name, text, call, tip in items:
            entry = unreal.ToolMenuEntry(name="EagleSim." + name, type=unreal.MultiBlockType.MENU_ENTRY)
            entry.set_label(text)
            entry.set_tool_tip(tip)
            entry.set_string_command(unreal.ToolMenuStringCommandType.PYTHON, "",
                                     "import eagle_sim.ue.app as A; A.%s" % call)
            sub.add_menu_entry(section, entry)
    menus.refresh_all_widgets()
    return True
