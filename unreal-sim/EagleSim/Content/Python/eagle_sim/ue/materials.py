"""Materials the demo generates under /Game/EagleSim/Materials.

They are built by script, so the repository holds no binary .uasset files:
  M_EagleSolid      a simple lit surface with Color, Roughness and Glow parameters
  MI_*              instances of it for leaves, bark, camouflage, the muntjac, the eagle ...
  M_EagleThermal    the post-process material that turns the Eagle camera into a white-hot
                    long-wave infrared view. Warm bodies are drawn from the Custom Depth stencil
                    (1 person, 2 animal, 4 on-screen text) and only where nothing
                    stands in front of them, so the person shows through canopy gaps only.
"""
import unreal

from . import util

DIR = "/Game/EagleSim/Materials"
SOLID = "M_EagleSolid"
THERMAL = "M_EagleThermal"

# name: (linear RGB, roughness, glow)
SURFACES = {
    "Leaf1": ((0.030, 0.085, 0.022), 0.85, 0.0),
    "Leaf2": ((0.045, 0.110, 0.030), 0.85, 0.0),
    "Leaf3": ((0.060, 0.090, 0.025), 0.85, 0.0),
    "Bark": ((0.060, 0.040, 0.025), 0.90, 0.0),
    "Camo": ((0.070, 0.075, 0.045), 0.90, 0.0),
    "Skin": ((0.350, 0.220, 0.150), 0.60, 0.0),
    "Muntjac": ((0.250, 0.120, 0.050), 0.80, 0.0),
    "Eagle": ((0.070, 0.045, 0.025), 0.70, 0.0),
    "EagleHead": ((0.300, 0.250, 0.180), 0.70, 0.0),
    "Beak": ((0.250, 0.220, 0.100), 0.50, 0.0),
}

THERMAL_HLSL = r"""
// White-hot long-wave IR look for the Eagle's Boson-class camera.
float sceneDepth = SceneD.r;
float customDepth = CustD.r;
float stencil = floor(CustS.r + 0.5);
float lum = dot(SceneC.rgb, float3(0.299, 0.587, 0.114));
// Background: wet canopy reads cool, sunlit soil and rock warmer, sky coldest.
float g = 0.10 + 0.42 * saturate(lum * 1.2);
if (sceneDepth > 2000000.0) { g = 0.03 + 0.05 * ScreenUV.y; }
// Warm bodies show only where nothing stands in front of them (canopy gaps).
if (stencil > 0.5 && sceneDepth > customDepth - 30.0)
{
    if (stencil < 1.5)      { g = max(g, 0.97); }   // person, about 34 C
    else if (stencil < 2.5) { g = max(g, 0.82); }   // animal, about 30 C
    else                    { g = 1.0; }            // on-screen text
}
// Sensor noise and a little lens vignette, except on the on-screen text.
if (stencil < 3.5)
{
    float n = frac(sin(dot(ScreenUV * float2(1279.0, 911.0) + frac(Seconds * 7.0) * 61.0,
                           float2(12.9898, 78.233))) * 43758.5453);
    g += (n - 0.5) * 0.05;
    float2 v = ScreenUV - 0.5;
    g *= 1.0 - 0.55 * dot(v, v);
}
return saturate(g).xxx;
"""


def _path(name):
    return "%s/%s" % (DIR, name)


def _load(name):
    path = _path(name)
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        return unreal.EditorAssetLibrary.load_asset(path)
    return None


def _create(name, cls, factory):
    asset = unreal.AssetToolsHelpers.get_asset_tools().create_asset(name, DIR, cls, factory)
    if asset is None:
        raise RuntimeError("could not create %s" % _path(name))
    return asset


def _save(name):
    unreal.EditorAssetLibrary.save_asset(_path(name), False)


def _connect(src, outputs, dst, input_name):
    mel = unreal.MaterialEditingLibrary
    for out in outputs:
        if mel.connect_material_expressions(src, out, dst, input_name):
            return
    raise RuntimeError("could not connect %s to input %s" % (src.get_name(), input_name))


def _fresh(name, rebuild):
    """Existing material with its graph cleared, or a new one."""
    mat = _load(name)
    if mat is not None and not rebuild:
        return mat, False
    if mat is None:
        mat = _create(name, unreal.Material, unreal.MaterialFactoryNew())
    else:
        unreal.MaterialEditingLibrary.delete_all_material_expressions(mat)
    return mat, True


def _build_solid(rebuild):
    mat, fresh = _fresh(SOLID, rebuild)
    if not fresh:
        return mat
    mel = unreal.MaterialEditingLibrary
    col = mel.create_material_expression(mat, unreal.MaterialExpressionVectorParameter, -700, -100)
    col.set_editor_property("parameter_name", "Color")
    col.set_editor_property("default_value", unreal.LinearColor(0.05, 0.1, 0.03, 1.0))
    rough = mel.create_material_expression(mat, unreal.MaterialExpressionScalarParameter, -700, 150)
    rough.set_editor_property("parameter_name", "Roughness")
    rough.set_editor_property("default_value", 0.8)
    glow = mel.create_material_expression(mat, unreal.MaterialExpressionScalarParameter, -700, 300)
    glow.set_editor_property("parameter_name", "Glow")
    glow.set_editor_property("default_value", 0.0)
    mul = mel.create_material_expression(mat, unreal.MaterialExpressionMultiply, -350, 250)
    _connect(col, [""], mul, "A")
    _connect(glow, [""], mul, "B")
    ok = (mel.connect_material_property(col, "", unreal.MaterialProperty.MP_BASE_COLOR)
          and mel.connect_material_property(rough, "", unreal.MaterialProperty.MP_ROUGHNESS)
          and mel.connect_material_property(mul, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR))
    if not ok:
        raise RuntimeError("could not wire %s" % SOLID)
    mel.recompile_material(mat)
    _save(SOLID)
    return mat


def _build_thermal(rebuild):
    mat, fresh = _fresh(THERMAL, rebuild)
    if not fresh:
        return mat
    mel = unreal.MaterialEditingLibrary
    mat.set_editor_property("material_domain", unreal.MaterialDomain.MD_POST_PROCESS)
    mat.set_editor_property("blendable_location", util.enum(
        unreal.BlendableLocation, "BL_SCENE_COLOR_AFTER_TONEMAPPING", "BL_AFTER_TONEMAPPING"))
    custom = mel.create_material_expression(mat, unreal.MaterialExpressionCustom, -350, 0)
    custom.set_editor_property("description", "EagleThermal")
    custom.set_editor_property("output_type", unreal.CustomMaterialOutputType.CMOT_FLOAT3)
    names = ["SceneC", "SceneD", "CustD", "CustS", "ScreenUV", "Seconds"]
    custom.set_editor_property("inputs", [unreal.CustomInput(input_name=n) for n in names])
    custom.set_editor_property("code", THERMAL_HLSL)
    sid = unreal.SceneTextureId
    textures = [("SceneC", sid.PPI_POST_PROCESS_INPUT0), ("SceneD", sid.PPI_SCENE_DEPTH),
                ("CustD", sid.PPI_CUSTOM_DEPTH), ("CustS", sid.PPI_CUSTOM_STENCIL)]
    y = -400
    for input_name, texture_id in textures:
        st = mel.create_material_expression(mat, unreal.MaterialExpressionSceneTexture, -800, y)
        st.set_editor_property("scene_texture_id", texture_id)
        _connect(st, ["Color", ""], custom, input_name)
        y += 160
    uv = mel.create_material_expression(mat, unreal.MaterialExpressionTextureCoordinate, -800, y)
    _connect(uv, [""], custom, "ScreenUV")
    t = mel.create_material_expression(mat, unreal.MaterialExpressionTime, -800, y + 160)
    _connect(t, [""], custom, "Seconds")
    if not mel.connect_material_property(custom, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR):
        raise RuntimeError("could not wire %s" % THERMAL)
    mel.recompile_material(mat)
    _save(THERMAL)
    return mat


def ensure(rebuild=False):
    """Create (or load) every material. Returns {name: material}; "Thermal" is the post-process one."""
    if not unreal.EditorAssetLibrary.does_directory_exist(DIR):
        unreal.EditorAssetLibrary.make_directory(DIR)
    mel = unreal.MaterialEditingLibrary
    solid = _build_solid(rebuild)
    out = {}
    for name, (rgb, rough, glow) in SURFACES.items():
        asset = "MI_" + name
        mi = _load(asset)
        if mi is None:
            mi = _create(asset, unreal.MaterialInstanceConstant,
                         unreal.MaterialInstanceConstantFactoryNew())
        mel.set_material_instance_parent(mi, solid)
        mel.set_material_instance_vector_parameter_value(mi, "Color",
                                                         unreal.LinearColor(rgb[0], rgb[1], rgb[2], 1.0))
        mel.set_material_instance_scalar_parameter_value(mi, "Roughness", rough)
        mel.set_material_instance_scalar_parameter_value(mi, "Glow", glow)
        mel.update_material_instance(mi)
        _save(asset)
        out[name] = mi
    out["Thermal"] = _build_thermal(rebuild)
    return out


def thermal_settings(camera_component, thermal_material):
    """Put the thermal look on one camera only: piloting that camera shows the IR view."""
    pps = camera_component.get_editor_property("post_process_settings")
    pps.set_editor_property("weighted_blendables", unreal.WeightedBlendables(
        array=[unreal.WeightedBlendable(weight=1.0, object=thermal_material)]))
    for key, value in (("motion_blur_amount", 0.0), ("bloom_intensity", 0.0),
                       ("vignette_intensity", 0.0)):
        try:
            pps.set_editor_property("override_" + key, True)
            pps.set_editor_property(key, value)
        except Exception as exc:
            util.warn("thermal camera setting %s: %s" % (key, exc))
    camera_component.set_editor_property("post_process_settings", pps)
    camera_component.set_post_process_blend_weight(1.0)
