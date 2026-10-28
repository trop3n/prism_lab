"""Fluid Distortion: smoke-like swirls that drag the rendered image around (the Hiss effect from Control).

A grid parented to the camera fills the frame. A Geometry Nodes simulation on it
("Prism Fluid Sim") advects screen-space UV offsets and a smoke density with a
curl-noise velocity field, fed by an emitter empty. The sim object stays in the
normal view layers (so playback simulates and caches it) but never renders.
A second object ("Prism Fluid Field") copies its result into a separate view
layer ("Prism Fluid"), where it writes the offsets to two AOVs; a compositor node
group then warps the scene's render with Map UV, once per color channel for
dispersion. Same algorithm as the FluidPrism shader in prism-ffgl.
"""

import math
import bpy
from bpy.app.handlers import persistent
from bpy.props import FloatProperty, PointerProperty

from .fx import _scene_tree
from .nodes import Builder, arrange, draw_input, get_input, modifier_inputs, set_input, socket, versioned_group

GROUP_NAME = "PL Fluid Sim"
DISPLAY_GROUP = "PL Fluid Field"
GROUP_VERSION = 1
MOD_NAME = "Prism Fluid"
SIM_COLLECTION = "Prism Fluid Sim"  # normal view layers only
COLLECTION = "Prism Fluid"          # the "Prism Fluid" view layer only
VIEW_LAYER = "Prism Fluid"
AOV_A = "pl_fluid_a"  # rgb = distorted UV (x, y), smoke density
AOV_B = "pl_fluid_b"  # rgb = UV offset (x, y), 0
COMP_NODE = "PL_FLUID"


# ---------------------------------------------------------------------------
# Geometry Nodes simulation
# ---------------------------------------------------------------------------

def field_group():
    g, needs_build = versioned_group(GROUP_NAME, GROUP_VERSION, "GeometryNodeTree")
    if not needs_build:
        return g
    g.is_modifier = True

    socket(g, "Geometry", "INPUT", "NodeSocketGeometry")
    socket(g, "Emitter", "INPUT", "NodeSocketObject", desc="Empty the smoke comes from")
    socket(g, "Emit", "INPUT", "NodeSocketFloat", 0.6, 0.0, 1.0, desc="How much smoke the emitter puts out")
    socket(g, "Radius", "INPUT", "NodeSocketFloat", 0.08, 0.0, 0.5, desc="Emitter size, in frame heights")
    socket(g, "Direction", "INPUT", "NodeSocketFloat", math.pi / 2, subtype="ANGLE", desc="Which way the smoke rises (90° = up)")
    socket(g, "Buoyancy", "INPUT", "NodeSocketFloat", 0.25, 0.0, desc="How fast the smoke rises (frame heights per second)")
    socket(g, "Turbulence", "INPUT", "NodeSocketFloat", 0.6, 0.0, desc="Strength of the swirls")
    socket(g, "Scale", "INPUT", "NodeSocketFloat", 4.0, 0.1, desc="Swirls per frame height")
    socket(g, "Speed", "INPUT", "NodeSocketFloat", 1.0, 0.0)
    socket(g, "Strength", "INPUT", "NodeSocketFloat", 1.0, 0.0, desc="How far the smoke drags the picture")
    socket(g, "Fade", "INPUT", "NodeSocketFloat", 0.6, 0.0, desc="How fast the smoke thins out")
    socket(g, "Recover", "INPUT", "NodeSocketFloat", 0.3, 0.0, desc="How fast the picture settles back")
    socket(g, "Resolution", "INPUT", "NodeSocketInt", 128, 16, 512, desc="Simulation grid rows (columns follow the frame aspect)")
    # Set by the add-on from the camera (see fit_to_camera); not shown in the panel.
    socket(g, "Size X", "INPUT", "NodeSocketFloat", 1.0, 0.0)
    socket(g, "Size Y", "INPUT", "NodeSocketFloat", 1.0, 0.0)
    socket(g, "Distance", "INPUT", "NodeSocketFloat", 1.0, 0.0)
    socket(g, "Geometry", "OUTPUT", "NodeSocketGeometry")

    b = Builder(g)
    gi = b.new("NodeGroupInput").outputs
    go = b.new("NodeGroupOutput")

    def named(name, vector=True):
        return b.g("GeometryNodeInputNamedAttribute", {"Name": name},
                   data_type="FLOAT_VECTOR" if vector else "FLOAT").outputs["Attribute"]

    def store(geo, name, value, vector=True, domain="POINT"):
        return b.g("GeometryNodeStoreNamedAttribute", {"Geometry": geo, "Name": name, "Value": value},
                   data_type="FLOAT_VECTOR" if vector else "FLOAT", domain=domain).outputs[0]

    def smoothstep(x, lo, hi):
        return b.g("ShaderNodeMapRange", {"Value": x, "From Min": lo, "From Max": hi},
                   interpolation_type="SMOOTHSTEP", clamp=True).outputs["Result"]

    # --- grid filling the frame; pl_uv on corners for Sample UV Surface ---
    aspect = b.div(gi["Size X"], b.math("MAXIMUM", gi["Size Y"], 1e-6))
    cols = b.math("ROUND", b.mul(gi["Resolution"], aspect))
    grid = b.g("GeometryNodeMeshGrid", {"Size X": gi["Size X"], "Size Y": gi["Size Y"],
                                        "Vertices X": cols, "Vertices Y": gi["Resolution"]})
    geo = store(grid.outputs["Mesh"], "pl_uv", grid.outputs["UV Map"], domain="CORNER")
    geo = store(geo, "pl_off", (0.0, 0.0, 0.0))
    geo = store(geo, "pl_dens", 0.0, vector=False)

    sim_in = b.new("GeometryNodeSimulationInput")
    sim_out = b.new("GeometryNodeSimulationOutput")
    sim_in.pair_with_output(sim_out)
    b.link(geo, sim_in.inputs["Geometry"])
    prev = sim_in.outputs["Geometry"]
    dt = b.mul(sim_in.outputs["Delta Time"], gi["Speed"])

    # Frame coordinates: p in frame heights (centered), uv in 0..1.
    pos = b.new("GeometryNodeInputPosition").outputs[0]
    px, py, _ = b.xyz(pos)
    p = b.vmath("SCALE", pos, scale=b.div(1.0, b.math("MAXIMUM", gi["Size Y"], 1e-6)))
    uv = b.vec(b.add(b.div(px, gi["Size X"]), 0.5), b.add(b.div(py, gi["Size Y"]), 0.5), 0.0)

    # Velocity: curl of a drifting noise potential, plus buoyancy along Direction.
    t = b.mul(b.new("GeometryNodeInputSceneTime").outputs["Seconds"], gi["Speed"])
    q = b.vmath("ADD", b.vmath("SCALE", p, scale=gi["Scale"]), b.vec(0.0, b.mul(t, -0.35), 0.0))
    e = 0.05

    def noise(dx, dy):
        n = b.g("ShaderNodeTexNoise", {"Vector": b.vmath("ADD", q, (dx, dy, 0.0)), "W": b.mul(t, 0.1),
                                       "Scale": 1.0, "Detail": 2.0}, noise_dimensions="4D")
        return n.outputs["Factor"]

    curl_k = b.div(gi["Turbulence"], b.mul(2.0 * e, gi["Scale"]))
    vx = b.mul(b.sub(noise(0.0, e), noise(0.0, -e)), curl_k)
    vy = b.mul(b.sub(noise(-e, 0.0), noise(e, 0.0)), curl_k)
    vx = b.madd(b.math("COSINE", gi["Direction"]), gi["Buoyancy"], vx)
    vy = b.madd(b.math("SINE", gi["Direction"]), gi["Buoyancy"], vy)
    vel_uv = b.vec(b.div(vx, aspect), vy, 0.0)

    # Semi-Lagrangian step: fetch what flowed into this point from the previous frame.
    back = b.vmath("SUBTRACT", uv, b.vmath("SCALE", vel_uv, scale=dt))
    s_off = b.g("GeometryNodeSampleUVSurface", {"Mesh": prev, "Value": named("pl_off"),
                                                "UV Map": named("pl_uv"), "Sample UV": back},
                data_type="FLOAT_VECTOR")
    s_dens = b.g("GeometryNodeSampleUVSurface", {"Mesh": prev, "Value": named("pl_dens", False),
                                                 "UV Map": named("pl_uv"), "Sample UV": back},
                 data_type="FLOAT")
    # Outside the frame there is nothing to fetch: fresh, undistorted picture.
    old_off = b.vmath("SCALE", s_off.outputs["Value"], scale=s_off.outputs["Is Valid"])
    old_dens = b.mul(s_dens.outputs["Value"], s_dens.outputs["Is Valid"])

    # Emitter: project the empty through the camera onto the grid plane.
    info = b.g("GeometryNodeObjectInfo", {"Object": gi["Emitter"]}, transform_space="RELATIVE")
    ex, ey, ez = b.xyz(info.outputs["Location"])
    depth = b.sub(gi["Distance"], ez)  # distance in front of the camera
    proj = b.div(gi["Distance"], b.math("MAXIMUM", depth, 1e-4))
    in_front = b.math("GREATER_THAN", depth, 1e-4)
    emit_p = b.vec(b.div(b.mul(ex, proj), gi["Size Y"]), b.div(b.mul(ey, proj), gi["Size Y"]), 0.0)
    dist = b.vmath("DISTANCE", p, emit_p)
    src = b.sub(1.0, smoothstep(dist, b.mul(gi["Radius"], 0.5), gi["Radius"]))
    src = b.mul(b.mul(src, in_front), b.mul(gi["Emit"], 6.0))

    dens = b.madd(b.math("MAXIMUM", b.sub(1.0, b.mul(gi["Fade"], dt)), 0.0), old_dens, b.mul(src, dt))
    dens = b.math("MINIMUM", b.math("MAXIMUM", dens, 0.0), 1.0)

    # Drag the picture only where there is smoke, then let it settle back.
    push = b.mul(b.mul(dt, smoothstep(old_dens, 0.0, 0.25)), gi["Strength"])
    off = b.vmath("SUBTRACT", old_off, b.vmath("SCALE", vel_uv, scale=push))
    off = b.vmath("SCALE", off, scale=b.math("MAXIMUM", b.sub(1.0, b.mul(gi["Recover"], dt)), 0.0))
    length = b.vmath("LENGTH", off)
    off = b.vmath("SCALE", off, scale=b.math("MINIMUM", 1.0, b.div(0.5, b.math("MAXIMUM", length, 1e-6))))

    nxt = store(prev, "pl_dens", dens, vector=False)
    nxt = store(nxt, "pl_off", off)
    b.link(nxt, sim_out.inputs["Geometry"])

    # Outputs for the AOVs (read by the field material).
    done = sim_out.outputs["Geometry"]
    cur_off = named("pl_off")
    ox, oy, _ = b.xyz(cur_off)
    ux, uy, _ = b.xyz(uv)
    done = store(done, AOV_A, b.vec(b.add(ux, ox), b.add(uy, oy), named("pl_dens", False)))
    done = store(done, AOV_B, cur_off)
    b.link(done, go.inputs[0])
    arrange(g)
    return g


def display_group():
    """Copy the sim object's result (with its attributes) and give it the AOV material."""
    g, needs_build = versioned_group(DISPLAY_GROUP, GROUP_VERSION, "GeometryNodeTree")
    if not needs_build:
        return g
    g.is_modifier = True
    socket(g, "Geometry", "INPUT", "NodeSocketGeometry")
    socket(g, "Sim", "INPUT", "NodeSocketObject")
    socket(g, "Material", "INPUT", "NodeSocketMaterial")
    socket(g, "Geometry", "OUTPUT", "NodeSocketGeometry")
    b = Builder(g)
    gi = b.new("NodeGroupInput").outputs
    go = b.new("NodeGroupOutput")
    info = b.g("GeometryNodeObjectInfo", {"Object": gi["Sim"]}, transform_space="RELATIVE")
    mat = b.g("GeometryNodeSetMaterial", {"Geometry": info.outputs["Geometry"], "Material": gi["Material"]})
    b.link(mat.outputs[0], go.inputs[0])
    arrange(g)
    return g


def field_material():
    mat = bpy.data.materials.get("PL Fluid Field")
    if mat is not None:
        return mat
    mat = bpy.data.materials.new("PL Fluid Field")
    if mat.node_tree is None:
        mat.use_nodes = True
    b = Builder(mat.node_tree)
    out = b.new("ShaderNodeOutputMaterial")
    em = b.g("ShaderNodeEmission", {"Color": (0.0, 0.0, 0.0, 1.0)})
    b.link(em.outputs[0], out.inputs["Surface"])
    for name in (AOV_A, AOV_B):
        attr = b.new("ShaderNodeAttribute", attribute_type="GEOMETRY", attribute_name=name)
        aov = b.new("ShaderNodeOutputAOV", aov_name=name)
        b.link(attr.outputs["Color"], aov.inputs["Color"])
    arrange(mat.node_tree)
    return mat


# ---------------------------------------------------------------------------
# Camera fit, view layers
# ---------------------------------------------------------------------------

def fit_to_camera(scene):
    """Size the grid to the camera frame and park it just past the near clip."""
    s = scene.prism_fluid
    field, cam = s.field, scene.camera
    mod = field.modifiers.get(MOD_NAME) if field else None
    if not (mod and cam and cam.type == "CAMERA"):
        return
    if field.parent != cam:
        field.parent = cam
        field.matrix_parent_inverse.identity()
    d = min(max(cam.data.clip_start * 2.0, 0.01), cam.data.clip_end * 0.5)
    corners = cam.data.view_frame(scene=scene)
    z = -corners[0].z
    w = (max(c.x for c in corners) - min(c.x for c in corners)) * d / z
    h = (max(c.y for c in corners) - min(c.y for c in corners)) * d / z
    keys = modifier_inputs(mod)
    changed = False
    for name, val in (("Size X", w), ("Size Y", h), ("Distance", d)):
        if abs(get_input(mod, keys[name]) - val) > 1e-6:
            set_input(mod, keys[name], val)
            changed = True
    if tuple(field.location) != (0.0, 0.0, -d):
        field.location = (0.0, 0.0, -d)
        changed = True
    if changed:
        field.update_tag()


def _collection(scene, name):
    coll = bpy.data.collections.get(name) or bpy.data.collections.new(name)
    if coll.name not in scene.collection.children:
        scene.collection.children.link(coll)
    return coll


def _setup_view_layers(scene, coll, sim_coll):
    vl = scene.view_layers.get(VIEW_LAYER) or scene.view_layers.new(VIEW_LAYER)
    vl.use_pass_combined = True
    vl.samples = 8
    for name in (AOV_A, AOV_B):
        if name not in vl.aovs:
            aov = vl.aovs.add()
            aov.name = name
            aov.type = "COLOR"
    for other in scene.view_layers:
        for lc in other.layer_collection.children:
            if other == vl:
                lc.exclude = lc.collection != coll  # only the field renders here (faster)
            elif lc.collection == coll:
                lc.exclude = True                   # never seen in the real render
            elif lc.collection == sim_coll:
                lc.exclude = False                  # evaluated (and cached) during playback


# ---------------------------------------------------------------------------
# Compositor
# ---------------------------------------------------------------------------

def _comp_group(scene):
    name = f"PL Fluid · {scene.name}"
    g = bpy.data.node_groups.get(name)
    if g is not None:
        return g
    g = bpy.data.node_groups.new(name, "CompositorNodeTree")
    g.interface.new_socket("Image", in_out="INPUT", socket_type="NodeSocketColor")
    g.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
    b = Builder(g)
    gi = b.new("NodeGroupInput")
    go = b.new("NodeGroupOutput")
    img = gi.outputs[0]
    rl = b.new("CompositorNodeRLayers", scene=scene, layer=VIEW_LAYER)
    a, off = rl.outputs[AOV_A], rl.outputs[AOV_B]
    ax, ay, dens = b.xyz(a)
    # Map UV multiplies its result by the UV's Z (the alpha of the old UV pass), so Z must be 1.
    uv = b.vec(ax, ay, 1.0)

    # Distorted UVs per channel: red bends less, blue more (the offset's Z is 0).
    k = b.new("ShaderNodeMath", name="K", operation="MULTIPLY")
    k.inputs[1].default_value = 0.5
    uv_r = b.vmath("SUBTRACT", uv, b.vmath("SCALE", off, scale=k.outputs[0]))
    uv_b = b.vmath("ADD", uv, b.vmath("SCALE", off, scale=k.outputs[0]))
    chans = []
    for ch_uv, ch in ((uv_r, "Red"), (uv, "Green"), (uv_b, "Blue")):
        m = b.g("CompositorNodeMapUV", {"Image": img, "UV": ch_uv})
        sep = b.g("CompositorNodeSeparateColor", {"Image": m.outputs[0]})
        chans.append(sep.outputs[ch])
    alpha = b.g("CompositorNodeSeparateColor", {"Image": img}).outputs["Alpha"]
    col = b.g("CompositorNodeCombineColor", {"Red": chans[0], "Green": chans[1], "Blue": chans[2], "Alpha": alpha})

    # Haze: a pale veil where the smoke is.
    haze = b.new("ShaderNodeMath", name="HAZE", operation="MULTIPLY", use_clamp=True)
    b.link(dens, haze.inputs[0])
    hz = b.new("ShaderNodeMix", data_type="RGBA")
    b.link(haze.outputs[0], hz.inputs["Factor"])
    b.link(col.outputs[0], hz.inputs[6])
    hz.inputs[7].default_value = (1.0, 1.0, 1.0, 1.0)

    # Mix with the original. Without field data (e.g. the viewport compositor, which only
    # sees the active view layer) the AOV is all zero, so the effect switches itself off.
    has_data = b.math("MULTIPLY", b.add(ax, ay), 1e4, clamp=True)
    fac = b.new("ShaderNodeMath", name="MIX", operation="MULTIPLY", use_clamp=True)
    b.link(has_data, fac.inputs[0])
    mix = b.new("ShaderNodeMix", data_type="RGBA")
    b.link(fac.outputs[0], mix.inputs["Factor"])
    b.link(img, mix.inputs[6])
    b.link(hz.outputs[2], mix.inputs[7])
    b.link(mix.outputs[2], go.inputs[0])
    arrange(g)
    return g


def sync_comp(scene):
    g = bpy.data.node_groups.get(f"PL Fluid · {scene.name}")
    if g is None:
        return
    s = scene.prism_fluid
    g.nodes["K"].inputs[0].default_value = s.dispersion
    g.nodes["HAZE"].inputs[1].default_value = s.haze
    g.nodes["MIX"].inputs[1].default_value = s.mix


def _ensure_comp_node(scene):
    tree = _scene_tree(scene)
    node = tree.nodes.get(COMP_NODE)
    if node is None:
        outs = [n for n in tree.nodes if n.bl_idname == "NodeGroupOutput"]
        out = next((n for n in outs if n.is_active_output), outs[0] if outs else None) \
            or tree.nodes.new("NodeGroupOutput")
        node = tree.nodes.new("CompositorNodeGroup")
        node.name = COMP_NODE
        node.label = "Prism Lab Fluid"
        node.node_tree = _comp_group(scene)
        node.location = (out.location.x, out.location.y - 200)
        out.location.x += 250
        src = out.inputs[0].links[0].from_socket if out.inputs[0].is_linked else None
        if src is None:
            rl = next((n for n in tree.nodes if n.bl_idname == "CompositorNodeRLayers" and n.layer != VIEW_LAYER),
                      None) or tree.nodes.new("CompositorNodeRLayers")
            src = rl.outputs["Image"]
        tree.links.new(src, node.inputs[0])
        tree.links.new(node.outputs[0], out.inputs[0])
    scene.render.use_compositing = True
    sync_comp(scene)


def _remove_comp_node(scene):
    tree = _scene_tree(scene, create=False)
    node = tree.nodes.get(COMP_NODE) if tree else None
    if node is None:
        return
    src = node.inputs[0].links[0].from_socket if node.inputs[0].is_linked else None
    dests = [l.to_socket for l in node.outputs[0].links]
    tree.nodes.remove(node)
    if src:
        for d in dests:
            tree.links.new(src, d)


# ---------------------------------------------------------------------------
# Settings, handlers, operators
# ---------------------------------------------------------------------------

def _upd_comp(self, context):
    if isinstance(self.id_data, bpy.types.Scene):
        sync_comp(self.id_data)


class PL_FluidSettings(bpy.types.PropertyGroup):
    field: PointerProperty(type=bpy.types.Object)    # the sim object
    display: PointerProperty(type=bpy.types.Object)  # its copy in the "Prism Fluid" view layer
    emitter: PointerProperty(type=bpy.types.Object)
    dispersion: FloatProperty(name="Dispersion", default=0.35, min=0.0, max=1.0, subtype="FACTOR", update=_upd_comp,
                              description="Rainbow fringes (red, green and blue dragged by different amounts)")
    haze: FloatProperty(name="Haze", default=0.15, min=0.0, max=1.0, subtype="FACTOR", update=_upd_comp,
                        description="Pale veil where the smoke is")
    mix: FloatProperty(name="Mix", default=1.0, min=0.0, max=1.0, subtype="FACTOR", update=_upd_comp)


@persistent
def _on_depsgraph(scene, depsgraph):
    # Keep the grid matched to the camera (lens, sensor, resolution or camera changes).
    s = getattr(scene, "prism_fluid", None)
    if s and s.field:
        fit_to_camera(scene)


class PRISM_OT_add_fluid(bpy.types.Operator):
    bl_idname = "prism.add_fluid"
    bl_label = "Add Fluid Distortion"
    bl_description = ("Add a smoke-like distortion that swirls the render out of an emitter "
                      "(at the active object, or the 3D cursor)")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.scene.camera is not None and not context.scene.prism_fluid.field

    def execute(self, context):
        scene = context.scene
        s = scene.prism_fluid
        coll = _collection(scene, COLLECTION)
        sim_coll = _collection(scene, SIM_COLLECTION)

        target = context.active_object if context.active_object and context.active_object.select_get() else None
        emitter = bpy.data.objects.new("Fluid Emitter", None)
        emitter.empty_display_type = "SPHERE"
        emitter.empty_display_size = 0.15
        context.collection.objects.link(emitter)
        if target:
            emitter.parent = target
        else:
            emitter.location = scene.cursor.location

        field = bpy.data.objects.new("Prism Fluid Sim", bpy.data.meshes.new("Prism Fluid Sim"))
        sim_coll.objects.link(field)
        field.hide_select = True
        field.hide_render = True
        field.display_type = "BOUNDS"  # just the frame outline in the viewport
        field.visible_camera = False
        mod = field.modifiers.new(MOD_NAME, "NODES")
        mod.node_group = field_group()
        set_input(mod, modifier_inputs(mod)["Emitter"], emitter)

        display = bpy.data.objects.new("Prism Fluid Field", bpy.data.meshes.new("Prism Fluid Field"))
        coll.objects.link(display)
        display.hide_select = True
        display.parent = field
        dmod = display.modifiers.new(MOD_NAME, "NODES")
        dmod.node_group = display_group()
        keys = modifier_inputs(dmod)
        set_input(dmod, keys["Sim"], field)
        set_input(dmod, keys["Material"], field_material())

        s.field, s.display, s.emitter = field, display, emitter
        fit_to_camera(scene)
        _setup_view_layers(scene, coll, sim_coll)
        _ensure_comp_node(scene)
        return {"FINISHED"}


class PRISM_OT_remove_fluid(bpy.types.Operator):
    bl_idname = "prism.remove_fluid"
    bl_label = "Remove Fluid Distortion"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        s = scene.prism_fluid
        _remove_comp_node(scene)
        g = bpy.data.node_groups.get(f"PL Fluid · {scene.name}")
        if g:
            bpy.data.node_groups.remove(g)
        vl = scene.view_layers.get(VIEW_LAYER)
        if vl and len(scene.view_layers) > 1:
            scene.view_layers.remove(vl)
        for ob in (s.display, s.field, s.emitter):
            if ob:
                bpy.data.objects.remove(ob)
        for name in (COLLECTION, SIM_COLLECTION):
            coll = bpy.data.collections.get(name)
            if coll and not coll.objects:
                bpy.data.collections.remove(coll)
        return {"FINISHED"}


class PRISM_OT_select_fluid_emitter(bpy.types.Operator):
    bl_idname = "prism.select_fluid_emitter"
    bl_label = "Select Emitter"
    bl_description = "Select the emitter so you can move it"

    def execute(self, context):
        em = context.scene.prism_fluid.emitter
        if em:
            for o in context.selected_objects:
                o.select_set(False)
            em.select_set(True)
            context.view_layer.objects.active = em
        return {"FINISHED"}


def draw(layout, context):
    scene = context.scene
    s = scene.prism_fluid
    mod = s.field.modifiers.get(MOD_NAME) if s.field else None
    if not mod:
        layout.operator("prism.add_fluid", icon="MOD_FLUIDSIM")
        if scene.camera is None:
            layout.label(text="Needs a scene camera", icon="ERROR")
        return
    keys = modifier_inputs(mod)
    layout.operator("prism.select_fluid_emitter", icon="EMPTY_AXIS")

    col = layout.column(align=True)
    for n in ("Emit", "Radius", "Direction", "Buoyancy", "Turbulence", "Scale", "Speed",
              "Strength", "Fade", "Recover", "Resolution"):
        draw_input(col, mod, keys[n], n)
    col = layout.column(align=True)
    col.prop(s, "dispersion"); col.prop(s, "haze"); col.prop(s, "mix")

    col = layout.box().column(align=True)
    col.label(text="Builds up over time:", icon="INFO")
    col.label(text="play from the start frame.")
    col.label(text="Renders only (F12), not")
    col.label(text="the viewport compositor.")
    layout.operator("prism.remove_fluid", icon="X")


classes = (PL_FluidSettings, PRISM_OT_add_fluid, PRISM_OT_remove_fluid, PRISM_OT_select_fluid_emitter)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.prism_fluid = PointerProperty(type=PL_FluidSettings)
    bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph)


def unregister():
    if _on_depsgraph in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph)
    del bpy.types.Scene.prism_fluid
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
