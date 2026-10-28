"""Data Points: FUI-style technical overlays generated around a target object."""

import math
import bpy
from bpy.props import EnumProperty, FloatProperty, FloatVectorProperty, PointerProperty

from .nodes import Builder, arrange, set_menu, draw_input, get_input, modifier_inputs, set_input, socket

GROUP_NAME = "PL Data Points"
GROUP_VERSION = 1
MOD_NAME = "Prism Data Points"
TAU = math.tau
UP = (0.0, 0.0, 1.0)

# name, accent color?, default on?, extra params: (name, socket type, default, min, max)
LAYERS = [
    ("Surface Data", False, True, [("Density", "NodeSocketFloat", 30.0, 0.0, None), ("Mark Size", "NodeSocketFloat", 1.0, 0.0, None)]),
    ("Labels", True, True, [("Count", "NodeSocketInt", 4, 0, 32), ("Offset", "NodeSocketFloat", 0.35, 0.0, None)]),
    ("Anchor Motifs", True, False, [("Count", "NodeSocketInt", 10, 0, 500), ("Size", "NodeSocketFloat", 1.0, 0.0, None)]),
    ("Surface Links", False, False, [("Count", "NodeSocketInt", 30, 2, 2000), ("Lines", "NodeSocketInt", 3, 1, 50)]),
    ("Circle Chain", False, False, [("Count", "NodeSocketInt", 28, 3, 500), ("Radius", "NodeSocketFloat", 1.25, 0.0, None), ("Height", "NodeSocketFloat", -0.9, -1.0, 1.0)]),
    ("Fracture Grid", False, False, [("Detail", "NodeSocketInt", 0, 0, 4), ("Break", "NodeSocketFloat", 0.35, 0.0, 1.0)]),
    ("Scan Brackets", True, True, [("Length", "NodeSocketFloat", 0.18, 0.0, 0.5), ("Gap", "NodeSocketFloat", 0.08, 0.0, None)]),
    ("Measure Arcs", False, False, [("Radius", "NodeSocketFloat", 1.4, 0.0, None), ("Sweep", "NodeSocketFloat", 4.2, 0.0, TAU), ("Ticks", "NodeSocketInt", 48, 0, 500)]),
    ("Node Network", False, False, [("Count", "NodeSocketInt", 18, 4, 500), ("Spread", "NodeSocketFloat", 1.5, 0.0, None)]),
    ("Object Lock Frame", True, False, [("Size", "NodeSocketFloat", 0.75, 0.0, None)]),
    ("Bio Scanner", False, False, [("Band", "NodeSocketFloat", 0.03, 0.0, 1.0), ("Density", "NodeSocketFloat", 1500.0, 0.0, None)]),
]
LAYER_NAMES = [l[0] for l in LAYERS]

PRESETS = {
    "MINIMAL": ("Minimal", {"Scan Brackets", "Labels", "Anchor Motifs"}),
    "TACTICAL": ("Tactical", {"Object Lock Frame", "Scan Brackets", "Measure Arcs", "Labels"}),
    "BIO_SCAN": ("Bio Scan", {"Bio Scanner", "Surface Data", "Fracture Grid", "Labels"}),
    "NETWORK": ("Network", {"Node Network", "Surface Links", "Anchor Motifs", "Circle Chain"}),
    "FULL": ("Full Stack", set(LAYER_NAMES)),
}
PRESET_ITEMS = [(k, v[0], "") for k, v in PRESETS.items()]


# ---------------------------------------------------------------------------
# Node group
# ---------------------------------------------------------------------------

class Ctx:
    """Shared sockets every layer builder needs."""


def _face_cam(b, c, at):
    """Rotation whose Z axis points from `at` toward the active camera, with Y kept up."""
    to_cam = b.vmath("SUBTRACT", c.cam, at)
    r = b.g("FunctionNodeAxesToRotation", {"Primary Axis": to_cam, "Secondary Axis": UP},
            primary_axis="Z", secondary_axis="Y")
    return r.outputs[0]


def _tube(b, c, curve):
    real = b.g("GeometryNodeRealizeInstances", {"Geometry": curve})
    return b.g("GeometryNodeCurveToMesh", {"Curve": real.outputs[0], "Profile Curve": c.profile}).outputs[0]


def _join(b, *geos):
    j = b.new("GeometryNodeJoinGeometry")
    for geo in reversed(geos):  # multi-input sockets insert at the top
        b.link(geo, j.inputs[0])
    return j.outputs[0]


def _first_n_points(b, c, n, density, seed_offset=0):
    """Scatter points on the target and keep the first n (stable count)."""
    dist = b.g("GeometryNodeDistributePointsOnFaces", {"Mesh": c.target, "Density": b.div(density, c.r2),
                                                      "Seed": b.math("ADD", c.seed, seed_offset)})
    idx = b.new("GeometryNodeInputIndex").outputs[0]
    keep = b.g("FunctionNodeCompare", {"A": idx, "B": n}, data_type="INT", operation="GREATER_EQUAL")
    dele = b.g("GeometryNodeDeleteGeometry", {"Geometry": dist.outputs["Points"], "Selection": keep.outputs[0]})
    return dele.outputs[0], dist.outputs["Normal"]


def _text(b, c, string, size, align="LEFT"):
    st = b.g("GeometryNodeStringToCurves", {"String": string, "Size": size})
    set_menu(st.inputs["Align X"], align)
    set_menu(st.inputs["Align Y"], "Bottom Baseline")
    real = b.g("GeometryNodeRealizeInstances", {"Geometry": st.outputs[0]})
    return b.g("GeometryNodeFillCurve", {"Curve": real.outputs[0]}).outputs[0]


def _strjoin(b, *parts):
    j = b.new("GeometryNodeStringJoin")
    for p in reversed(parts):
        if isinstance(p, str):
            p = b.g("FunctionNodeInputString", string=p).outputs[0]
        b.link(p, j.inputs["Strings"])
    return j.outputs[0]


def _num(b, value, decimals):
    return b.g("FunctionNodeValueToString", {"Value": value, "Decimals": decimals}).outputs[0]


def _line(b, a, z):
    return b.g("GeometryNodeCurvePrimitiveLine", {"Start": a, "End": z}).outputs[0]


# -- layers -------------------------------------------------------------------

def layer_surface_data(b, c, p):
    pts, _ = _first_n_points(b, c, 100000, p["Density"], 1)
    s = b.mul(b.mul(p["Mark Size"], c.r), 0.03)
    ns = b.mul(s, -1.0)
    cross = _join(b, _line(b, b.vec(ns, 0, 0), b.vec(s, 0, 0)), _line(b, b.vec(0, ns, 0), b.vec(0, s, 0)))
    normal = b.new("GeometryNodeInputNormal").outputs[0]
    lift = b.g("GeometryNodeSetPosition", {"Geometry": pts, "Offset": b.vmath("SCALE", normal, scale=b.mul(c.r, 0.01))})
    rot = b.g("FunctionNodeAlignRotationToVector", {"Vector": normal}, axis="Z")
    inst = b.g("GeometryNodeInstanceOnPoints", {"Points": lift.outputs[0], "Instance": cross,
                                                "Rotation": rot.outputs[0]})
    return _tube(b, c, inst.outputs[0])


def layer_labels(b, c, p):
    pts, nrm = _first_n_points(b, c, p["Count"], 400.0, 2)
    zi = b.new("GeometryNodeForeachGeometryElementInput")
    zo = b.new("GeometryNodeForeachGeometryElementOutput", domain="POINT")
    zi.pair_with_output(zo)
    zo.input_items.new("VECTOR", "Anchor")
    zo.input_items.new("VECTOR", "Normal")
    b.set(zi.inputs[0], pts)
    b.set(zi.inputs["Anchor"], b.new("GeometryNodeInputPosition").outputs[0])
    b.set(zi.inputs["Normal"], nrm)
    anchor, n, index = zi.outputs["Anchor"], zi.outputs["Normal"], zi.outputs["Index"]

    elbow = b.vmath("ADD", anchor, b.vmath("SCALE", n, scale=b.mul(p["Offset"], c.r)))
    to_cam = b.vmath("NORMALIZE", b.vmath("SUBTRACT", c.cam, elbow))
    right = b.vmath("NORMALIZE", b.vmath("CROSS_PRODUCT", UP, to_cam))
    side = b.math("SIGN", b.vmath("DOT_PRODUCT", n, right))
    run = b.mul(c.r, 0.35)
    end = b.vmath("ADD", elbow, b.vmath("SCALE", right, scale=b.mul(run, side)))
    leader = _join(b, _line(b, anchor, elbow), _line(b, elbow, end))
    dot = b.g("GeometryNodeCurvePrimitiveCircle", {"Radius": b.mul(c.r, 0.012), "Resolution": 12})
    face = _face_cam(b, c, anchor)
    dot_t = b.g("GeometryNodeTransform", {"Geometry": dot.outputs[0], "Translation": anchor, "Rotation": face})
    lines = _tube(b, c, _join(b, leader, dot_t.outputs[0]))

    txt = _strjoin(b, "PT-", _num(b, b.add(index, 1), 0), "  ", _num(b, b.vmath("LENGTH", anchor), 3))
    size = b.mul(b.mul(c.text_size, c.r), 0.07)
    left = _text(b, c, txt, size, "LEFT")
    rgt = _text(b, c, txt, size, "RIGHT")
    pick = b.g("GeometryNodeSwitch", {"Switch": b.g("FunctionNodeCompare", {"A": side, "B": 0.0},
                                                         operation="GREATER_THAN").outputs[0],
                                         "False": rgt, "True": left}, input_type="GEOMETRY")
    lift = b.vmath("ADD", end, b.vmath("SCALE", UP, scale=b.mul(c.r, 0.02)))
    txt_t = b.g("GeometryNodeTransform", {"Geometry": pick.outputs[0], "Translation": lift,
                                          "Rotation": _face_cam(b, c, end)})
    b.link(_join(b, lines, txt_t.outputs[0]), zo.inputs[1])
    return zo.outputs[2]


def layer_anchor_motifs(b, c, p):
    pts, nrm = _first_n_points(b, c, p["Count"], 400.0, 3)
    s = b.mul(b.mul(p["Size"], c.r), 0.06)
    quad = b.g("GeometryNodeCurvePrimitiveQuadrilateral", {"Width": s, "Height": s})
    diamond = b.g("GeometryNodeTransform", {"Geometry": quad.outputs[0],
                                            "Rotation": b.g("FunctionNodeEulerToRotation", {"Euler": (0, 0, math.pi / 4)}).outputs[0]})
    ring = b.g("GeometryNodeCurvePrimitiveCircle", {"Radius": b.mul(s, 0.18), "Resolution": 10})
    motif = _join(b, diamond.outputs[0], ring.outputs[0])
    pos = b.new("GeometryNodeInputPosition").outputs[0]
    lift = b.g("GeometryNodeSetPosition", {"Geometry": pts, "Offset": b.vmath("SCALE", nrm, scale=b.mul(c.r, 0.02))})
    inst = b.g("GeometryNodeInstanceOnPoints", {"Points": lift.outputs[0], "Instance": motif,
                                                "Rotation": _face_cam(b, c, pos)})
    return _tube(b, c, inst.outputs[0])


def layer_surface_links(b, c, p):
    pts, nrm = _first_n_points(b, c, p["Count"], 400.0, 4)
    lift = b.g("GeometryNodeSetPosition", {"Geometry": pts, "Offset": b.vmath("SCALE", nrm, scale=b.mul(c.r, 0.01))})
    idx = b.new("GeometryNodeInputIndex").outputs[0]
    group = b.math("FLOORED_MODULO", idx, p["Lines"])
    curves = b.g("GeometryNodePointsToCurves", {"Points": lift.outputs[0], "Curve Group ID": group, "Weight": idx})
    nodes = b.g("GeometryNodeCurvePrimitiveCircle", {"Radius": b.mul(c.r, 0.01), "Resolution": 8})
    pos = b.new("GeometryNodeInputPosition").outputs[0]
    dots = b.g("GeometryNodeInstanceOnPoints", {"Points": lift.outputs[0], "Instance": nodes.outputs[0],
                                                "Rotation": _face_cam(b, c, pos)})
    return _tube(b, c, _join(b, curves.outputs[0], dots.outputs[0]))


def layer_circle_chain(b, c, p):
    ring_r = b.mul(c.r, p["Radius"])
    height = b.madd(p["Height"], b.mul(c.ez, 0.5), 0.0)
    center = b.vmath("ADD", c.center, b.vec(0, 0, height))
    path = b.g("GeometryNodeCurvePrimitiveCircle", {"Radius": ring_r, "Resolution": 8})
    pts = b.g("GeometryNodeResampleCurve", {"Curve": path.outputs[0], "Count": p["Count"]})
    set_menu(pts.inputs["Mode"], "Count")
    link_r = b.mul(b.div(b.mul(ring_r, math.pi), p["Count"]), 0.9)
    link = b.g("GeometryNodeCurvePrimitiveCircle", {"Radius": link_r, "Resolution": 16})
    pos = b.new("GeometryNodeInputPosition").outputs[0]
    radial = b.g("FunctionNodeAxesToRotation", {"Primary Axis": pos, "Secondary Axis": UP},
                 primary_axis="Z", secondary_axis="Y").outputs[0]
    idx = b.new("GeometryNodeInputIndex").outputs[0]
    big = b.math("FLOORED_MODULO", idx, 4.0)
    scale = b.mix(b.math("COMPARE", big, 0.0, 0.5), 0.55, 1.0)
    inst = b.g("GeometryNodeInstanceOnPoints", {"Points": pts.outputs[0], "Instance": link.outputs[0],
                                                "Rotation": radial, "Scale": scale})
    spin = b.g("FunctionNodeEulerToRotation", {"Euler": b.vec(0, 0, b.mul(c.time, 0.15))}).outputs[0]
    t = b.g("GeometryNodeTransform", {"Geometry": _join(b, inst.outputs[0], path.outputs[0]),
                                      "Translation": center, "Rotation": spin})
    return _tube(b, c, t.outputs[0])


def layer_fracture_grid(b, c, p):
    sub = b.g("GeometryNodeSubdivideMesh", {"Mesh": c.target, "Level": p["Detail"]})
    tri = b.g("GeometryNodeTriangulate", {"Mesh": sub.outputs[0]})
    dual = b.g("GeometryNodeDualMesh", {"Mesh": tri.outputs[0]})
    normal = b.new("GeometryNodeInputNormal").outputs[0]
    lift = b.g("GeometryNodeSetPosition", {"Geometry": dual.outputs[0],
                                           "Offset": b.vmath("SCALE", normal, scale=b.mul(c.r, 0.012))})
    rnd = b.g("FunctionNodeRandomValue", {"Min": 0.0, "Max": 1.0, "Seed": c.seed}, data_type="FLOAT")
    gone = b.g("FunctionNodeCompare", {"A": rnd.outputs["Value"], "B": p["Break"]}, operation="LESS_THAN")
    dele = b.g("GeometryNodeDeleteGeometry", {"Geometry": lift.outputs[0], "Selection": gone.outputs[0]},
               domain="EDGE", mode="EDGE_FACE")
    curve = b.g("GeometryNodeMeshToCurve", {"Mesh": dele.outputs[0]})
    return _tube(b, c, curve.outputs[0])


def layer_scan_brackets(b, c, p):
    bb = b.g("GeometryNodeBoundBox", {"Geometry": c.target}).outputs["Bounding Box"]
    pos = b.new("GeometryNodeInputPosition").outputs[0]
    grow = b.add(1.0, b.mul(p["Gap"], b.madd(b.math("SINE", b.mul(c.time, 3.0)), 0.15, 1.0)))
    out = b.vmath("ADD", c.center, b.vmath("SCALE", b.vmath("SUBTRACT", pos, c.center), scale=grow))
    moved = b.g("GeometryNodeSetPosition", {"Geometry": bb, "Position": out})
    edges_only = b.g("GeometryNodeDeleteGeometry", {"Geometry": moved.outputs[0], "Selection": True},
                     domain="FACE", mode="ONLY_FACE")
    split = b.g("GeometryNodeSplitEdges", {"Mesh": edges_only.outputs[0]})
    curve = b.g("GeometryNodeMeshToCurve", {"Mesh": split.outputs[0]}).outputs[0]
    a = b.g("GeometryNodeTrimCurve", {"Curve": curve, "Start": 0.0, "End": p["Length"]})
    z = b.g("GeometryNodeTrimCurve", {"Curve": curve, "Start": b.sub(1.0, p["Length"]), "End": 1.0})
    return _tube(b, c, _join(b, a.outputs[0], z.outputs[0]))


def layer_measure_arcs(b, c, p):
    r = b.mul(c.r, p["Radius"])
    base = b.vec(b.xyz(c.center)[0], b.xyz(c.center)[1], c.minz)
    arc = b.g("GeometryNodeCurveArc", {"Radius": r, "Resolution": 96, "Start Angle": b.mul(c.time, 0.2),
                                        "Sweep Angle": p["Sweep"]})
    inner = b.g("GeometryNodeCurveArc", {"Radius": b.mul(r, 0.93), "Resolution": 64,
                                          "Start Angle": b.mul(c.time, -0.12), "Sweep Angle": b.mul(p["Sweep"], 0.45)})
    ticks_pts = b.g("GeometryNodeResampleCurve", {"Curve": arc.outputs[0], "Count": p["Ticks"]})
    set_menu(ticks_pts.inputs["Mode"], "Count")
    tick = _line(b, (0, 0, 0), b.vec(b.mul(c.r, 0.05), 0, 0))
    pos = b.new("GeometryNodeInputPosition").outputs[0]
    radial = b.g("FunctionNodeAxesToRotation", {"Primary Axis": pos, "Secondary Axis": UP},
                 primary_axis="X", secondary_axis="Z").outputs[0]
    idx = b.new("GeometryNodeInputIndex").outputs[0]
    major = b.math("COMPARE", b.math("FLOORED_MODULO", idx, 6.0), 0.0, 0.5)
    inst = b.g("GeometryNodeInstanceOnPoints", {"Points": ticks_pts.outputs[0], "Instance": tick,
                                                "Rotation": radial, "Scale": b.madd(major, 1.2, 1.0)})
    ring = b.g("GeometryNodeTransform", {"Geometry": _join(b, arc.outputs[0], inner.outputs[0], inst.outputs[0]),
                                         "Translation": base})

    # Width dimension line with end ticks and a value label.
    off = b.mul(c.r, 0.25)
    y = b.sub(c.miny, off)
    a = b.vec(c.minx, y, c.minz)
    z = b.vec(c.maxx, y, c.minz)
    th = b.mul(c.r, 0.05)
    dim = _join(b, _line(b, a, z),
                _line(b, b.vec(c.minx, y, b.sub(c.minz, th)), b.vec(c.minx, y, b.add(c.minz, th))),
                _line(b, b.vec(c.maxx, y, b.sub(c.minz, th)), b.vec(c.maxx, y, b.add(c.minz, th))))
    width = b.sub(c.maxx, c.minx)
    label = _text(b, c, _strjoin(b, "W ", _num(b, width, 3), " m"), b.mul(b.mul(c.text_size, c.r), 0.07), "CENTER")
    mid = b.vec(b.mul(b.add(c.minx, c.maxx), 0.5), y, b.add(c.minz, th))
    label_t = b.g("GeometryNodeTransform", {"Geometry": label, "Translation": mid, "Rotation": _face_cam(b, c, mid)})
    return _join(b, _tube(b, c, _join(b, ring.outputs[0], dim)), label_t.outputs[0])


def layer_node_network(b, c, p):
    rnd = b.g("FunctionNodeRandomValue", {"Min": (-1.0, -1.0, -1.0), "Max": (1.0, 1.0, 1.0), "Seed": c.seed},
              data_type="FLOAT_VECTOR")
    half = b.vmath("SCALE", c.extent, scale=b.mul(p["Spread"], 0.5))
    pos = b.vmath("ADD", c.center, b.vmath("MULTIPLY", rnd.outputs[0], half))
    pts = b.g("GeometryNodePoints", {"Count": p["Count"], "Position": pos})
    hull = b.g("GeometryNodeConvexHull", {"Geometry": pts.outputs[0]}).outputs[0]
    edges = b.g("GeometryNodeMeshToCurve", {"Mesh": hull}).outputs[0]
    verts = b.g("GeometryNodeMeshToPoints", {"Mesh": hull}).outputs[0]
    ball = b.g("GeometryNodeMeshIcoSphere", {"Radius": b.mul(c.r, 0.018), "Subdivisions": 1}).outputs[0]
    balls = b.g("GeometryNodeInstanceOnPoints", {"Points": verts, "Instance": ball}).outputs[0]
    spin = b.g("FunctionNodeEulerToRotation", {"Euler": b.vec(0, 0, b.mul(c.time, 0.1))}).outputs[0]
    net = _join(b, _tube(b, c, edges), b.g("GeometryNodeRealizeInstances", {"Geometry": balls}).outputs[0])
    # rotate around the object's center
    to0 = b.g("GeometryNodeTransform", {"Geometry": net, "Translation": b.vmath("SCALE", c.center, scale=-1.0)})
    rot = b.g("GeometryNodeTransform", {"Geometry": to0.outputs[0], "Rotation": spin})
    back = b.g("GeometryNodeTransform", {"Geometry": rot.outputs[0], "Translation": c.center})
    return back.outputs[0]


def layer_lock_frame(b, c, p):
    pulse = b.madd(b.math("SINE", b.mul(c.time, 4.0)), 0.03, 1.0)
    a = b.mul(b.mul(c.r, p["Size"]), pulse)
    na = b.mul(a, -1.0)
    k = b.mul(a, 0.3)
    parts = []
    for sx, sy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
        cx, cy = (a if sx > 0 else na), (a if sy > 0 else na)
        corner = b.vec(cx, cy, 0)
        parts.append(_line(b, corner, b.vec(b.sub(cx, b.mul(k, sx)), cy, 0)))
        parts.append(_line(b, corner, b.vec(cx, b.sub(cy, b.mul(k, sy)), 0)))
    g = b.mul(a, 0.08)
    h = b.mul(a, 0.2)
    for v0, v1 in (((g, 0), (h, 0)), ((b.mul(g, -1), 0), (b.mul(h, -1), 0)),
                   ((0, g), (0, h)), ((0, b.mul(g, -1)), (0, b.mul(h, -1)))):
        parts.append(_line(b, b.vec(v0[0], v0[1], 0), b.vec(v1[0], v1[1], 0)))
    frame = _tube(b, c, _join(b, *parts))
    dist = b.vmath("DISTANCE", c.cam, c.center)
    label = _text(b, c, _strjoin(b, "LOCK  D ", _num(b, dist, 2), " m"), b.mul(b.mul(c.text_size, c.r), 0.07))
    label_t = b.g("GeometryNodeTransform", {"Geometry": label,
                                            "Translation": b.vec(na, b.add(a, b.mul(c.r, 0.03)), 0)})
    t = b.g("GeometryNodeTransform", {"Geometry": _join(b, frame, label_t.outputs[0]),
                                      "Translation": c.center, "Rotation": _face_cam(b, c, c.center)})
    return t.outputs[0]


def layer_bio_scanner(b, c, p):
    phase = b.madd(b.math("SINE", b.mul(c.time, 1.2)), 0.5, 0.5)
    h = b.madd(phase, c.ez, c.minz)
    cx, cy, _ = b.xyz(c.center)
    ring_c = b.vec(cx, cy, h)
    ring = b.g("GeometryNodeCurvePrimitiveCircle", {"Radius": b.mul(c.r, 1.1), "Resolution": 96})
    ring2 = b.g("GeometryNodeCurvePrimitiveCircle", {"Radius": b.mul(c.r, 1.18), "Resolution": 96})
    rings = b.g("GeometryNodeTransform", {"Geometry": _join(b, ring.outputs[0], ring2.outputs[0]), "Translation": ring_c})

    pts, nrm = _first_n_points(b, c, 1000000, p["Density"], 5)
    z = b.xyz(b.new("GeometryNodeInputPosition").outputs[0])[2]
    far = b.g("FunctionNodeCompare", {"A": b.math("ABSOLUTE", b.sub(z, h)), "B": b.mul(p["Band"], c.ez)},
              operation="GREATER_THAN")
    band = b.g("GeometryNodeDeleteGeometry", {"Geometry": pts, "Selection": far.outputs[0]}).outputs[0]
    lift = b.g("GeometryNodeSetPosition", {"Geometry": band, "Offset": b.vmath("SCALE", nrm, scale=b.mul(c.r, 0.01))})
    dot = b.g("GeometryNodeMeshIcoSphere", {"Radius": b.mul(c.r, 0.006), "Subdivisions": 1}).outputs[0]
    dots = b.g("GeometryNodeInstanceOnPoints", {"Points": lift.outputs[0], "Instance": dot}).outputs[0]
    return _join(b, _tube(b, c, rings.outputs[0]), b.g("GeometryNodeRealizeInstances", {"Geometry": dots}).outputs[0])


BUILDERS = {
    "Surface Data": layer_surface_data, "Labels": layer_labels, "Anchor Motifs": layer_anchor_motifs,
    "Surface Links": layer_surface_links, "Circle Chain": layer_circle_chain, "Fracture Grid": layer_fracture_grid,
    "Scan Brackets": layer_scan_brackets, "Measure Arcs": layer_measure_arcs, "Node Network": layer_node_network,
    "Object Lock Frame": layer_lock_frame, "Bio Scanner": layer_bio_scanner,
}


def data_group():
    g = bpy.data.node_groups.get(GROUP_NAME)
    if g and g.get("pl_version") == GROUP_VERSION:
        return g
    if g is None:
        g = bpy.data.node_groups.new(GROUP_NAME, "GeometryNodeTree")
    g.interface.clear()
    g["pl_version"] = GROUP_VERSION
    g.is_modifier = True

    socket(g, "Geometry", "INPUT", "NodeSocketGeometry")
    socket(g, "Target", "INPUT", "NodeSocketObject")
    socket(g, "Material", "INPUT", "NodeSocketMaterial")
    socket(g, "Line Weight", "INPUT", "NodeSocketFloat", 0.003, 0.0, subtype="DISTANCE")
    socket(g, "Text Size", "INPUT", "NodeSocketFloat", 1.0, 0.0)
    socket(g, "Speed", "INPUT", "NodeSocketFloat", 1.0)
    socket(g, "Seed", "INPUT", "NodeSocketInt", 0)
    for name, _accent, on, params in LAYERS:
        panel = g.interface.new_panel(name, default_closed=True)
        socket(g, name, "INPUT", "NodeSocketBool", on, parent=panel)
        socket(g, f"{name} Opacity", "INPUT", "NodeSocketFloat", 1.0, 0.0, 1.0, subtype="FACTOR", parent=panel)
        for pname, stype, default, lo, hi in params:
            socket(g, f"{name} {pname}", "INPUT", stype, default, lo, hi, parent=panel)
    socket(g, "Geometry", "OUTPUT", "NodeSocketGeometry")

    b = Builder(g)
    gi = b.new("NodeGroupInput").outputs
    go = b.new("NodeGroupOutput")

    c = Ctx()
    info = b.g("GeometryNodeObjectInfo", {"Object": gi["Target"]}, transform_space="RELATIVE")
    c.target = info.outputs["Geometry"]
    bb = b.g("GeometryNodeBoundBox", {"Geometry": c.target})
    mn, mx = bb.outputs["Min"], bb.outputs["Max"]
    c.minx, c.miny, c.minz = b.xyz(mn)
    c.maxx, c.maxy, c.maxz = b.xyz(mx)
    c.extent = b.vmath("SUBTRACT", mx, mn)
    c.ez = b.xyz(c.extent)[2]
    c.center = b.vmath("SCALE", b.vmath("ADD", mn, mx), scale=0.5)
    c.r = b.math("MAXIMUM", b.mul(b.vmath("LENGTH", c.extent), 0.5), 0.001)
    c.r2 = b.mul(c.r, c.r)
    cam = b.g("GeometryNodeObjectInfo", {"Object": b.new("GeometryNodeInputActiveCamera").outputs[0]},
              transform_space="RELATIVE")
    c.cam = cam.outputs["Location"]
    c.time = b.mul(b.new("GeometryNodeInputSceneTime").outputs["Seconds"], gi["Speed"])
    c.seed = gi["Seed"]
    c.text_size = gi["Text Size"]
    c.profile = b.g("GeometryNodeCurvePrimitiveCircle", {"Radius": gi["Line Weight"], "Resolution": 4}).outputs[0]

    layers = []
    for name, accent, _on, params in LAYERS:
        p = {pname: gi[f"{name} {pname}"] for pname, *_ in params}
        mesh = BUILDERS[name](b, c, p)
        st = b.g("GeometryNodeStoreNamedAttribute", {"Geometry": mesh, "Name": "pl_alpha",
                                                     "Value": gi[f"{name} Opacity"]})
        st2 = b.g("GeometryNodeStoreNamedAttribute", {"Geometry": st.outputs[0], "Name": "pl_accent",
                                                      "Value": 1.0 if accent else 0.0})
        sw = b.g("GeometryNodeSwitch", {"Switch": gi[name], "True": st2.outputs[0]}, input_type="GEOMETRY",
                 label=name)
        layers.append(sw.outputs[0])

    mat = b.g("GeometryNodeSetMaterial", {"Geometry": _join(b, *layers), "Material": gi["Material"]})
    b.link(mat.outputs[0], go.inputs[0])
    arrange(g)
    return g


# ---------------------------------------------------------------------------
# Material and settings
# ---------------------------------------------------------------------------

def build_material(obj):
    mat = bpy.data.materials.new(f"PL Data · {obj.name}")
    if mat.node_tree is None:
        mat.use_nodes = True
    b = Builder(mat.node_tree)
    out = b.new("ShaderNodeOutputMaterial")
    alpha = b.new("ShaderNodeAttribute", attribute_name="pl_alpha")
    accent = b.new("ShaderNodeAttribute", attribute_name="pl_accent")
    mix = b.new("ShaderNodeMix", name="PL_COLOR", data_type="RGBA")
    b.link(accent.outputs["Fac"], mix.inputs["Factor"])
    em = b.new("ShaderNodeEmission", name="PL_EMISSION")
    b.link(mix.outputs[2], em.inputs["Color"])
    opacity = b.new("ShaderNodeValue", name="PL_OPACITY")
    fac = b.mul(alpha.outputs["Fac"], opacity.outputs[0])
    tr = b.new("ShaderNodeBsdfTransparent")
    ms = b.new("ShaderNodeMixShader")
    b.link(fac, ms.inputs["Fac"])
    b.link(tr.outputs[0], ms.inputs[1])
    b.link(em.outputs[0], ms.inputs[2])
    b.link(ms.outputs[0], out.inputs["Surface"])
    arrange(mat.node_tree)
    if hasattr(mat, "surface_render_method"):
        mat.surface_render_method = "BLENDED"
    if hasattr(mat, "use_transparency_overlap"):
        mat.use_transparency_overlap = True
    return mat


def sync_material(obj):
    s = obj.prism_data
    mat = s.material
    if not mat or not mat.node_tree:
        return
    nt = mat.node_tree
    mix = nt.nodes.get("PL_COLOR")
    if mix:
        mix.inputs[6].default_value = s.color   # A (color)
        mix.inputs[7].default_value = s.accent  # B (color)
    em = nt.nodes.get("PL_EMISSION")
    if em:
        em.inputs["Strength"].default_value = s.glow
    op = nt.nodes.get("PL_OPACITY")
    if op:
        op.outputs[0].default_value = s.opacity
    mat.diffuse_color = s.color


def _upd(self, context):
    sync_material(self.id_data)


class PL_DataSettings(bpy.types.PropertyGroup):
    material: PointerProperty(type=bpy.types.Material)
    color: FloatVectorProperty(name="Color", subtype="COLOR", size=4, min=0, max=1,
                               default=(0.55, 0.85, 1.0, 1.0), update=_upd)
    accent: FloatVectorProperty(name="Accent", subtype="COLOR", size=4, min=0, max=1,
                                default=(1.0, 0.45, 0.15, 1.0), update=_upd)
    glow: FloatProperty(name="Glow", default=1.5, min=0.0, soft_max=50.0, update=_upd)
    opacity: FloatProperty(name="Opacity", default=1.0, min=0.0, max=1.0, subtype="FACTOR", update=_upd)


def get_mod(obj):
    return obj.modifiers.get(MOD_NAME) if obj else None


def find_overlay(obj):
    """The overlay object for `obj`: itself if it is one, otherwise a child overlay."""
    if obj is None:
        return None
    if get_mod(obj):
        return obj
    for ch in obj.children:
        if get_mod(ch):
            return ch
    return None


def set_inputs(obj, values):
    mod = get_mod(obj)
    keys = modifier_inputs(mod)
    for name, val in values.items():
        if name in keys:
            set_input(mod, keys[name], val)
    obj.update_tag()


class PRISM_OT_add_data_points(bpy.types.Operator):
    bl_idname = "prism.add_data_points"
    bl_label = "Add Data Points"
    bl_description = "Add a technical data overlay around each selected object"
    bl_options = {"REGISTER", "UNDO"}

    preset: EnumProperty(name="Preset", items=PRESET_ITEMS, default="MINIMAL")

    @classmethod
    def poll(cls, context):
        return any(o.type == "MESH" for o in context.selected_objects)

    def execute(self, context):
        g = data_group()
        made = []
        for target in [o for o in context.selected_objects if o.type == "MESH" and not find_overlay(o)]:
            mesh = bpy.data.meshes.new(f"{target.name} Data")
            ov = bpy.data.objects.new(f"{target.name} Data", mesh)
            context.collection.objects.link(ov)
            ov.parent = target
            ov.visible_shadow = False
            mod = ov.modifiers.new(MOD_NAME, "NODES")
            mod.node_group = g
            mat = build_material(ov)
            ov.prism_data.material = mat
            sync_material(ov)
            set_inputs(ov, {"Target": target, "Material": mat})
            apply_preset(ov, self.preset)
            made.append(ov)
        return {"FINISHED"} if made else {"CANCELLED"}


def apply_preset(obj, key):
    on = PRESETS[key][1]
    set_inputs(obj, {name: (name in on) for name in LAYER_NAMES})


class PRISM_OT_data_preset(bpy.types.Operator):
    bl_idname = "prism.data_preset"
    bl_label = "Data Points Preset"
    bl_options = {"REGISTER", "UNDO"}
    preset: EnumProperty(name="Preset", items=PRESET_ITEMS)

    def execute(self, context):
        ov = find_overlay(context.object)
        if ov:
            apply_preset(ov, self.preset)
        return {"FINISHED"}


class PRISM_OT_remove_data_points(bpy.types.Operator):
    bl_idname = "prism.remove_data_points"
    bl_label = "Remove Data Points"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        ov = find_overlay(context.object)
        if ov:
            mat = ov.prism_data.material
            bpy.data.objects.remove(ov)
            if mat and mat.users == 0:
                bpy.data.materials.remove(mat)
        return {"FINISHED"}


def draw(layout, context):
    ov = find_overlay(context.object)
    if not ov:
        layout.operator_menu_enum("prism.add_data_points", "preset", text="Add Data Points", icon="STICKY_UVS_DISABLE")
        return
    mod = get_mod(ov)
    keys = modifier_inputs(mod)
    s = ov.prism_data
    layout.operator_menu_enum("prism.data_preset", "preset", text="Presets", icon="PRESET")

    box = layout.box()
    row = box.row(align=True)
    row.prop(s, "color", text="")
    row.prop(s, "accent", text="")
    col = box.column(align=True)
    col.prop(s, "glow"); col.prop(s, "opacity")
    for n in ("Line Weight", "Text Size", "Speed", "Seed"):
        draw_input(col, mod, keys[n], n)

    for name, _accent, _on, params in LAYERS:
        box = layout.box()
        row = box.row(align=True)
        draw_input(row, mod, keys[name], name)
        if get_input(mod, keys[name]):
            draw_input(row, mod, keys[f"{name} Opacity"], "")
            col = box.column(align=True)
            for pname, *_ in params:
                draw_input(col, mod, keys[f"{name} {pname}"], pname)
    layout.operator("prism.remove_data_points", icon="X")


classes = (PL_DataSettings, PRISM_OT_add_data_points, PRISM_OT_data_preset, PRISM_OT_remove_data_points)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Object.prism_data = PointerProperty(type=PL_DataSettings)


def unregister():
    del bpy.types.Object.prism_data
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
