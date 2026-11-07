"""Small helpers over the Unreal Python API. Editor world only (no Play-In-Editor needed)."""
import math

import unreal

TAG = "EagleSim"            # every actor this demo spawns carries this tag
ROLE_PREFIX = "ES:"         # plus one role tag, e.g. "ES:tree" or "ES:eagle"
FOLDER = "EagleSim"


# ------------------------------------------------------------ logging and dialogs
def log(msg):
    unreal.log("[EagleSim] " + msg)


def warn(msg):
    unreal.log_warning("[EagleSim] " + msg)


def error(msg):
    unreal.log_error("[EagleSim] " + msg)


def dialog(title, text):
    """A modal message box (falls back to the log)."""
    try:
        unreal.EditorDialog.show_message(title, text, unreal.AppMsgType.OK)
    except Exception:
        warn("%s: %s" % (title, text))


def flash(text, seconds=6.0, key="EagleSimFlash"):
    """Short on-screen note in the viewport, when the editor shows them, plus the log."""
    log(text)
    try:
        unreal.SystemLibrary.print_string(world(), text, True, False,
                                          unreal.LinearColor(1.0, 0.85, 0.1, 1.0), seconds, key)
    except Exception:
        pass


# ------------------------------------------------------------ subsystems
def actors_sub():
    return unreal.get_editor_subsystem(unreal.EditorActorSubsystem)


def level_sub():
    return unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)


def editor_sub():
    return unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)


def world():
    return editor_sub().get_editor_world()


def enum(enum_cls, *names):
    """First enum member that exists (names change between engine versions)."""
    for name in names:
        value = getattr(enum_cls, name, None)
        if value is not None:
            return value
    raise AttributeError("%s has none of %s" % (enum_cls, names))


# ------------------------------------------------------------ coordinates
# Mission frame: metres, x = east, y = north, z = up (from the Cesium georeference origin).
# Unreal with a Cesium georeference: centimetres, +X = east, +Y = south, +Z = up.
def to_ue(x, y, z):
    return unreal.Vector(x * 100.0, -y * 100.0, z * 100.0)


def from_ue(v):
    return (v.x / 100.0, -v.y / 100.0, v.z / 100.0)


def rot(yaw=0.0, pitch=0.0, roll=0.0):
    # unreal.Rotator's positional order is (roll, pitch, yaw), so always pass keywords
    return unreal.Rotator(roll=roll, pitch=pitch, yaw=yaw)


def yaw_from_heading(heading_rad):
    """Mission heading (radians, counter-clockwise from east) to Unreal yaw (degrees, clockwise)."""
    return -math.degrees(heading_rad)


def look_rot(src, dst, yaw_offset=0.0):
    """Rotator that points an actor's +X from mission point src to mission point dst."""
    dx, dy, dz = dst[0] - src[0], dst[1] - src[1], dst[2] - src[2]
    yaw = -math.degrees(math.atan2(dy, dx))
    pitch = math.degrees(math.atan2(dz, math.hypot(dx, dy)))
    if yaw_offset:
        return rot(yaw + yaw_offset, -pitch, 0.0)
    return rot(yaw, pitch, 0.0)


def color(r, g, b, a=255):
    # unreal.Color's positional order is (b, g, r, a), so always pass keywords
    return unreal.Color(r=r, g=g, b=b, a=a)


# ------------------------------------------------------------ line traces
# Trees use the built-in "UI" collision profile: they block the Visibility channel (so they hide
# the person from the thermal camera) but not the Camera channel (so terrain probes pass through).
# Cesium tiles block both.
def visibility_channel():
    return unreal.TraceTypeQuery.TRACE_TYPE_QUERY1


def terrain_channel():
    return unreal.TraceTypeQuery.TRACE_TYPE_QUERY2


def trace(world_obj, start, end, channel, ignore=()):
    """First blocking hit between two Unreal points as (location, actor), or None."""
    hit = unreal.SystemLibrary.line_trace_single(world_obj, start, end, channel, True, list(ignore),
                                                 unreal.DrawDebugTrace.NONE, True)
    if hit is None:
        return None
    return parse_hit(hit)


def parse_hit(hit):
    """Read a HitResult without depending on its exact field order."""
    try:
        fields = hit.to_tuple()
    except Exception:
        return None
    if fields and isinstance(fields[0], bool) and not fields[0]:
        return None                                   # not a blocking hit
    vectors = [f for f in fields if isinstance(f, unreal.Vector)]
    if not vectors:
        return None
    location = vectors[1] if len(vectors) > 1 else vectors[0]   # impact point
    actor = next((f for f in fields if isinstance(f, unreal.Actor)), None)
    return location, actor


# ------------------------------------------------------------ actors
def tag(actor, role):
    actor.set_editor_property("tags", [TAG, ROLE_PREFIX + role])


def role_of(actor):
    try:
        for t in actor.get_editor_property("tags"):
            s = str(t)
            if s.startswith(ROLE_PREFIX):
                return s[len(ROLE_PREFIX):]
    except Exception:
        pass
    return None


def demo_actors():
    """All actors this demo spawned, grouped by role."""
    out = {}
    for a in actors_sub().get_all_level_actors():
        try:
            if not a.actor_has_tag(TAG):
                continue
        except Exception:
            continue
        out.setdefault(role_of(a) or "other", []).append(a)
    return out


def destroy(actors):
    actors = [a for a in actors if a is not None]
    if actors:
        actors_sub().destroy_actors(actors)


_MESHES = {}


def basic_mesh(shape):
    """Engine basic shapes: Sphere and Cube are 1 m across; Cylinder and Cone are 1 m wide and tall."""
    mesh = _MESHES.get(shape)
    if mesh is None:
        mesh = unreal.EditorAssetLibrary.load_asset("/Engine/BasicShapes/%s.%s" % (shape, shape))
        if mesh is None:
            raise RuntimeError("Engine mesh /Engine/BasicShapes/%s is missing" % shape)
        _MESHES[shape] = mesh
    return mesh


def setup_primitive(comp, profile="NoCollision", stencil=0, shadows=True):
    comp.set_mobility(unreal.ComponentMobility.MOVABLE)
    if profile == "NoCollision":
        comp.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
    comp.set_collision_profile_name(profile)
    if stencil:
        comp.set_render_custom_depth(True)
        comp.set_custom_depth_stencil_value(stencil)
    comp.set_cast_shadow(shadows)


def spawn_mesh(shape, location, size_m, material, label, role, folder, rotation=None,
               profile="NoCollision", stencil=0, shadows=True):
    """A basic-shape StaticMeshActor. location is an unreal.Vector, size_m is (x, y, z) metres."""
    actor = actors_sub().spawn_actor_from_object(basic_mesh(shape), location, rotation or rot())
    if actor is None:
        raise RuntimeError("could not spawn %s" % label)
    comp = actor.static_mesh_component
    setup_primitive(comp, profile, stencil, shadows)
    actor.set_actor_scale3d(unreal.Vector(size_m[0], size_m[1], size_m[2]))
    if material is not None:
        comp.set_material(0, material)
    finish(actor, label, role, folder)
    return actor


def spawn_empty(location, label, role, folder):
    """A StaticMeshActor with no mesh: a movable root to attach parts to."""
    actor = actors_sub().spawn_actor_from_class(unreal.StaticMeshActor, location, rot())
    comp = actor.static_mesh_component
    comp.set_mobility(unreal.ComponentMobility.MOVABLE)
    comp.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
    finish(actor, label, role, folder)
    return actor


def spawn_text(text, location, size_cm, rgb, label, role, folder, align="center", stencil=4):
    """A TextRenderActor. stencil 4 makes it read as bright white in the thermal view."""
    actor = actors_sub().spawn_actor_from_class(unreal.TextRenderActor, location, rot())
    tr = actor.text_render
    setup_primitive(tr, "NoCollision", stencil, False)
    tr.set_text(text)
    tr.set_world_size(size_cm)
    tr.set_text_render_color(color(*rgb))
    try:
        if align == "left":
            tr.set_horizontal_alignment(unreal.HorizTextAligment.EHTA_LEFT)
            tr.set_vertical_alignment(unreal.VerticalTextAligment.EVRTA_TEXT_TOP)
        else:
            tr.set_horizontal_alignment(unreal.HorizTextAligment.EHTA_CENTER)
            tr.set_vertical_alignment(unreal.VerticalTextAligment.EVRTA_TEXT_CENTER)
    except Exception as exc:
        warn("text alignment: %s" % exc)
    finish(actor, label, role, folder)
    return actor


def finish(actor, label, role, folder):
    tag(actor, role)
    actor.set_actor_label(label)
    try:
        actor.set_folder_path(folder)
    except Exception:
        pass


def attach(child, parent):
    child.attach_to_actor(parent, "", unreal.AttachmentRule.KEEP_WORLD,
                          unreal.AttachmentRule.KEEP_WORLD, unreal.AttachmentRule.KEEP_WORLD, False)


def set_visible(actor, visible):
    """Show or hide in the editor viewport and the game view alike."""
    try:
        root = actor.get_editor_property("root_component")
        root.set_visibility(bool(visible), True)
    except Exception:
        actor.set_actor_hidden_in_game(not visible)


def place(actor, location, rotation):
    actor.set_actor_location_and_rotation(location, rotation, False, True)
