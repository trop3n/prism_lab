"""Compositor effects: Optic Blur regions and Glow.

Everything lives in one compositor node group ("PL Optic FX · <scene>") that is
inserted once in front of the scene compositor's output, so the user's own
compositor nodes are left alone. The group's insides are rebuilt when the
structure changes (regions added/removed, modes switched) and otherwise only
have their values updated.
"""

import math
import bpy
from bpy.app.handlers import persistent
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty,
                       IntProperty, PointerProperty, StringProperty)
from bpy_extras.object_utils import world_to_camera_view

from .nodes import Builder, arrange, set_menu

FX_NODE = "PL_FX"
LENGTH_SCALE = 0.05  # Length 1 = streak of 5% of the frame


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def _upd_struct(self, context):
    scene = self.id_data
    if isinstance(scene, bpy.types.Scene):
        refresh(scene)


def _upd_values(self, context):
    scene = self.id_data
    if isinstance(scene, bpy.types.Scene):
        sync_values(scene)


class PL_ObjectRef(bpy.types.PropertyGroup):
    obj: PointerProperty(type=bpy.types.Object, update=_upd_values)


class PL_BlurRegion(bpy.types.PropertyGroup):
    name: StringProperty(default="Blur")
    enabled: BoolProperty(name="Enabled", default=True, update=_upd_struct)
    mode: EnumProperty(name="Mode", items=[
        ("OBJECTS", "Selected Mesh", "Blur specific objects (uses the Cryptomatte object pass)"),
        ("AREA", "Area", "Blur a box or ellipse region of the frame"),
    ], default="OBJECTS", update=_upd_struct)
    objects: CollectionProperty(type=PL_ObjectRef)
    shape: EnumProperty(name="Shape", items=[("BOX", "Box", ""), ("ELLIPSE", "Ellipse", "")],
                        default="ELLIPSE", update=_upd_struct)
    center_x: FloatProperty(name="X", default=0.5, soft_min=0.0, soft_max=1.0, update=_upd_values)
    center_y: FloatProperty(name="Y", default=0.5, soft_min=0.0, soft_max=1.0, update=_upd_values)
    width: FloatProperty(name="Width", default=0.4, min=0.0, soft_max=1.5, update=_upd_values,
                         description="Fraction of the frame width")
    height: FloatProperty(name="Height", default=0.3, min=0.0, soft_max=1.5, update=_upd_values,
                          description="Fraction of the frame height")
    area_rotation: FloatProperty(name="Rotation", default=0.0, subtype="ANGLE", update=_upd_values)
    follow: PointerProperty(name="Follow", type=bpy.types.Object, update=_upd_values,
                            description="Keep the area centered on this object as seen by the camera")

    amount: FloatProperty(name="Blur Amount", default=1.0, min=0.0, max=1.0, subtype="FACTOR", update=_upd_values)
    length: FloatProperty(name="Length", default=1.0, min=0.0, max=6.0, update=_upd_values,
                          description="Streak length (1 = 5% of the frame width)")
    direction: FloatProperty(name="Direction", default=0.0, subtype="ANGLE", update=_upd_values,
                             description="Direction of motion (0 = right); the streak trails behind")
    softness: FloatProperty(name="Edge Softness", default=12.0, min=0.0, soft_max=200.0, update=_upd_values,
                            description="Feathering of the blur edge, in pixels")
    trail: BoolProperty(name="Smear Beyond Edges", default=True, update=_upd_values,
                        description="Streak the mask too, so the blur trails past the object's silhouette")
    samples: IntProperty(name="Quality", default=16, min=1, max=256, update=_upd_values)

    animate: BoolProperty(name="Animate", default=False, update=_upd_values)
    pulse_speed: FloatProperty(name="Pulse", default=1.0, min=0.0, soft_max=10.0, update=_upd_values,
                               description="How fast the blur length breathes in and out (cycles per second)")
    pulse_depth: FloatProperty(name="Pulse Depth", default=0.6, min=0.0, max=1.0, subtype="FACTOR", update=_upd_values)
    spin_speed: FloatProperty(name="Spin", default=0.0, soft_min=-5.0, soft_max=5.0, update=_upd_values,
                              description="Rotate the blur direction over time (turns per second)")


class PL_FXSettings(bpy.types.PropertyGroup):
    regions: CollectionProperty(type=PL_BlurRegion)
    active: IntProperty(default=0)
    live_update: BoolProperty(name="Live Update", default=True,
                              description="Keep the compositor in sync on every frame change and scene edit")

    glow: BoolProperty(name="Glow", default=False, update=_upd_struct)
    glow_type: EnumProperty(name="Type", items=[("Bloom", "Bloom", ""), ("Fog Glow", "Fog Glow", ""),
                                                ("Streaks", "Streaks", ""), ("Ghosts", "Ghosts", "")],
                            default="Bloom", update=_upd_values)
    glow_strength: FloatProperty(name="Strength", default=1.0, min=0.0, soft_max=5.0, update=_upd_values)
    glow_size: FloatProperty(name="Size", default=0.6, min=0.0, max=1.0, subtype="FACTOR", update=_upd_values)
    glow_threshold: FloatProperty(name="Threshold", default=1.0, min=0.0, soft_max=10.0, update=_upd_values,
                                  description="Only pixels brighter than this glow")


# ---------------------------------------------------------------------------
# Compositor plumbing
# ---------------------------------------------------------------------------

def _scene_tree(scene, create=True):
    tree = scene.compositing_node_group
    if tree is None and create:
        tree = bpy.data.node_groups.new(f"Compositor · {scene.name}", "CompositorNodeTree")
        tree.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
        rl = tree.nodes.new("CompositorNodeRLayers")
        rl.location = (-300, 0)
        out = tree.nodes.new("NodeGroupOutput")
        out.location = (300, 0)
        tree.links.new(rl.outputs["Image"], out.inputs[0])
        scene.compositing_node_group = tree
    return tree


def _group_name(scene):
    return f"PL Optic FX · {scene.name}"


def _fx_group(scene):
    g = bpy.data.node_groups.get(_group_name(scene))
    if g is None:
        g = bpy.data.node_groups.new(_group_name(scene), "CompositorNodeTree")
        g.interface.new_socket("Image", in_out="INPUT", socket_type="NodeSocketColor")
        g.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
    return g


def ensure_fx_node(scene):
    tree = _scene_tree(scene)
    node = tree.nodes.get(FX_NODE)
    if node is None:
        outs = [n for n in tree.nodes if n.bl_idname == "NodeGroupOutput"]
        out = next((n for n in outs if n.is_active_output), outs[0] if outs else None)
        if out is None:
            out = tree.nodes.new("NodeGroupOutput")
        if not tree.interface.items_tree:
            tree.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
        node = tree.nodes.new("CompositorNodeGroup")
        node.name = node.label = FX_NODE
        node.label = "Prism Lab FX"
        node.node_tree = _fx_group(scene)
        node.location = (out.location.x, out.location.y)
        out.location.x += 250
        src = out.inputs[0].links[0].from_socket if out.inputs[0].is_linked else None
        if src is None:
            rl = next((n for n in tree.nodes if n.bl_idname == "CompositorNodeRLayers"), None) \
                or tree.nodes.new("CompositorNodeRLayers")
            src = rl.outputs["Image"]
        tree.links.new(src, node.inputs[0])
        tree.links.new(node.outputs[0], out.inputs[0])
    scene.render.use_compositing = True
    return node


def remove_fx_node(scene):
    tree = _scene_tree(scene, create=False)
    node = tree.nodes.get(FX_NODE) if tree else None
    if node is None:
        return
    src = node.inputs[0].links[0].from_socket if node.inputs[0].is_linked else None
    dests = [l.to_socket for l in node.outputs[0].links]
    tree.nodes.remove(node)
    if src:
        for d in dests:
            tree.links.new(src, d)


def _structure_key(scene):
    s = scene.prism_fx
    parts = [f"glow={s.glow}"]
    for i, r in enumerate(s.regions):
        if r.enabled:
            parts.append(f"{i}:{r.mode}:{r.shape if r.mode == 'AREA' else ''}")
    return "|".join(parts)


def _active_regions(scene):
    return [(i, r) for i, r in enumerate(scene.prism_fx.regions) if r.enabled]


def rebuild_group(scene):
    g = _fx_group(scene)
    b = Builder(g)
    gi = b.new("NodeGroupInput")
    go = b.new("NodeGroupOutput")
    img = gi.outputs[0]

    for i, r in _active_regions(scene):
        p = f"R{i}_"
        blur = b.new("CompositorNodeDBlur", name=p + "DBLUR")
        b.link(img, blur.inputs["Image"])
        if r.mode == "OBJECTS":
            crypto = b.new("CompositorNodeCryptomatteV2", name=p + "MASK")
            crypto.source = "RENDER"
            b.link(img, crypto.inputs["Image"])
            mask = crypto.outputs["Matte"]
        else:
            idn = "CompositorNodeBoxMask" if r.shape == "BOX" else "CompositorNodeEllipseMask"
            area = b.new(idn, name=p + "MASK")
            area.inputs["Mask"].default_value = 0.0
            area.inputs["Value"].default_value = 1.0
            mask = area.outputs["Mask"]
        # Optional streak of the mask itself, so the blur trails past the silhouette.
        mblur = b.new("CompositorNodeDBlur", name=p + "MDBLUR")
        b.link(mask, mblur.inputs["Image"])
        trail = b.new("ShaderNodeMix", name=p + "TRAIL", data_type="FLOAT")
        b.link(mask, trail.inputs[2])
        b.link(mblur.outputs[0], trail.inputs[3])
        soft = b.new("CompositorNodeBlur", name=p + "SOFT")
        b.link(trail.outputs[0], soft.inputs["Image"])
        amt = b.new("ShaderNodeMath", name=p + "AMT", operation="MULTIPLY", use_clamp=True)
        b.link(soft.outputs[0], amt.inputs[0])
        mix = b.new("ShaderNodeMix", name=p + "MIX", data_type="RGBA")
        b.link(amt.outputs[0], mix.inputs["Factor"])
        b.link(img, mix.inputs[6])
        b.link(blur.outputs[0], mix.inputs[7])
        img = mix.outputs[2]

    if scene.prism_fx.glow:
        glare = b.new("CompositorNodeGlare", name="GLOW")
        b.link(img, glare.inputs["Image"])
        img = glare.outputs["Image"]

    b.link(img, go.inputs[0])
    arrange(g)
    g["pl_struct"] = _structure_key(scene)


def _set(sock_or_obj, attr, value):
    """Assign only when the value changed, so handlers don't trigger endless updates."""
    cur = getattr(sock_or_obj, attr)
    try:
        same = cur == value if not hasattr(cur, "__len__") or isinstance(cur, str) else tuple(cur) == tuple(value)
    except TypeError:
        same = False
    if not same:
        setattr(sock_or_obj, attr, value)


def _animated(scene, r):
    length, direction = r.length, r.direction
    if r.animate:
        t = scene.frame_current / max(scene.render.fps / scene.render.fps_base, 1e-6)
        length *= 1.0 - r.pulse_depth * (0.5 - 0.5 * math.cos(math.tau * r.pulse_speed * t))
        direction += math.tau * r.spin_speed * t
    return length, direction


def _area_center(scene, r):
    if r.follow and scene.camera:
        co = world_to_camera_view(scene, scene.camera, r.follow.matrix_world.translation)
        return co.x, co.y
    return r.center_x, r.center_y


def sync_values(scene):
    g = bpy.data.node_groups.get(_group_name(scene))
    if g is None or g.get("pl_struct") != _structure_key(scene):
        return
    nodes = g.nodes
    for i, r in _active_regions(scene):
        p = f"R{i}_"
        length, direction = _animated(scene, r)
        for name in ("DBLUR", "MDBLUR"):
            n = nodes.get(p + name)
            if n:
                _set(n.inputs["Samples"], "default_value", r.samples)
                _set(n.inputs["Translation Amount"], "default_value", length * LENGTH_SCALE)
                _set(n.inputs["Translation Direction"], "default_value", direction)
        mask = nodes.get(p + "MASK")
        if mask and r.mode == "OBJECTS":
            ids = ",".join(ref.obj.name for ref in r.objects if ref.obj)
            _set(mask, "matte_id", ids)
        elif mask:
            cx, cy = _area_center(scene, r)
            _set(mask.inputs["Position"], "default_value", (cx, cy))
            # The mask node measures both axes in frame widths; convert height to frame heights.
            rs = scene.render
            aspect = (rs.resolution_y * rs.pixel_aspect_y) / max(rs.resolution_x * rs.pixel_aspect_x, 1)
            _set(mask.inputs["Size"], "default_value", (r.width, r.height * aspect))
            _set(mask.inputs["Rotation"], "default_value", r.area_rotation)
        trail = nodes.get(p + "TRAIL")
        if trail:
            _set(trail.inputs[0], "default_value", 1.0 if r.trail else 0.0)
        soft = nodes.get(p + "SOFT")
        if soft:
            _set(soft.inputs["Size"], "default_value", (r.softness, r.softness))
        amt = nodes.get(p + "AMT")
        if amt:
            _set(amt.inputs[1], "default_value", r.amount)
    glare = nodes.get("GLOW")
    if glare:
        s = scene.prism_fx
        if glare.inputs["Type"].default_value != s.glow_type:
            set_menu(glare.inputs["Type"], s.glow_type)
        _set(glare.inputs["Strength"], "default_value", s.glow_strength)
        _set(glare.inputs["Size"], "default_value", s.glow_size)
        _set(glare.inputs["Highlights Threshold"], "default_value", s.glow_threshold)


def refresh(scene):
    """Bring the compositor fully in line with the scene's settings."""
    s = scene.prism_fx
    wanted = s.glow or any(r.enabled for r in s.regions)
    if not wanted:
        remove_fx_node(scene)
        return
    if any(r.enabled and r.mode == "OBJECTS" for r in s.regions):
        for vl in scene.view_layers:
            vl.use_pass_cryptomatte_object = True
    ensure_fx_node(scene)
    g = _fx_group(scene)
    if g.get("pl_struct") != _structure_key(scene):
        rebuild_group(scene)
    sync_values(scene)


# ---------------------------------------------------------------------------
# Live update handlers
# ---------------------------------------------------------------------------

def _needs_live(scene):
    s = getattr(scene, "prism_fx", None)
    return s and s.live_update and any(r.enabled for r in s.regions) \
        and bpy.data.node_groups.get(_group_name(scene)) is not None


@persistent
def _on_frame(scene, *args):
    if _needs_live(scene):
        sync_values(scene)


@persistent
def _on_depsgraph(scene, depsgraph):
    # Only object moves matter here (Follow targets, camera); keep it cheap.
    if _needs_live(scene) and any(u.is_updated_transform for u in depsgraph.updates):
        sync_values(scene)


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------

class PRISM_OT_toggle_glow(bpy.types.Operator):
    bl_idname = "prism.toggle_glow"
    bl_label = "Glow"
    bl_description = "Add or remove a compositor glow so bright emissive parts bloom"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = context.scene.prism_fx
        s.glow = not s.glow  # update callback refreshes the compositor
        return {"FINISHED"}


class PRISM_OT_add_blur(bpy.types.Operator):
    bl_idname = "prism.add_blur"
    bl_label = "Add Blur Region"
    bl_description = "Add a directional blur region"
    bl_options = {"REGISTER", "UNDO"}

    mode: EnumProperty(items=[("OBJECTS", "Selected Mesh", "Blur the selected objects"),
                              ("AREA", "Area", "Blur a region of the frame")], default="OBJECTS")

    def execute(self, context):
        scene = context.scene
        s = scene.prism_fx
        sel = [o for o in context.selected_objects if o.type in {"MESH", "CURVE", "FONT", "SURFACE", "META", "CURVES"}]
        if self.mode == "OBJECTS" and not sel:
            self.report({"WARNING"}, "Select the objects to blur first")
            return {"CANCELLED"}
        r = s.regions.add()
        if self.mode == "OBJECTS":
            r.name = sel[0].name if len(sel) == 1 else f"{sel[0].name} +{len(sel) - 1}"
            for o in sel:
                r.objects.add().obj = o
        else:
            r.name = f"Area {len(s.regions)}"
        r.mode = self.mode
        s.active = len(s.regions) - 1
        refresh(scene)
        return {"FINISHED"}


class PRISM_OT_remove_blur(bpy.types.Operator):
    bl_idname = "prism.remove_blur"
    bl_label = "Remove Blur Region"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = context.scene.prism_fx
        if 0 <= s.active < len(s.regions):
            s.regions.remove(s.active)
            s.active = max(0, min(s.active, len(s.regions) - 1))
            refresh(context.scene)
        return {"FINISHED"}


class PRISM_OT_blur_set_objects(bpy.types.Operator):
    bl_idname = "prism.blur_set_objects"
    bl_label = "Use Selected"
    bl_description = "Replace this region's objects with the current selection"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = context.scene.prism_fx
        if not (0 <= s.active < len(s.regions)):
            return {"CANCELLED"}
        r = s.regions[s.active]
        r.objects.clear()
        for o in context.selected_objects:
            r.objects.add().obj = o
        sync_values(context.scene)
        return {"FINISHED"}


class PRISM_OT_fx_refresh(bpy.types.Operator):
    bl_idname = "prism.fx_refresh"
    bl_label = "Update"
    bl_description = "Rebuild and sync the Prism Lab compositor effects now"

    def execute(self, context):
        g = bpy.data.node_groups.get(_group_name(context.scene))
        if g:
            g["pl_struct"] = ""
        refresh(context.scene)
        return {"FINISHED"}


class PRISM_OT_fx_preview(bpy.types.Operator):
    bl_idname = "prism.fx_preview"
    bl_label = "Preview"
    bl_description = "Show compositor effects in the 3D viewport (rendered shading)"

    def execute(self, context):
        spaces = [sp for win in context.window_manager.windows for area in win.screen.areas
                  if area.type == "VIEW_3D" for sp in area.spaces if sp.type == "VIEW_3D"]
        turning_on = any(sp.shading.use_compositor == "DISABLED" for sp in spaces)
        for sp in spaces:
            sp.shading.use_compositor = "ALWAYS" if turning_on else "DISABLED"
            if turning_on:
                sp.shading.type = "RENDERED"
        return {"FINISHED"}


class PRISM_UL_blur_regions(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_prop, index=0):
        ic = "OBJECT_DATA" if item.mode == "OBJECTS" else ("MESH_CIRCLE" if item.shape == "ELLIPSE" else "MESH_PLANE")
        layout.prop(item, "name", text="", emboss=False, icon=ic)
        layout.prop(item, "enabled", text="", emboss=False,
                    icon="HIDE_OFF" if item.enabled else "HIDE_ON")


def draw_glow(layout, context):
    s = context.scene.prism_fx
    row = layout.row(align=True)
    row.operator("prism.toggle_glow", text="Remove Glow" if s.glow else "Add Glow",
                 icon="X" if s.glow else "LIGHT_SUN", depress=s.glow)
    if s.glow:
        col = layout.column(align=True)
        col.prop(s, "glow_type", text="")
        col.prop(s, "glow_strength"); col.prop(s, "glow_size"); col.prop(s, "glow_threshold")


def draw_blur(layout, context):
    scene = context.scene
    s = scene.prism_fx
    row = layout.row(align=True)
    row.operator("prism.add_blur", text="Selected Mesh", icon="OBJECT_DATA").mode = "OBJECTS"
    row.operator("prism.add_blur", text="Area", icon="SELECT_SET").mode = "AREA"
    row = layout.row()
    row.template_list("PRISM_UL_blur_regions", "", s, "regions", s, "active", rows=3)
    row.operator("prism.remove_blur", text="", icon="REMOVE")
    if not (0 <= s.active < len(s.regions)):
        _draw_footer(layout, s)
        return
    r = s.regions[s.active]

    box = layout.box()
    box.row().prop(r, "mode", expand=True)
    if r.mode == "OBJECTS":
        col = box.column(align=True)
        for ref in r.objects:
            col.prop(ref, "obj", text="")
        box.operator("prism.blur_set_objects", icon="RESTRICT_SELECT_OFF")
    else:
        box.row().prop(r, "shape", expand=True)
        box.prop(r, "follow")
        col = box.column(align=True)
        if not r.follow:
            row = col.row(align=True)
            row.prop(r, "center_x"); row.prop(r, "center_y")
        row = col.row(align=True)
        row.prop(r, "width"); row.prop(r, "height")
        col.prop(r, "area_rotation")

    col = layout.column(align=True)
    col.prop(r, "amount"); col.prop(r, "length"); col.prop(r, "direction")
    col.prop(r, "softness"); col.prop(r, "samples")
    layout.prop(r, "trail")

    box = layout.box()
    box.prop(r, "animate", icon="ANIM")
    if r.animate:
        col = box.column(align=True)
        col.prop(r, "pulse_speed"); col.prop(r, "pulse_depth"); col.prop(r, "spin_speed")
    _draw_footer(layout, s)


def _draw_footer(layout, s):
    row = layout.row(align=True)
    row.prop(s, "live_update", toggle=True, icon="FILE_REFRESH")
    row.operator("prism.fx_refresh", text="", icon="FILE_REFRESH")
    row.operator("prism.fx_preview", icon="HIDE_OFF")


classes = (PL_ObjectRef, PL_BlurRegion, PL_FXSettings, PRISM_OT_toggle_glow, PRISM_OT_add_blur,
           PRISM_OT_remove_blur, PRISM_OT_blur_set_objects, PRISM_OT_fx_refresh, PRISM_OT_fx_preview,
           PRISM_UL_blur_regions)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.prism_fx = PointerProperty(type=PL_FXSettings)
    bpy.app.handlers.frame_change_post.append(_on_frame)
    bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph)


def unregister():
    for lst, fn in ((bpy.app.handlers.frame_change_post, _on_frame),
                    (bpy.app.handlers.depsgraph_update_post, _on_depsgraph)):
        if fn in lst:
            lst.remove(fn)
    del bpy.types.Scene.prism_fx
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
