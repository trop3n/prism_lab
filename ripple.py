"""Surface Ripple: non-destructive ripple deformation driven by a movable origin empty."""

import math
import bpy
from bpy.props import EnumProperty, PointerProperty

from .nodes import Builder, arrange, draw_input, get_input, modifier_inputs, set_input, set_menu, socket, versioned_group

GROUP_NAME = "PL Surface Ripple"
GROUP_VERSION = 2
MOD_NAME = "Prism Ripple"
TAU = math.tau

PATTERNS = [
    ("CIRCULAR", "Circular", "Rings spreading from the origin"),
    ("LINEAR", "Linear Waves", "Straight parallel waves"),
    ("DROPLET", "Droplet", "A single expanding ring that repeats"),
    ("STANDING", "Standing Wave", "Rings that pulse in place"),
]
PATTERN_INDEX = {k: i for i, (k, _, _) in enumerate(PATTERNS)}

PRESETS = {
    "CALM_POND": ("Calm Pond", dict(pattern="CIRCULAR", Amplitude=0.02, Wavelength=0.3, Speed=0.3, Falloff=0.6, Noise=0.05)),
    "RAINDROP": ("Raindrop", dict(pattern="DROPLET", Amplitude=0.04, Wavelength=0.12, Speed=0.6, Falloff=0.3, Radius=1.5, Noise=0.0)),
    "SHOCKWAVE": ("Shockwave", dict(pattern="DROPLET", Amplitude=0.1, Wavelength=0.35, Speed=1.2, Falloff=0.1, Radius=2.5, Noise=0.0)),
    "SILK_WAVES": ("Silk Waves", dict(pattern="LINEAR", Amplitude=0.07, Wavelength=0.7, Speed=0.25, Falloff=0.0, Noise=0.25)),
    "STANDING_WAVE": ("Standing Wave", dict(pattern="STANDING", Amplitude=0.05, Wavelength=0.3, Speed=0.5, Falloff=0.4, Noise=0.0)),
    "LIQUID_METAL": ("Liquid Metal", dict(pattern="CIRCULAR", Amplitude=0.01, Wavelength=0.16, Speed=0.2, Falloff=0.2, Noise=1.2)),
}
PRESET_ITEMS = [(k, v[0], "") for k, v in PRESETS.items()]

QUALITY = {
    "PREVIEW": {"Viewport Level": 1, "Render Level": 2},
    "FINAL": {"Viewport Level": 2, "Render Level": 4},
}


def ripple_group():
    g, needs_build = versioned_group(GROUP_NAME, GROUP_VERSION, "GeometryNodeTree")
    if not needs_build:
        return g
    g.is_modifier = True

    socket(g, "Geometry", "INPUT", "NodeSocketGeometry")
    socket(g, "Origin", "INPUT", "NodeSocketObject", desc="Empty marking the ripple center")
    socket(g, "Pattern", "INPUT", "NodeSocketInt", 0, 0, len(PATTERNS) - 1)
    socket(g, "Amplitude", "INPUT", "NodeSocketFloat", 0.03, subtype="DISTANCE")
    socket(g, "Wavelength", "INPUT", "NodeSocketFloat", 0.25, 0.001, subtype="DISTANCE")
    socket(g, "Speed", "INPUT", "NodeSocketFloat", 0.5, desc="Meters per second")
    socket(g, "Phase", "INPUT", "NodeSocketFloat", 0.0, subtype="ANGLE")
    socket(g, "Falloff", "INPUT", "NodeSocketFloat", 0.5, 0.0, desc="How quickly ripples fade with distance")
    socket(g, "Radius", "INPUT", "NodeSocketFloat", 2.0, 0.01, subtype="DISTANCE", desc="Droplet: how far a ring travels before restarting")
    socket(g, "Direction", "INPUT", "NodeSocketFloat", 0.0, subtype="ANGLE", desc="Linear waves direction")
    socket(g, "Noise", "INPUT", "NodeSocketFloat", 0.0, 0.0, desc="Organic distortion of the wave fronts")
    socket(g, "Viewport Level", "INPUT", "NodeSocketInt", 2, 0, 6)
    socket(g, "Render Level", "INPUT", "NodeSocketInt", 3, 0, 6)
    socket(g, "Smooth Subdivision", "INPUT", "NodeSocketBool", True)
    socket(g, "Shade Smooth", "INPUT", "NodeSocketBool", True)
    socket(g, "Geometry", "OUTPUT", "NodeSocketGeometry")

    b = Builder(g)
    gi = b.new("NodeGroupInput").outputs
    go = b.new("NodeGroupOutput")

    # --- subdivision (viewport vs render level) ---
    isvp = b.new("GeometryNodeIsViewport").outputs[0]
    lvl = b.new("GeometryNodeSwitch", input_type="INT")
    b.set(lvl.inputs["Switch"], isvp)
    b.set(lvl.inputs["False"], gi["Render Level"])
    b.set(lvl.inputs["True"], gi["Viewport Level"])
    smooth = b.new("GeometryNodeSubdivisionSurface")
    b.set(smooth.inputs["Mesh"], gi[0])
    b.set(smooth.inputs["Level"], lvl.outputs[0])
    set_menu(smooth.inputs["Boundary Smooth"], "Keep Corners")  # flat meshes keep their corners
    linear = b.new("GeometryNodeSubdivideMesh")
    b.set(linear.inputs["Mesh"], gi[0])
    b.set(linear.inputs["Level"], lvl.outputs[0])
    gsw = b.new("GeometryNodeSwitch", input_type="GEOMETRY")
    b.set(gsw.inputs["Switch"], gi["Smooth Subdivision"])
    b.set(gsw.inputs["False"], linear.outputs[0])
    b.set(gsw.inputs["True"], smooth.outputs[0])

    # --- height field ---
    info = b.new("GeometryNodeObjectInfo", transform_space="RELATIVE")
    b.set(info.inputs["Object"], gi["Origin"])
    pos = b.new("GeometryNodeInputPosition").outputs[0]
    rel = b.vmath("SUBTRACT", pos, info.outputs["Location"])
    rx, ry, _ = b.xyz(rel)

    noise = b.new("ShaderNodeTexNoise")
    b.set(noise.inputs["Vector"], pos)
    b.set(noise.inputs["Scale"], b.div(1.5, gi["Wavelength"]))
    b.set(noise.inputs["Detail"], 2.0)
    wobble = b.mul(b.sub(noise.outputs["Fac"], 0.5), b.mul(gi["Noise"], gi["Wavelength"]))

    d = b.add(b.vmath("LENGTH", rel), wobble)
    k = b.div(TAU, gi["Wavelength"])
    time = b.new("GeometryNodeInputSceneTime").outputs["Seconds"]
    travel = b.mul(time, gi["Speed"])
    damp = b.math("EXPONENT", b.mul(b.mul(d, gi["Falloff"]), -1.0))

    # 0 circular
    circ = b.mul(b.math("SINE", b.madd(b.sub(d, travel), k, gi["Phase"])), damp)
    # 1 linear
    along = b.add(b.madd(rx, b.math("COSINE", gi["Direction"]), b.mul(ry, b.math("SINE", gi["Direction"]))), wobble)
    lin = b.math("SINE", b.madd(b.sub(along, travel), k, gi["Phase"]))
    # 2 droplet: one ring whose front moves outward, restarting every Radius meters
    front = b.math("FLOORED_MODULO", travel, gi["Radius"])
    env_x = b.div(b.sub(d, front), b.mul(gi["Wavelength"], 0.75))
    envelope = b.math("EXPONENT", b.mul(b.mul(env_x, env_x), -1.0))
    fade = b.math("MAXIMUM", b.sub(1.0, b.div(front, gi["Radius"])), 0.0)
    drop = b.mul(b.mul(b.math("SINE", b.madd(b.sub(d, front), k, gi["Phase"])), envelope), b.mul(fade, damp))
    # 3 standing
    stand = b.mul(b.mul(b.math("SINE", b.madd(d, k, gi["Phase"])), b.math("COSINE", b.mul(travel, k))), damp)

    sw = b.new("GeometryNodeIndexSwitch", data_type="FLOAT")
    while len(sw.index_switch_items) < 4:
        sw.index_switch_items.new()
    b.set(sw.inputs["Index"], gi["Pattern"])
    for i, v in enumerate((circ, lin, drop, stand)):
        b.set(sw.inputs[i + 1], v)
    height = b.mul(sw.outputs[0], gi["Amplitude"])

    normal = b.new("GeometryNodeInputNormal").outputs["Normal"]
    setp = b.new("GeometryNodeSetPosition")
    b.set(setp.inputs["Geometry"], gsw.outputs[0])
    b.set(setp.inputs["Offset"], b.vmath("SCALE", normal, scale=height))

    shade = b.new("GeometryNodeSetShadeSmooth")
    b.set(shade.inputs[0], setp.outputs[0])
    b.set(shade.inputs["Selection"], gi["Shade Smooth"])
    b.set(shade.inputs["Shade Smooth"], True)
    b.link(shade.outputs[0], go.inputs[0])
    arrange(g)
    return g


def get_mod(obj):
    return obj.modifiers.get(MOD_NAME) if obj else None


def set_inputs(obj, values):
    mod = get_mod(obj)
    if not mod:
        return
    keys = modifier_inputs(mod)
    for name, val in values.items():
        if name in keys:
            set_input(mod, keys[name], val)
    obj.update_tag()


def _upd_pattern(self, context):
    set_inputs(self.id_data, {"Pattern": PATTERN_INDEX[self.pattern]})


class PL_RippleSettings(bpy.types.PropertyGroup):
    pattern: EnumProperty(name="Pattern", items=PATTERNS, default="CIRCULAR", update=_upd_pattern)


class PRISM_OT_add_ripple(bpy.types.Operator):
    bl_idname = "prism.add_ripple"
    bl_label = "Add Surface Ripple"
    bl_description = "Add a non-destructive ripple deformation to the selected meshes"
    bl_options = {"REGISTER", "UNDO"}

    preset: EnumProperty(name="Preset", items=PRESET_ITEMS, default="CALM_POND")

    @classmethod
    def poll(cls, context):
        return any(o.type == "MESH" for o in context.selected_objects)

    def execute(self, context):
        g = ripple_group()
        for obj in [o for o in context.selected_objects if o.type == "MESH"]:
            mod = get_mod(obj) or obj.modifiers.new(MOD_NAME, "NODES")
            mod.node_group = g
            origin = bpy.data.objects.new(f"{obj.name} Ripple Origin", None)
            origin.empty_display_type = "SPHERE"
            origin.empty_display_size = 0.1
            context.collection.objects.link(origin)
            origin.parent = obj
            set_inputs(obj, {"Origin": origin})
            apply_preset(obj, self.preset)
        return {"FINISHED"}


def apply_preset(obj, key):
    values = dict(PRESETS[key][1])
    obj.prism_ripple.pattern = values.pop("pattern")
    set_inputs(obj, values)


class PRISM_OT_ripple_preset(bpy.types.Operator):
    bl_idname = "prism.ripple_preset"
    bl_label = "Ripple Preset"
    bl_options = {"REGISTER", "UNDO"}
    preset: EnumProperty(name="Preset", items=PRESET_ITEMS)

    def execute(self, context):
        for o in context.selected_objects:
            if get_mod(o):
                apply_preset(o, self.preset)
        return {"FINISHED"}


class PRISM_OT_ripple_quality(bpy.types.Operator):
    bl_idname = "prism.ripple_quality"
    bl_label = "Ripple Quality"
    bl_description = "Set subdivision levels for preview or final quality"
    bl_options = {"REGISTER", "UNDO"}
    quality: EnumProperty(items=[("PREVIEW", "Preview", "Fast, low subdivision"),
                                 ("FINAL", "Final", "Dense subdivision for renders")])

    def execute(self, context):
        for o in context.selected_objects:
            set_inputs(o, QUALITY[self.quality])
        return {"FINISHED"}


class PRISM_OT_select_ripple_origin(bpy.types.Operator):
    bl_idname = "prism.select_ripple_origin"
    bl_label = "Select Origin"
    bl_description = "Select the ripple origin so you can move it"

    def execute(self, context):
        obj = context.object
        mod = get_mod(obj)
        origin = get_input(mod, modifier_inputs(mod)["Origin"]) if mod else None
        if origin:
            for o in context.selected_objects:
                o.select_set(False)
            origin.select_set(True)
            context.view_layer.objects.active = origin
        return {"FINISHED"}


class PRISM_OT_remove_ripple(bpy.types.Operator):
    bl_idname = "prism.remove_ripple"
    bl_label = "Remove Ripple"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = context.object
        mod = get_mod(obj)
        if mod:
            origin = get_input(mod, modifier_inputs(mod)["Origin"])
            obj.modifiers.remove(mod)
            if origin and origin.parent == obj:
                bpy.data.objects.remove(origin)
        return {"FINISHED"}


def draw(layout, context):
    obj = context.object
    # Show the mesh's controls when its origin empty is selected too.
    if obj and obj.type == "EMPTY" and obj.parent and get_mod(obj.parent):
        obj = obj.parent
    mod = get_mod(obj)
    if not mod:
        layout.operator_menu_enum("prism.add_ripple", "preset", text="Add Ripple", icon="MOD_WAVE")
        return
    keys = modifier_inputs(mod)
    row = layout.row(align=True)
    row.operator_menu_enum("prism.ripple_preset", "preset", text="Presets", icon="PRESET")
    row.operator("prism.select_ripple_origin", text="", icon="EMPTY_AXIS")

    def prop(col, name, text=None):
        if name in keys:
            draw_input(col, mod, keys[name], text or name)

    box = layout.box()
    box.prop(obj.prism_ripple, "pattern", text="")
    col = box.column(align=True)
    for n in ("Amplitude", "Wavelength", "Speed", "Phase", "Falloff", "Noise"):
        prop(col, n)
    p = obj.prism_ripple.pattern
    if p == "DROPLET":
        prop(col, "Radius")
    if p == "LINEAR":
        prop(col, "Direction")
    prop(box, "Origin")

    box = layout.box()
    row = box.row(align=True)
    row.label(text="Quality")
    row.operator("prism.ripple_quality", text="Preview").quality = "PREVIEW"
    row.operator("prism.ripple_quality", text="Final").quality = "FINAL"
    col = box.column(align=True)
    prop(col, "Viewport Level"); prop(col, "Render Level")
    prop(box, "Smooth Subdivision"); prop(box, "Shade Smooth")
    layout.operator("prism.remove_ripple", icon="X")


classes = (PL_RippleSettings, PRISM_OT_add_ripple, PRISM_OT_ripple_preset,
           PRISM_OT_ripple_quality, PRISM_OT_select_ripple_origin, PRISM_OT_remove_ripple)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Object.prism_ripple = PointerProperty(type=PL_RippleSettings)


def unregister():
    del bpy.types.Object.prism_ripple
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
