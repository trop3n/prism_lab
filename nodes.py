"""Small helpers for building node trees from Python without the boilerplate."""

import bpy
from collections import defaultdict

_SCALAR_VMATH = {"LENGTH", "DOT_PRODUCT", "DISTANCE"}


class Builder:
    def __init__(self, tree, clear=True):
        self.tree = tree
        if clear:
            tree.nodes.clear()

    # -- basics ---------------------------------------------------------------
    def new(self, idname, label=None, name=None, **attrs):
        n = self.tree.nodes.new(idname)
        for k, v in attrs.items():
            setattr(n, k, v)
        if label:
            n.label = label
        if name:
            n.name = name
        return n

    def set(self, socket, value):
        """Link a socket or set a constant on an input socket."""
        if value is None:
            return
        if isinstance(value, bpy.types.NodeSocket):
            self.tree.links.new(value, socket)
        else:
            socket.default_value = value

    def link(self, a, b):
        self.tree.links.new(a, b)

    def g(self, idname, ins=None, label=None, **attrs):
        """Create a node, setting inputs from a {name_or_index: value_or_socket} dict."""
        n = self.new(idname, label=label, **attrs)
        for k, v in (ins or {}).items():
            self.set(n.inputs[k], v)
        return n

    # -- math -----------------------------------------------------------------
    def math(self, op, *args, clamp=False):
        n = self.new("ShaderNodeMath", operation=op, use_clamp=clamp)
        for sock, a in zip(n.inputs, args):
            self.set(sock, a)
        return n.outputs[0]

    def add(self, a, b): return self.math("ADD", a, b)
    def sub(self, a, b): return self.math("SUBTRACT", a, b)
    def mul(self, a, b): return self.math("MULTIPLY", a, b)
    def div(self, a, b): return self.math("DIVIDE", a, b)
    def madd(self, a, b, c): return self.math("MULTIPLY_ADD", a, b, c)

    def vmath(self, op, a, b=None, scale=None):
        n = self.new("ShaderNodeVectorMath", operation=op)
        self.set(n.inputs[0], a)
        if b is not None:
            self.set(n.inputs[1], b)
        if scale is not None:
            self.set(n.inputs["Scale"], scale)
        return n.outputs["Value" if op in _SCALAR_VMATH else "Vector"]

    def xyz(self, vec):
        n = self.new("ShaderNodeSeparateXYZ")
        self.set(n.inputs[0], vec)
        return n.outputs

    def vec(self, x=0.0, y=0.0, z=0.0):
        n = self.new("ShaderNodeCombineXYZ")
        for s, v in zip(n.inputs, (x, y, z)):
            self.set(s, v)
        return n.outputs[0]

    def mix(self, fac, a, b):
        n = self.new("ShaderNodeMix", data_type="FLOAT")
        self.set(n.inputs["Factor"], fac)
        self.set(n.inputs[2], a)
        self.set(n.inputs[3], b)
        return n.outputs[0]


def set_menu(sock, value):
    """Set a menu socket from an identifier like "BOTTOM_BASELINE" or a name like "Bottom Baseline"."""
    words = value.replace("_", " ")
    for cand in (value, words.title(), words.capitalize(), words.upper().replace(" ", "_")):
        try:
            sock.default_value = cand
            return
        except TypeError:
            pass
    raise ValueError(f"{value!r} not in menu {sock.name}")


def socket(tree, name, in_out, stype, default=None, lo=None, hi=None, desc="", subtype=None, parent=None):
    kw = {"parent": parent} if parent is not None else {}
    s = tree.interface.new_socket(name, in_out=in_out, socket_type=stype, description=desc, **kw)
    if default is not None and hasattr(s, "default_value"):
        s.default_value = default
    if lo is not None and hasattr(s, "min_value"):
        s.min_value = lo
    if hi is not None and hasattr(s, "max_value"):
        s.max_value = hi
    if subtype and hasattr(s, "subtype"):
        s.subtype = subtype
    return s


def versioned_group(base, version, tree_type):
    """Get or create the node group for this add-on version.

    Each version gets its own group ("<base> v<version>") instead of rebuilding an
    existing one in place: rebuilding regenerates socket identifiers, which would wipe
    the settings of every modifier already using the group (e.g. in an older file).
    Returns (group, needs_build).
    """
    name = f"{base} v{version}"
    g = bpy.data.node_groups.get(name)
    if g is not None and g.bl_idname == tree_type and g.get("pl_version") == version:
        return g, False
    g = bpy.data.node_groups.new(name, tree_type)
    g["pl_version"] = version
    return g, True


def arrange(tree, dx=230, dy=190):
    """Lay nodes out in columns by distance from the output so the tree is readable."""
    incoming = defaultdict(list)
    for l in tree.links:
        incoming[l.to_node].append(l.from_node)
    outputs = [n for n in tree.nodes if n.bl_idname in ("NodeGroupOutput", "ShaderNodeOutputMaterial")]
    depth = {n: 0 for n in outputs}
    changed = True
    while changed:
        changed = False
        for n, d in list(depth.items()):
            for m in incoming[n]:
                if depth.get(m, -1) < d + 1:
                    depth[m] = d + 1
                    changed = True
    cols = defaultdict(list)
    for n in tree.nodes:
        cols[depth.get(n, max(depth.values(), default=0) + 1)].append(n)
    for d, nodes in cols.items():
        for i, n in enumerate(nodes):
            n.location = (-d * dx, (len(nodes) / 2 - i) * dy)


def _input_struct(mod, ident):
    props = getattr(mod, "properties", None)
    inputs = getattr(props, "inputs", None)
    return getattr(inputs, ident, None) if inputs is not None else None


def get_input(mod, ident):
    st = _input_struct(mod, ident)
    return st.value if st is not None else mod[ident]


def set_input(mod, ident, value):
    st = _input_struct(mod, ident)
    if st is not None:  # Blender 5.2+: typed structs under mod.properties.inputs
        st.value = value
    else:
        mod[ident] = value


def draw_input(layout, mod, ident, text):
    st = _input_struct(mod, ident)
    if st is not None:
        layout.prop(st, "value", text=text)
    else:
        layout.prop(mod, f'["{ident}"]', text=text)


def modifier_inputs(mod):
    """Map interface socket name -> modifier property key for a Geometry Nodes modifier."""
    out = {}
    if mod.node_group:
        for item in mod.node_group.interface.items_tree:
            if item.item_type == "SOCKET" and item.in_out == "INPUT":
                out[item.name] = item.identifier
    return out
