"""Patterned Glass: procedural glass panels with presets, shapes and optional animation."""

import math
import bmesh
import bpy
from bpy.props import (BoolProperty, EnumProperty, FloatProperty,
                       FloatVectorProperty, PointerProperty)

from .nodes import Builder, arrange, socket

PATTERN_VERSION = 1
TAU = math.tau

PATTERNS = [
    ("REEDED", "Reeded", "Rounded vertical ribs"),
    ("FLUTED", "Fluted", "Concave vertical channels"),
    ("FROST", "Frost", "Fine frosted grain"),
    ("LIQUID", "Liquid", "Large, smooth, wobbly distortion"),
    ("BUBBLE", "Bubble", "Grid of small dome lenses"),
    ("RIPPLE", "Ripple", "Wavy rippled texture"),
    ("HAMMERED", "Hammered", "Dimpled, hand-hammered surface"),
    ("LENTICULAR", "Lenticular", "Fine pillow-shaped lens grid"),
]

SHAPES = [
    ("RECT", "Rectangle", ""),
    ("ROUNDED", "Rounded", ""),
    ("PILL", "Pill", ""),
    ("ARCH", "Arch", ""),
    ("CIRCLE", "Circle", ""),
]

PRESETS = {
    "LUXURY_REEDED": ("Luxury Reeded", dict(pattern="REEDED", pattern_scale=24, depth=0.015, roughness=0.02, ior=1.5)),
    "FINE_FLUTED": ("Fine Fluted", dict(pattern="FLUTED", pattern_scale=60, depth=0.006, roughness=0.03, ior=1.5)),
    "SOFT_FROST": ("Soft Frost", dict(pattern="FROST", pattern_scale=150, depth=0.0006, roughness=0.3, ior=1.45)),
    "LIQUID_GLASS": ("Liquid Glass", dict(pattern="LIQUID", pattern_scale=2.5, depth=0.35, roughness=0.0, ior=1.5)),
    "BUBBLE_LENS": ("Bubble Lens", dict(pattern="BUBBLE", pattern_scale=12, depth=0.008, roughness=0.0, ior=1.5)),
    "RIPPLE_GLASS": ("Ripple Glass", dict(pattern="RIPPLE", pattern_scale=12, depth=0.008, roughness=0.02, ior=1.5)),
    "HAMMERED": ("Hammered", dict(pattern="HAMMERED", pattern_scale=10, depth=0.02, roughness=0.05, ior=1.5)),
    "LENTICULAR": ("Lenticular", dict(pattern="LENTICULAR", pattern_scale=24, depth=0.008, roughness=0.0, ior=1.55)),
}
PRESET_ITEMS = [(k, v[0], "") for k, v in PRESETS.items()]

_lock = 0  # suppresses update callbacks while a preset writes many properties


# ---------------------------------------------------------------------------
# Pattern node groups (one shared group per pattern type)
# ---------------------------------------------------------------------------

def _semicircle(b, t):
    """Rounded rib profile: 1 at the center of each unit cell, 0 at its edges."""
    f = b.math("FRACT", t)
    c = b.madd(f, 2.0, -1.0)
    return b.math("SQRT", b.math("MAXIMUM", b.sub(1.0, b.mul(c, c)), 0.0))


def _pattern_height(b, kind, co, us, vs, scale):
    if kind == "REEDED":
        return _semicircle(b, us)
    if kind == "FLUTED":
        return b.sub(1.0, _semicircle(b, us))
    if kind == "LENTICULAR":
        return b.mul(_semicircle(b, us), _semicircle(b, vs))
    if kind in ("FROST", "LIQUID"):
        n = b.new("ShaderNodeTexNoise", noise_dimensions="2D")
        b.set(n.inputs["Vector"], co)
        b.set(n.inputs["Scale"], scale)
        b.set(n.inputs["Detail"], 8.0 if kind == "FROST" else 1.5)
        b.set(n.inputs["Roughness"], 0.6 if kind == "FROST" else 0.4)
        b.set(n.inputs["Distortion"], 0.0 if kind == "FROST" else 0.4)
        return n.outputs["Fac"]
    if kind in ("BUBBLE", "HAMMERED"):
        v = b.new("ShaderNodeTexVoronoi", voronoi_dimensions="2D",
                  feature="F1" if kind == "BUBBLE" else "SMOOTH_F1")
        b.set(v.inputs["Vector"], b.vec(us, vs, 0.0))
        b.set(v.inputs["Scale"], 1.0)
        if kind == "BUBBLE":
            b.set(v.inputs["Randomness"], 0.0)
            r = b.div(v.outputs["Distance"], 0.48)
            return b.math("SQRT", b.math("MAXIMUM", b.sub(1.0, b.mul(r, r)), 0.0))
        b.set(v.inputs["Randomness"], 1.0)
        return b.math("POWER", v.outputs["Distance"], 0.8)
    if kind == "RIPPLE":
        wob = b.math("SINE", b.mul(vs, TAU * 0.35))
        return b.madd(b.math("SINE", b.madd(us, TAU, b.mul(wob, 1.5))), 0.5, 0.5)
    raise ValueError(kind)


def pattern_group(kind):
    name = f"PL Glass Pattern · {kind.title()}"
    g = bpy.data.node_groups.get(name)
    if g and g.get("pl_version") == PATTERN_VERSION:
        return g
    if g is None:
        g = bpy.data.node_groups.new(name, "ShaderNodeTree")
    g.interface.clear()
    g["pl_version"] = PATTERN_VERSION

    socket(g, "Scale", "INPUT", "NodeSocketFloat", 30.0, 0.0)
    socket(g, "Rotation", "INPUT", "NodeSocketFloat", 0.0, subtype="ANGLE")
    socket(g, "Depth", "INPUT", "NodeSocketFloat", 0.004, 0.0)
    socket(g, "Frame", "INPUT", "NodeSocketFloat", 0.0)
    socket(g, "Anim Amount", "INPUT", "NodeSocketFloat", 0.0)
    socket(g, "Anim Mode", "INPUT", "NodeSocketFloat", 0.0, 0.0, 1.0, "0 = ripples, 1 = waves")
    socket(g, "Anim Speed", "INPUT", "NodeSocketFloat", 1.0)
    socket(g, "Anim Frequency", "INPUT", "NodeSocketFloat", 6.0)
    socket(g, "Anim Direction", "INPUT", "NodeSocketFloat", 0.0, subtype="ANGLE")
    socket(g, "Normal", "OUTPUT", "NodeSocketVector")
    socket(g, "Height", "OUTPUT", "NodeSocketFloat")

    b = Builder(g)
    gi = b.new("NodeGroupInput").outputs
    go = b.new("NodeGroupOutput")

    co = b.new("ShaderNodeTexCoord").outputs["Object"]
    rot = b.new("ShaderNodeVectorRotate", rotation_type="Z_AXIS")
    b.set(rot.inputs["Vector"], co)
    b.set(rot.inputs["Angle"], gi["Rotation"])
    rco = rot.outputs[0]
    x, y, _ = b.xyz(rco)
    us, vs = b.mul(x, gi["Scale"]), b.mul(y, gi["Scale"])

    h = _pattern_height(b, kind, rco, us, vs, gi["Scale"])

    # Animation layer: travelling ripples from the center, or straight waves.
    t = b.mul(b.mul(gi["Frame"], gi["Anim Speed"]), 0.25)
    px, py, _ = b.xyz(co)
    radial = b.math("SQRT", b.madd(px, px, b.mul(py, py)))
    along = b.madd(px, b.math("COSINE", gi["Anim Direction"]),
                   b.mul(py, b.math("SINE", gi["Anim Direction"])))
    dist = b.mix(gi["Anim Mode"], radial, along)
    wave = b.math("SINE", b.sub(b.mul(dist, b.mul(gi["Anim Frequency"], TAU)), t))
    h = b.madd(wave, gi["Anim Amount"], h)

    bump = b.new("ShaderNodeBump")
    b.set(bump.inputs["Strength"], 1.0)
    b.set(bump.inputs["Distance"], gi["Depth"])
    b.set(bump.inputs["Height"], h)
    b.link(bump.outputs["Normal"], go.inputs["Normal"])
    b.link(h, go.inputs["Height"])
    arrange(g)
    return g


# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------

def _eevee_glass(mat):
    for attr, val in (("use_raytrace_refraction", True), ("thickness_mode", "SLAB"),
                      ("surface_render_method", "DITHERED")):
        if hasattr(mat, attr):
            setattr(mat, attr, val)


def _add_pattern_node(b, s):
    gn = b.new("ShaderNodeGroup", name="PL_PATTERN", label="Glass Pattern")
    gn.node_tree = pattern_group(s.pattern)
    fc = gn.inputs["Frame"].driver_add("default_value")
    fc.driver.type = "SCRIPTED"
    fc.driver.expression = "frame"
    return gn


def build_material(obj):
    s = obj.prism_glass
    old = s.material
    if s.material_mode == "CUSTOM" and s.custom_material:
        mat = s.custom_material.copy()
        mat.name = f"{s.custom_material.name} · Glass"
        b = Builder(mat.node_tree, clear=False)
        gn = _add_pattern_node(b, s)
        gn.location = (-500, 300)
        for node in mat.node_tree.nodes:
            sock = node.inputs.get("Normal") if node.type.startswith("BSDF") or "Bsdf" in node.bl_idname else None
            if sock and not sock.is_linked:
                b.link(gn.outputs["Normal"], sock)
    else:
        mat = bpy.data.materials.new(f"PL Glass · {obj.name}")
        if mat.node_tree is None:
            mat.use_nodes = True
        b = Builder(mat.node_tree)
        out = b.new("ShaderNodeOutputMaterial")
        gn = _add_pattern_node(b, s)
        if s.material_mode == "DISPERSION":
            shaders = []
            for ch in "RGB":
                g = b.new("ShaderNodeBsdfGlass", name=f"PL_GLASS_{ch}")
                b.link(gn.outputs["Normal"], g.inputs["Normal"])
                shaders.append(g.outputs[0])
            add1 = b.new("ShaderNodeAddShader")
            b.link(shaders[0], add1.inputs[0]); b.link(shaders[1], add1.inputs[1])
            add2 = b.new("ShaderNodeAddShader")
            b.link(add1.outputs[0], add2.inputs[0]); b.link(shaders[2], add2.inputs[1])
            b.link(add2.outputs[0], out.inputs["Surface"])
        else:
            p = b.new("ShaderNodeBsdfPrincipled", name="PL_BSDF")
            b.set(p.inputs["Transmission Weight"], 1.0)
            b.link(gn.outputs["Normal"], p.inputs["Normal"])
            b.link(p.outputs[0], out.inputs["Surface"])
        arrange(mat.node_tree)
    _eevee_glass(mat)
    mat["pl_glass"] = True
    obj.data.materials.clear()
    obj.data.materials.append(mat)
    s.material = mat
    if old and old != mat and old.get("pl_glass") and old.users == 0:
        bpy.data.materials.remove(old)
    sync_values(obj)


def sync_values(obj):
    s = obj.prism_glass
    mat = s.material
    if not mat or not mat.node_tree:
        return
    nt = mat.node_tree
    gn = nt.nodes.get("PL_PATTERN")
    if gn:
        if gn.node_tree is None or gn.node_tree.name != pattern_group(s.pattern).name:
            gn.node_tree = pattern_group(s.pattern)
        gi = gn.inputs
        gi["Scale"].default_value = s.pattern_scale
        gi["Rotation"].default_value = s.pattern_rotation
        gi["Depth"].default_value = s.depth
        gi["Anim Amount"].default_value = s.anim_amount if s.animate else 0.0
        gi["Anim Mode"].default_value = 1.0 if s.anim_mode == "WAVES" else 0.0
        gi["Anim Speed"].default_value = s.anim_speed
        gi["Anim Frequency"].default_value = s.anim_frequency
        gi["Anim Direction"].default_value = s.anim_direction
    p = nt.nodes.get("PL_BSDF")
    if p:
        p.inputs["Base Color"].default_value = s.tint
        p.inputs["Roughness"].default_value = s.roughness
        p.inputs["IOR"].default_value = s.ior
    for i, ch in enumerate("RGB"):
        g = nt.nodes.get(f"PL_GLASS_{ch}")
        if g:
            col = [0.0, 0.0, 0.0, 1.0]
            col[i] = s.tint[i]
            g.inputs["Color"].default_value = col
            g.inputs["Roughness"].default_value = s.roughness
            g.inputs["IOR"].default_value = s.ior + (i - 1) * s.dispersion
    mat.diffuse_color = (*s.tint[:3], 0.25)  # solid-view preview color


# ---------------------------------------------------------------------------
# Panel geometry
# ---------------------------------------------------------------------------

def _arc(cx, cy, r, a0, a1, n):
    return [(cx + r * math.cos(a0 + (a1 - a0) * i / n), cy + r * math.sin(a0 + (a1 - a0) * i / n)) for i in range(n + 1)]


def outline(shape, w, h, radius):
    hw, hh = w / 2, h / 2
    if shape == "RECT":
        return [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)]
    if shape == "CIRCLE":
        return [(hw * math.cos(TAU * i / 96), hh * math.sin(TAU * i / 96)) for i in range(96)]
    if shape in ("ROUNDED", "PILL"):
        r = min(hw, hh) if shape == "PILL" else min(radius, hw, hh)
        if r <= 1e-5:
            return outline("RECT", w, h, 0)
        seg = 16
        pts = []
        pts += _arc(hw - r, -hh + r, r, -math.pi / 2, 0, seg)
        pts += _arc(hw - r, hh - r, r, 0, math.pi / 2, seg)
        pts += _arc(-hw + r, hh - r, r, math.pi / 2, math.pi, seg)
        pts += _arc(-hw + r, -hh + r, r, math.pi, 1.5 * math.pi, seg)
        return pts
    if shape == "ARCH":
        r = hw
        top = max(hh - r, -hh)
        return [(-hw, -hh), (hw, -hh)] + _arc(0, top, r, 0, math.pi, 48)[:-1] + [(-hw, top)]
    raise ValueError(shape)


def build_panel_mesh(obj):
    s = obj.prism_glass
    bm = bmesh.new()
    verts = []
    for x, y in outline(s.shape, s.width, s.height, s.corner_radius):
        if not verts or (abs(verts[-1].co.x - x) > 1e-6 or abs(verts[-1].co.y - y) > 1e-6):
            verts.append(bm.verts.new((x, y, 0.0)))
    if (verts[0].co - verts[-1].co).length < 1e-6:
        bm.verts.remove(verts.pop())
    bm.faces.new(verts)
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    mod = obj.modifiers.get("PL Thickness") or obj.modifiers.new("PL Thickness", "SOLIDIFY")
    mod.thickness = s.thickness
    mod.offset = 0.0
    mod.use_even_offset = True


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def _upd_values(self, context):
    if _lock == 0 and self.material:
        sync_values(self.id_data)


def _upd_material(self, context):
    if _lock == 0 and self.enabled:
        build_material(self.id_data)


def _upd_shape(self, context):
    if _lock == 0 and self.is_panel:
        build_panel_mesh(self.id_data)


class PL_GlassSettings(bpy.types.PropertyGroup):
    enabled: BoolProperty(default=False)
    is_panel: BoolProperty(default=False)
    material: PointerProperty(type=bpy.types.Material)

    shape: EnumProperty(name="Shape", items=SHAPES, default="RECT", update=_upd_shape)
    width: FloatProperty(name="Width", default=1.0, min=0.01, unit="LENGTH", update=_upd_shape)
    height: FloatProperty(name="Height", default=1.5, min=0.01, unit="LENGTH", update=_upd_shape)
    corner_radius: FloatProperty(name="Corner Radius", default=0.1, min=0.0, unit="LENGTH", update=_upd_shape)
    thickness: FloatProperty(name="Thickness", default=0.02, min=0.0, unit="LENGTH", update=_upd_shape)

    pattern: EnumProperty(name="Pattern", items=PATTERNS, default="REEDED", update=_upd_values)
    pattern_scale: FloatProperty(name="Scale", default=24.0, min=0.0, soft_max=300.0, update=_upd_values,
                                 description="Pattern repeats per meter")
    pattern_rotation: FloatProperty(name="Rotation", default=0.0, subtype="ANGLE", update=_upd_values)
    depth: FloatProperty(name="Depth", default=0.015, min=0.0, soft_max=0.5, precision=4, update=_upd_values,
                         description="How strongly the pattern bends light")

    material_mode: EnumProperty(name="Material", items=[
        ("GLASS", "Glass", "Clear glass"),
        ("DISPERSION", "Glass Dispersion", "Rainbow edges from color-split refraction (best in Cycles)"),
        ("CUSTOM", "Custom", "Apply the pattern to a copy of your own material"),
    ], default="GLASS", update=_upd_material)
    custom_material: PointerProperty(name="Custom Material", type=bpy.types.Material, update=_upd_material)
    tint: FloatVectorProperty(name="Tint", subtype="COLOR", size=4, default=(1, 1, 1, 1), min=0, max=1, update=_upd_values)
    roughness: FloatProperty(name="Roughness", default=0.02, min=0.0, max=1.0, update=_upd_values)
    ior: FloatProperty(name="IOR", default=1.5, min=1.0, max=3.0, update=_upd_values)
    dispersion: FloatProperty(name="Dispersion", default=0.03, min=0.0, max=0.3, update=_upd_values)

    animate: BoolProperty(name="Animate Glass", default=False, update=_upd_values)
    anim_mode: EnumProperty(name="Motion", items=[
        ("RIPPLES", "Ripples", "Rings travelling out from the panel center"),
        ("WAVES", "Waves", "Straight waves travelling in one direction"),
    ], default="RIPPLES", update=_upd_values)
    anim_amount: FloatProperty(name="Amount", default=0.5, min=0.0, soft_max=2.0, update=_upd_values)
    anim_speed: FloatProperty(name="Speed", default=1.0, soft_min=-5.0, soft_max=5.0, update=_upd_values)
    anim_frequency: FloatProperty(name="Frequency", default=4.0, min=0.0, soft_max=30.0, update=_upd_values)
    anim_direction: FloatProperty(name="Direction", default=0.0, subtype="ANGLE", update=_upd_values)


def apply_preset(obj, key):
    global _lock
    s = obj.prism_glass
    _lock += 1
    try:
        for k, v in PRESETS[key][1].items():
            setattr(s, k, v)
    finally:
        _lock -= 1
    sync_values(obj)


def make_glass(obj, preset=None):
    global _lock
    s = obj.prism_glass
    s.enabled = True
    if preset:
        _lock += 1
        try:
            for k, v in PRESETS[preset][1].items():
                setattr(s, k, v)
        finally:
            _lock -= 1
    build_material(obj)


def enable_eevee_raytracing(scene):
    if hasattr(scene, "eevee") and hasattr(scene.eevee, "use_raytracing"):
        scene.eevee.use_raytracing = True


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------

class PRISM_OT_add_glass_panel(bpy.types.Operator):
    bl_idname = "prism.add_glass_panel"
    bl_label = "Add Glass Panel"
    bl_description = "Add a procedural patterned glass panel at the 3D cursor"
    bl_options = {"REGISTER", "UNDO"}

    preset: EnumProperty(name="Preset", items=PRESET_ITEMS, default="LUXURY_REEDED")
    shape: EnumProperty(name="Shape", items=SHAPES, default="ROUNDED")

    def execute(self, context):
        global _lock
        mesh = bpy.data.meshes.new("Glass Panel")
        obj = bpy.data.objects.new("Glass Panel", mesh)
        context.collection.objects.link(obj)
        obj.location = context.scene.cursor.location
        obj.rotation_euler = (math.pi / 2, 0, 0)
        s = obj.prism_glass
        _lock += 1
        try:
            s.is_panel = True
            s.shape = self.shape
        finally:
            _lock -= 1
        build_panel_mesh(obj)
        make_glass(obj, self.preset)
        enable_eevee_raytracing(context.scene)
        for o in context.selected_objects:
            o.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        return {"FINISHED"}


class PRISM_OT_make_glass(bpy.types.Operator):
    bl_idname = "prism.make_glass"
    bl_label = "Make Selected Glass"
    bl_description = "Turn the selected meshes into patterned glass"
    bl_options = {"REGISTER", "UNDO"}

    preset: EnumProperty(name="Preset", items=PRESET_ITEMS, default="LUXURY_REEDED")

    @classmethod
    def poll(cls, context):
        return any(o.type == "MESH" for o in context.selected_objects)

    def execute(self, context):
        for o in context.selected_objects:
            if o.type == "MESH":
                make_glass(o, self.preset)
        enable_eevee_raytracing(context.scene)
        return {"FINISHED"}


class PRISM_OT_glass_preset(bpy.types.Operator):
    bl_idname = "prism.glass_preset"
    bl_label = "Glass Preset"
    bl_description = "Apply a glass preset to the selected glass objects"
    bl_options = {"REGISTER", "UNDO"}

    preset: EnumProperty(name="Preset", items=PRESET_ITEMS)

    def execute(self, context):
        objs = {o for o in context.selected_objects if o.prism_glass.enabled}
        if context.object and context.object.prism_glass.enabled:
            objs.add(context.object)
        for o in objs:
            apply_preset(o, self.preset)
        return {"FINISHED"}


class PRISM_OT_remove_glass(bpy.types.Operator):
    bl_idname = "prism.remove_glass"
    bl_label = "Remove Glass"
    bl_description = "Remove the glass material from the active object (panel geometry stays)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        o = context.object
        if o and o.prism_glass.enabled:
            mat = o.prism_glass.material
            o.data.materials.clear()
            o.prism_glass.enabled = False
            o.prism_glass.material = None
            if mat and mat.users == 0:
                bpy.data.materials.remove(mat)
        return {"FINISHED"}


def draw(layout, context):
    obj = context.object
    row = layout.row(align=True)
    row.operator_menu_enum("prism.add_glass_panel", "shape", text="Add Panel", icon="MESH_PLANE")
    row.operator("prism.make_glass", text="Selected", icon="SHADING_RENDERED")
    if not obj or not obj.prism_glass.enabled:
        return
    s = obj.prism_glass
    layout.operator_menu_enum("prism.glass_preset", "preset", text="Presets", icon="PRESET")

    if s.is_panel:
        box = layout.box()
        box.label(text="Panel", icon="MESH_PLANE")
        box.prop(s, "shape", text="")
        col = box.column(align=True)
        col.prop(s, "width"); col.prop(s, "height"); col.prop(s, "thickness")
        if s.shape == "ROUNDED":
            col.prop(s, "corner_radius")

    box = layout.box()
    box.label(text="Pattern", icon="TEXTURE")
    box.prop(s, "pattern", text="")
    col = box.column(align=True)
    col.prop(s, "pattern_scale"); col.prop(s, "depth"); col.prop(s, "pattern_rotation")

    box = layout.box()
    box.label(text="Material", icon="MATERIAL")
    box.prop(s, "material_mode", text="")
    if s.material_mode == "CUSTOM":
        box.prop(s, "custom_material", text="")
    else:
        box.prop(s, "tint")
        col = box.column(align=True)
        col.prop(s, "roughness"); col.prop(s, "ior")
        if s.material_mode == "DISPERSION":
            col.prop(s, "dispersion")

    box = layout.box()
    box.prop(s, "animate", icon="MOD_WAVE")
    if s.animate:
        box.row().prop(s, "anim_mode", expand=True)
        col = box.column(align=True)
        col.prop(s, "anim_amount"); col.prop(s, "anim_speed"); col.prop(s, "anim_frequency")
        if s.anim_mode == "WAVES":
            col.prop(s, "anim_direction")
    layout.operator("prism.remove_glass", icon="X")


classes = (PL_GlassSettings, PRISM_OT_add_glass_panel, PRISM_OT_make_glass,
           PRISM_OT_glass_preset, PRISM_OT_remove_glass)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Object.prism_glass = PointerProperty(type=PL_GlassSettings)


def unregister():
    del bpy.types.Object.prism_glass
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
