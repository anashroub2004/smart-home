"""Build the smart-home mockup (scale 1:10) in Blender from docs/mockup/mockup-data.json.

The data comes from the page docs/mockup/mockup-design.html (its data block is the single source of dimensions):
    node docs/mockup/make_obj.js      ->  mockup.obj, mockup.mtl and mockup-data.json

What it builds (everything named, in collections):
  House      walls with door / window openings, glass, door leaves, raised floor + spacers, base board, ceiling beams
  Furniture  detailed procedural furniture at the same place and size as the page (sofa, bed, kitchen, bathroom ...)
  Parts      the 27 modules shaped like the real ones at their real size (LD2410C, DHT11, BH1750, SG90, ESP32, Pi 5 ...)
  Wires      every conductor as a round tube (red 5V, black GND, orange 3.3V, blue SDA, green SCL, yellow signal,
             white / purple radar TX / RX) from the part, down its floor hole, under the floor, out of the east wall
             to the node's ESP32 in the hub
  Hub        the clear acrylic box east of the house with the Pi 5, router, both ESP32s on expansion boards, supply
  Cameras    overview, kitchen, bedroom, wires (transparent floor), hub, plus an orbiting animation camera

The timeline is an assembly animation: walls rise, furniture drops in, each part drops to its place, the floor turns
transparent and the wires grow from every part to the hub, then the lights turn on.

Units: Blender shows centimetres of the real mockup (80 x 60 cm). Everything sits under the empty "Mockup_root"
(scale 0.01, so 1 cm of the mockup = 0.01 m in the scene). Axes: x east, y north, z up, origin = south-west corner.

Run it
  In Blender:  Scripting tab -> Open -> this file -> Run Script   (it REPLACES everything in the open file)
  Command line (from the repo root):
    blender -b -P docs/mockup/blender/build_mockup.py -- --save mockup.blend
    blender -b -P docs/mockup/blender/build_mockup.py -- --render          (stills -> docs/mockup/renders/)
    blender -b -P docs/mockup/blender/build_mockup.py -- --anim out.mp4    (the assembly video, Cycles)
  Options: --samples N  --res 1920x1080  --eevee (fast preview engine)  --wire-colors node
"""
import json
import math
import os
import sys

import bmesh
import bpy
from mathutils import Euler, Matrix, Vector

# --------------------------------------------------------------------------------------------- settings
WIRE_COLOR_MODE = "cable"       # "cable" = one coloured cable per part (default, see cables.md),
                                # "signal" = every conductor in its wire colour, "node" = conductors in the node colour
WIRE_R = 0.045                  # conductor radius, cm (about 0.9 mm wire)
WIRE_GAP = 0.11                 # spacing between conductors of one bundle, cm
FPS = 24

# timeline (frames)
F_WALLS = (1, 24)
F_FURN = 20                     # first furniture drop
F_PARTS = 80                    # first part drop
F_FLOOR_FADE = (172, 186)
F_WIRES = 186                   # first wire starts growing
WIRE_GROW = 40
F_WIRES_HOLD = 282              # wires fully visible, floor transparent (still "wires" is rendered here)
F_FLOOR_BACK = (292, 306)
F_LIGHTS = (306, 316)
F_END = 336


# --------------------------------------------------------------------------------------------- data
def find_data():
    cands = []
    try:
        cands.append(os.path.dirname(os.path.abspath(__file__)))
    except NameError:
        pass
    for t in bpy.data.texts:
        if t.filepath and t.filepath.endswith(".py"):
            cands.append(os.path.dirname(bpy.path.abspath(t.filepath)))
    cands.append(os.getcwd())
    cands.append(os.path.join(os.getcwd(), "docs", "mockup", "blender"))
    for c in cands:
        for p in (os.path.join(c, "..", "mockup-data.json"), os.path.join(c, "mockup-data.json")):
            if os.path.isfile(p):
                return os.path.normpath(p)
    raise FileNotFoundError("mockup-data.json not found: run `node docs/mockup/make_obj.js` and keep this script in "
                            "docs/mockup/blender/")


DATA_PATH = find_data()
MOCKUP_DIR = os.path.dirname(DATA_PATH)
with open(DATA_PATH, encoding="utf-8") as fh:
    DATA = json.load(fh)
W, D, H, T = DATA["W"], DATA["D"], DATA["H"], DATA["T"]
FLOOR_Z = DATA["FLOOR_Z"]
ROOMS, WALLS, FURN, COMP, HUB = DATA["ROOMS"], DATA["WALLS"], DATA["FURN"], DATA["COMP"], DATA["HUB"]
ROUTES = {r["id"]: r for r in DATA["ROUTES"]}


def args():
    a = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    o = {"save": None, "render": False, "anim": None, "samples": 128, "res": (1920, 1080), "eevee": False}
    i = 0
    while i < len(a):
        k = a[i]
        if k == "--save":
            o["save"] = a[i + 1]; i += 1
        elif k == "--render":
            o["render"] = True
        elif k == "--anim":
            o["anim"] = a[i + 1]; i += 1
        elif k == "--samples":
            o["samples"] = int(a[i + 1]); i += 1
        elif k == "--res":
            w, h = a[i + 1].lower().split("x"); o["res"] = (int(w), int(h)); i += 1
        elif k == "--eevee":
            o["eevee"] = True
        elif k == "--wire-colors":
            global WIRE_COLOR_MODE
            WIRE_COLOR_MODE = a[i + 1]; i += 1
        i += 1
    return o


# --------------------------------------------------------------------------------------------- scene reset
def reset_scene():
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob, do_unlink=True)
    for c in list(bpy.data.collections):
        bpy.data.collections.remove(c)
    for block in (bpy.data.meshes, bpy.data.curves, bpy.data.materials, bpy.data.cameras, bpy.data.lights,
                  bpy.data.actions):
        for item in list(block):
            if item.users == 0:
                block.remove(item)
    sc = bpy.context.scene
    sc.unit_settings.system = "METRIC"
    sc.unit_settings.length_unit = "CENTIMETERS"
    sc.frame_start, sc.frame_end = 1, F_END
    sc.render.fps = FPS


COLL = {}


def coll(path):
    """Collection by path like 'Parts/Living node' (created on first use)."""
    if path in COLL:
        return COLL[path]
    parent = bpy.context.scene.collection
    if "/" in path:
        parent = coll(path.rsplit("/", 1)[0])
    c = bpy.data.collections.new(path.rsplit("/", 1)[-1])
    parent.children.link(c)
    COLL[path] = c
    return c


# --------------------------------------------------------------------------------------------- materials
MATS = {}


def _set(bsdf, names, value):
    for n in names if isinstance(names, (list, tuple)) else [names]:
        if n in bsdf.inputs:
            bsdf.inputs[n].default_value = value
            return bsdf.inputs[n]
    return None


def mat(name, color, rough=0.5, metal=0.0, alpha=1.0, emit=0.0, emit_color=None, coat=0.0):
    if name in MATS:
        return MATS[name]
    m = bpy.data.materials.new(name)
    if m.node_tree is None:                     # Blender < 5 creates materials without nodes
        m.use_nodes = True
    bsdf = next(n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    rgba = (*color, 1.0)
    _set(bsdf, "Base Color", rgba)
    _set(bsdf, "Roughness", rough)
    _set(bsdf, "Metallic", metal)
    _set(bsdf, ["Coat Weight", "Clearcoat"], coat)
    if alpha < 1.0:
        _set(bsdf, "Alpha", alpha)
        for attr, val in (("surface_render_method", "BLENDED"), ("blend_method", "BLEND")):
            try:
                setattr(m, attr, val)
            except Exception:
                pass
    if emit > 0 or emit_color is not None:
        _set(bsdf, ["Emission Color", "Emission"], (*(emit_color or color), 1.0))
        _set(bsdf, "Emission Strength", emit)
    m.diffuse_color = (*color, alpha)          # viewport solid colour
    MATS[name] = m
    return m


def bsdf_input(m, name):
    bsdf = next(n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    return bsdf.inputs[name] if name in bsdf.inputs else None


def make_materials():
    mat("foam_white", (0.86, 0.87, 0.89), 0.85)
    mat("floor", (0.92, 0.92, 0.90), 0.8, alpha=1.0 - 1e-4)          # alpha animated (transparent floor)
    mat("base_board", (0.05, 0.06, 0.08), 0.7)                      # dark, so the wires stand out
    mat("spacer", (0.75, 0.75, 0.72), 0.4, metal=0.6)
    mat("glass", (0.70, 0.86, 0.95), 0.05, alpha=0.22)
    mat("acrylic", (0.85, 0.93, 0.98), 0.03, alpha=0.08)
    mat("door_leaf", (0.62, 0.45, 0.30), 0.6)
    mat("wood", (0.55, 0.38, 0.24), 0.55)
    mat("wood_light", (0.78, 0.62, 0.44), 0.55)
    mat("fabric_sofa", (0.30, 0.38, 0.48), 0.95)
    mat("fabric_cushion", (0.78, 0.70, 0.56), 0.95)
    mat("fabric_bed", (0.93, 0.93, 0.92), 0.9)
    mat("fabric_blanket", (0.55, 0.30, 0.28), 0.95)
    mat("rug", (0.48, 0.52, 0.45), 1.0)
    mat("white_gloss", (0.95, 0.95, 0.96), 0.15, coat=0.4)
    mat("worktop", (0.25, 0.25, 0.27), 0.3)
    mat("steel", (0.80, 0.81, 0.83), 0.25, metal=1.0)
    mat("black_plastic", (0.03, 0.03, 0.035), 0.4)
    mat("screen", (0.01, 0.01, 0.015), 0.08, coat=1.0)
    mat("dark_line", (0.08, 0.08, 0.09), 0.6)
    mat("grommet", (0.02, 0.02, 0.02), 0.6)
    # electronics
    mat("pcb_green", (0.05, 0.32, 0.12), 0.35, coat=0.6)
    mat("pcb_blue", (0.04, 0.16, 0.45), 0.35, coat=0.6)
    mat("pcb_purple", (0.22, 0.08, 0.40), 0.35, coat=0.6)
    mat("pcb_black", (0.03, 0.03, 0.04), 0.35, coat=0.6)
    mat("gold", (0.85, 0.65, 0.25), 0.25, metal=1.0)
    mat("chip", (0.02, 0.02, 0.025), 0.35)
    mat("dht_blue", (0.15, 0.45, 0.80), 0.45)
    mat("servo_blue", (0.12, 0.30, 0.75), 0.25, alpha=0.85)
    mat("servo_white", (0.95, 0.95, 0.95), 0.4)
    mat("fan_blade", (0.92, 0.92, 0.94), 0.35)
    mat("terminal_green", (0.10, 0.55, 0.25), 0.4)
    mat("pot_blue", (0.15, 0.35, 0.85), 0.35)
    mat("ir_led_clear", (0.90, 0.92, 0.95), 0.05, alpha=0.6)
    mat("ir_led_dark", (0.05, 0.05, 0.08), 0.1)
    mat("button_cap_red", (0.80, 0.12, 0.10), 0.4)
    mat("button_cap_yellow", (0.92, 0.75, 0.10), 0.4)
    mat("button_cap_blue", (0.12, 0.30, 0.80), 0.4)
    mat("led_off", (0.95, 0.93, 0.80), 0.1, alpha=0.8, emit=0.0, emit_color=(1.0, 0.92, 0.75))
    mat("router_white", (0.93, 0.93, 0.94), 0.35)
    mat("capacitor", (0.08, 0.20, 0.55), 0.3)
    mat("label_living", (0.06, 0.55, 0.55), 0.5)
    mat("label_bedroom", (0.77, 0.48, 0.07), 0.5)
    mat("label_door", (0.76, 0.25, 0.23), 0.5)
    mat("label_hub", (0.42, 0.31, 0.73), 0.5)
    mat("planned_ghost", (0.85, 0.30, 0.28), 0.4, alpha=0.4)
    mat("ethernet", (0.10, 0.35, 0.80), 0.5)
    mat("cable_white", (0.92, 0.92, 0.92), 0.5)
    mat("conduit", (0.12, 0.12, 0.14), 0.6, alpha=0.75)
    # wire colours (signal mode) and node colours
    for n, c in WIRE_COLORS.items():
        mat("wire_" + n, c, 0.45, coat=0.3)
        mat("wire_" + n + "_planned", c, 0.45, alpha=0.35)
    for n, c in NODE_COLORS.items():
        mat("wire_node_" + n, c, 0.45, coat=0.3)
        mat("wire_node_" + n + "_planned", c, 0.45, alpha=0.35)


WIRE_COLORS = {"5V": (0.80, 0.05, 0.04), "GND": (0.02, 0.02, 0.02), "3V3": (0.95, 0.40, 0.04),
               "SDA": (0.05, 0.25, 0.85), "SCL": (0.05, 0.60, 0.15), "SIG": (0.95, 0.80, 0.05),
               "TX": (0.95, 0.95, 0.95), "RX": (0.50, 0.15, 0.75)}
NODE_COLORS = {"living": (0.06, 0.55, 0.55), "bedroom": (0.77, 0.48, 0.07), "door": (0.76, 0.25, 0.23),
               "hub": (0.42, 0.31, 0.73)}


# --------------------------------------------------------------------------------------------- geometry helpers
def _finish(name, bm, material, parent, collection, bevel=0.0, smooth=False):
    if smooth:
        for f in bm.faces:
            f.smooth = True
        for e in bm.edges:
            if e.is_manifold and e.calc_face_angle(0.0) > 0.6:
                e.smooth = False
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    collection.objects.link(ob)
    me.materials.append(MATS[material] if isinstance(material, str) else material)
    if parent is not None:
        ob.parent = parent
    if bevel > 0:
        m = ob.modifiers.new("Bevel", "BEVEL")
        m.width = bevel
        m.segments = 3 if bevel > 0.2 else 2
        m.limit_method = "ANGLE"
        try:
            m.harden_normals = False
        except Exception:
            pass
    return ob


def box(name, x, y, z, w, d, h, material, parent, collection, bevel=0.0):
    """Box from its lower corner (x, y, z) and size (w, d, h), in the parent's local frame (cm)."""
    if w <= 0 or d <= 0 or h <= 0:
        return None
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    for v in bm.verts:
        v.co = Vector((x + (v.co.x + 0.5) * w, y + (v.co.y + 0.5) * d, z + (v.co.z + 0.5) * h))
    return _finish(name, bm, material, parent, collection, bevel)


def cyl(name, c, r, h, material, parent, collection, axis="Z", seg=24, r2=None, bevel=0.0):
    """Cylinder / cone centred at c (x, y, z) along axis, radius r (r2 at the + end), length h (cm)."""
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=seg, radius1=r,
                          radius2=r if r2 is None else r2, depth=h)
    rot = {"Z": Matrix.Identity(3), "X": Matrix.Rotation(math.pi / 2, 3, "Y"),
           "Y": Matrix.Rotation(-math.pi / 2, 3, "X")}[axis]
    for v in bm.verts:
        v.co = rot @ v.co + Vector(c)
    return _finish(name, bm, material, parent, collection, bevel, smooth=True)


def empty(name, loc, collection, parent=None, rot=(0, 0, 0), size=1.0):
    ob = bpy.data.objects.new(name, None)
    ob.empty_display_type = "PLAIN_AXES"
    ob.empty_display_size = size
    ob.location = loc
    ob.rotation_euler = rot
    collection.objects.link(ob)
    if parent is not None:
        ob.parent = parent
    return ob


def poly_curve(name, pts, radius, material, parent, collection):
    cu = bpy.data.curves.new(name, "CURVE")
    cu.dimensions = "3D"
    cu.bevel_depth = radius
    cu.bevel_resolution = 2
    cu.use_fill_caps = True
    sp = cu.splines.new("POLY")
    sp.points.add(len(pts) - 1)
    for p, q in zip(sp.points, pts):
        p.co = (q[0], q[1], q[2], 1.0)
    try:
        cu.bevel_factor_mapping_end = "SPLINE"
    except Exception:
        pass
    ob = bpy.data.objects.new(name, cu)
    ob.data.materials.append(MATS[material])
    collection.objects.link(ob)
    if parent is not None:
        ob.parent = parent
    return ob


def safe(s):
    out = "".join(ch if ch.isalnum() else "_" for ch in s)
    while "__" in out:
        out = out.replace("__", "_")
    return out.strip("_")


# --------------------------------------------------------------------------------------------- house
def build_house(root):
    cw = coll("House/Walls")
    cf = coll("House/Floor")
    walls_root = empty("Walls_root", (0, 0, 0), cw, root)
    # base board, spacers, raised floor
    box("Base_board", -1, -1, -3.5, W + 2, D + 2, 0.5, "base_board", root, cf, bevel=0.1)
    for sx in (10, 30, 50, 70):
        for sy in (10, 22, 45, 55):
            cyl(f"Floor_spacer_{sx}_{sy}", (sx, sy, -1.75), 0.45, 2.5, "spacer", root, cf, seg=6)
    floor = box("Raised_floor", -1, -1, -0.5, W + 2, D + 2, 0.5, "floor", root, cf, bevel=0.05)
    # floor holes (grommets) where each cable / wire bundle goes down
    for k in COMP:
        if k["id"] in CABLE_ROUTES:
            hole = next(q for q in CABLE_ROUTES[k["id"]] if abs(q[2]) < 1e-6)
        elif k["id"] in ROUTES:
            hole = next(b for a, b in zip(ROUTES[k["id"]]["pts"], ROUTES[k["id"]]["pts"][1:])
                        if a[2] > FLOOR_Z and abs(b[2] - FLOOR_Z) < 1e-6)
        else:
            continue
        cyl(f"Floor_hole_{k['id']}", (hole[0], hole[1], 0.02), 0.5, 0.06, "grommet", root, cf, seg=20)
    # walls with openings
    for n, w in enumerate(WALLS):
        horiz = w["a"][1] == w["b"][1]
        s0 = w["a"][0] if horiz else w["a"][1]
        s1 = w["b"][0] if horiz else w["b"][1]

        def piece(p, q, z0, z1, tag, material="foam_white"):
            if horiz:
                return box(f"Wall_{n}_{tag}", p, w["a"][1] - T / 2, z0, q - p, T, z1 - z0, material, walls_root, cw)
            return box(f"Wall_{n}_{tag}", w["a"][0] - T / 2, p, z0, T, q - p, z1 - z0, material, walls_root, cw)

        pos, k = s0, 0
        for op in sorted(w["open"], key=lambda o: o[0]):
            piece(pos, op[0], 0, H, f"part{k}")
            k += 1
            piece(op[0], op[1], 0, op[2], f"sill{k}")
            piece(op[0], op[1], op[3], H, f"lintel{k}")
            if op[4] == "window":
                if horiz:
                    box(f"Window_{n}_{k}", op[0], w["a"][1] - 0.08, op[2], op[1] - op[0], 0.16, op[3] - op[2],
                        "glass", walls_root, cw)
                else:
                    box(f"Window_{n}_{k}", w["a"][0] - 0.08, op[0], op[2], 0.16, op[1] - op[0], op[3] - op[2],
                        "glass", walls_root, cw)
                # thin frame
                if horiz:
                    for zz in (op[2], op[3] - 0.3):
                        box(f"Window_{n}_{k}_frame_{zz}", op[0], w["a"][1] - T / 2 - 0.05, zz, op[1] - op[0],
                            T + 0.1, 0.3, "white_gloss", walls_root, cw)
            else:
                door_leaf(n, k, w, horiz, op, walls_root, cw)
            pos = op[1]
        piece(pos, s1, 0, H, f"part{k}")
    # ceiling beams (they carry the LED lights)
    for bx, by, bw in ((0, 16.2, 50), (50, 16.2, 30), (0, 46.2, 56), (56, 46.2, 24)):
        box(f"Ceiling_beam_{bx}_{by}", bx, by, H - 0.6, bw, 1.6, 0.6, "foam_white", walls_root, cw)
    return walls_root, floor


def door_leaf(n, k, w, horiz, op, parent, collection):
    """Door leaf opened ~80 degrees into the room (north for the south wall and for the y = 34 wall)."""
    width = op[1] - op[0]
    hinge_at_start = not (horiz and w["a"][1] == 34 and op[0] >= 60)   # bathroom door hinges on its east side
    if not horiz:
        return
    hx = op[0] if hinge_at_start else op[1]
    y = w["a"][1] + T / 2
    piv = empty(f"Door_{n}_{k}_hinge", (hx, y, 0), collection, parent, size=1.0)
    ang = math.radians(80 if hinge_at_start else -80)
    piv.rotation_euler = (0, 0, ang)
    x0 = 0 if hinge_at_start else -width + 0.2
    box(f"Door_{n}_{k}_leaf", x0 + (0.1 if hinge_at_start else 0), 0, 0.2, width - 0.3, 0.35, op[3] - 0.4,
        "door_leaf", piv, collection, bevel=0.05)
    hx2 = width - 1.2 if hinge_at_start else -width + 1.4
    cyl(f"Door_{n}_{k}_handle", (hx2, 0.5, op[3] * 0.48), 0.12, 0.5, "steel", piv, collection, axis="Y", seg=12)


# --------------------------------------------------------------------------------------------- furniture
def build_furniture(root):
    items = []
    for i, f in enumerate(FURN):
        c = coll(f"Furniture/{f['r'].capitalize()}")
        e = empty(f"Furniture_{f['r']}_{safe(f['n'])}_{i}", (f["x"], f["y"], 0), c, root, size=2)
        w, d, h, n = f["w"], f["d"], f["h"], f["n"].lower()
        p = e.name
        if n == "sofa":
            sofa(p, w, d, h, e, c)
        elif n == "tv unit":
            tv_unit(p, w, d, h, e, c)
        elif n == "coffee table":
            table(p, w, d, h, e, c, top_mat="wood", legs=0.4, top=0.4)
            box(p + "_rug", -3, -4, 0.01, w + 6, d + 8, 0.08, "rug", e, c, bevel=0.03)
        elif n == "kitchen counter":
            kitchen_counter(p, w, d, h, e, c, faces="west" if w < d else "north")
        elif n == "dining table":
            table(p, w, d, h, e, c, top_mat="wood_light", legs=0.5, top=0.5)
            for cx in (w * 0.25, w * 0.75):
                chair(p + f"_chair_s_{cx:.0f}", cx, -2.4, e, c, face=90)
                chair(p + f"_chair_n_{cx:.0f}", cx, d + 2.4, e, c, face=-90)
        elif n == "bed":
            bed(p, w, d, h, e, c)
        elif n == "nightstand":
            box(p + "_body", 0, 0, 0, w, d, h, "wood", e, c, bevel=0.08)
            box(p + "_drawer_line", 0.3, -0.03, h * 0.55, w - 0.6, 0.05, 0.1, "dark_line", e, c)
            cyl(p + "_knob", (w / 2, -0.15, h * 0.3), 0.18, 0.3, "steel", e, c, axis="Y", seg=12)
            cyl(p + "_lamp_base", (w / 2, d / 2, h + 0.15), 0.6, 0.3, "steel", e, c)
            cyl(p + "_lamp_shade", (w / 2, d / 2, h + 1.6), 1.1, 1.4, "fabric_cushion", e, c, r2=0.7)
        elif n == "desk":
            table(p, w, d, h, e, c, top_mat="wood_light", legs=0.45, top=0.45)
            box(p + "_laptop_base", w * 0.3, d * 0.3, h, 3.2, 2.2, 0.15, "steel", e, c, bevel=0.05)
            lap = empty(p + "_laptop_hinge", (w * 0.3, d * 0.3 + 2.2, h + 0.15), c, e, rot=(math.radians(-15), 0, 0))
            box(p + "_laptop_screen", 0, -0.12, 0, 3.2, 0.12, 2.1, "screen", lap, c, bevel=0.03)
            chair(p + "_chair", w * 0.4, -2.6, e, c, face=90)
        elif n == "wardrobe":
            wardrobe(p, w, d, h, e, c)
        elif n == "shower":
            shower(p, w, d, h, e, c)
        elif n == "toilet":
            toilet(p, w, d, h, e, c)
        elif n == "sink":
            sink(p, w, d, h, e, c)
        else:
            box(p + "_box", 0, 0, 0, w, d, h, "wood", e, c, bevel=0.1)
        items.append(e)
    return items


def table(p, w, d, h, e, c, top_mat, legs, top):
    box(p + "_top", 0, 0, h - top, w, d, top, top_mat, e, c, bevel=0.08)
    for lx, ly in ((0.3, 0.3), (w - 0.3 - legs, 0.3), (0.3, d - 0.3 - legs), (w - 0.3 - legs, d - 0.3 - legs)):
        box(p + f"_leg_{lx:.1f}_{ly:.1f}", lx, ly, 0, legs, legs, h - top, top_mat, e, c, bevel=0.04)


def chair(p, cx, cy, e, c, face=90):
    """Small chair (seat 4 cm high) centred at (cx, cy) in e's frame; face = direction the sitter looks (deg)."""
    ch = empty(p, (cx, cy, 0), c, e, rot=(0, 0, math.radians(face - 90)), size=0.5)
    box(p + "_seat", -1.8, -1.8, 4.0, 3.6, 3.6, 0.4, "wood_light", ch, c, bevel=0.08)
    for lx, ly in ((-1.6, -1.6), (1.2, -1.6), (-1.6, 1.2), (1.2, 1.2)):
        box(p + f"_leg_{lx}_{ly}", lx, ly, 0, 0.35, 0.35, 4.0, "wood_light", ch, c)
    box(p + "_back", -1.8, -1.8, 4.4, 3.6, 0.4, 4.0, "wood_light", ch, c, bevel=0.08)


def sofa(p, w, d, h, e, c):
    # long along y, back on the east side, faces west (the TV)
    seat_h = h * 0.5
    box(p + "_base", 0, 0, 0.3, w, d, seat_h - 0.3, "fabric_sofa", e, c, bevel=0.3)
    for i in range(3):
        y0 = 1.2 + i * (d - 2.4) / 3
        box(p + f"_seat_cushion_{i}", 0.1, y0 + 0.05, seat_h, w - 1.9, (d - 2.4) / 3 - 0.1, 0.8, "fabric_sofa", e, c,
            bevel=0.3)
        box(p + f"_back_cushion_{i}", w - 2.6, y0 + 0.1, seat_h + 0.5, 1.0, (d - 2.4) / 3 - 0.2, h - seat_h - 0.6,
            "fabric_sofa", e, c, bevel=0.35)
    box(p + "_back", w - 1.7, 0, 0.3, 1.7, d, h - 0.3, "fabric_sofa", e, c, bevel=0.35)
    for y0 in (0, d - 1.2):
        box(p + f"_arm_{y0:.0f}", 0, y0, 0.3, w, 1.2, h * 0.72, "fabric_sofa", e, c, bevel=0.35)
    box(p + "_pillow_a", w - 3.5, 1.6, seat_h + 0.7, 1.1, 2.8, 2.6, "fabric_cushion", e, c, bevel=0.45)
    box(p + "_pillow_b", w - 3.5, d - 4.4, seat_h + 0.7, 1.1, 2.8, 2.6, "fabric_cushion", e, c, bevel=0.45)
    for lx, ly in ((0.4, 0.4), (w - 0.8, 0.4), (0.4, d - 0.8), (w - 0.8, d - 0.8)):
        cyl(p + f"_foot_{lx}_{ly}", (lx + 0.2, ly + 0.2, 0.15), 0.2, 0.3, "wood", e, c, seg=10)


def tv_unit(p, w, d, h, e, c):
    body = 2.0
    box(p + "_cabinet", 0, 0, 0, w, d, body, "wood", e, c, bevel=0.06)
    for i in range(1, 4):
        box(p + f"_door_line_{i}", w - 0.02, i * d / 4 - 0.05, 0.2, 0.05, 0.1, body - 0.4, "dark_line", e, c)
    box(p + "_tv_stand", w * 0.35, d / 2 - 1.5, body, 0.8, 3.0, 0.2, "black_plastic", e, c)
    box(p + "_tv_neck", w * 0.45, d / 2 - 0.3, body, 0.25, 0.6, 0.8, "black_plastic", e, c)
    box(p + "_tv_panel", w * 0.5, 0.5, body + 0.6, 0.3, d - 1.0, h - body - 0.6, "black_plastic", e, c, bevel=0.05)
    box(p + "_tv_screen", w * 0.5 + 0.29, 0.75, body + 0.85, 0.03, d - 1.5, h - body - 1.1, "screen", e, c)


def kitchen_counter(p, w, d, h, e, c, faces):
    top = 0.6
    box(p + "_cabinets", 0, 0, 0.5, w, d, h - top - 0.5, "white_gloss", e, c, bevel=0.04)
    if faces == "west":
        box(p + "_plinth", 0.4, 0, 0, w - 0.4, d, 0.5, "dark_line", e, c)
        box(p + "_worktop", -0.3, 0, h - top, w + 0.3, d, top, "worktop", e, c, bevel=0.05)
        n = max(1, round(d / 5))
        for i in range(n + 1):
            box(p + f"_door_gap_{i}", -0.03, i * d / n - 0.04, 0.6, 0.05, 0.08, h - top - 0.7, "dark_line", e, c)
            if i < n:
                box(p + f"_handle_{i}", -0.2, i * d / n + d / n / 2 - 1.0, h - top - 1.2, 0.2, 2.0, 0.2, "steel", e, c)
        # sink + tap near the north end
        box(p + "_sink", 1.0, d - 7.5, h - 0.15, w - 2.2, 4.5, 0.2, "steel", e, c, bevel=0.15)
        box(p + "_sink_basin", 1.4, d - 7.1, h - 0.17, w - 3.0, 3.7, 0.05, "dark_line", e, c)
        cyl(p + "_tap", (w - 0.7, d - 5.25, h + 1.0), 0.15, 2.0, "steel", e, c, seg=12)
        cyl(p + "_tap_spout", (w - 1.3, d - 5.25, h + 1.9), 0.12, 1.2, "steel", e, c, axis="X", seg=12)
        # fridge-like tall unit is not in the data; keep the counter only
    else:
        box(p + "_plinth", 0, 0, 0, w, d - 0.4, 0.5, "dark_line", e, c)
        box(p + "_worktop", 0, 0, h - top, w, d + 0.3, top, "worktop", e, c, bevel=0.05)
        n = max(1, round(w / 5))
        for i in range(n + 1):
            box(p + f"_door_gap_{i}", i * w / n - 0.04, d - 0.02, 0.6, 0.08, 0.05, h - top - 0.7, "dark_line", e, c)
            if i < n:
                box(p + f"_handle_{i}", i * w / n + w / n / 2 - 1.0, d, h - top - 1.2, 2.0, 0.2, 0.2, "steel", e, c)
        # stove with 4 burners
        box(p + "_hob", 1.5, 1.0, h, 6.0, 4.0, 0.08, "screen", e, c, bevel=0.05)
        for bx in (3.0, 6.0):
            for by in (2.0, 4.0):
                cyl(p + f"_burner_{bx}_{by}", (bx, by, h + 0.1), 0.75, 0.04, "dark_line", e, c, seg=24)
        box(p + "_oven_door", 1.6, d - 0.05, 1.0, 5.8, 0.1, 5.5, "screen", e, c, bevel=0.05)


def bed(p, w, d, h, e, c):
    # headboard on the north side
    frame_h = 2.0
    box(p + "_frame", 0, 0, 0.4, w, d - 0.8, frame_h - 0.4, "wood", e, c, bevel=0.1)
    for lx, ly in ((0.3, 0.3), (w - 0.9, 0.3)):
        box(p + f"_foot_{lx}", lx, ly, 0, 0.6, 0.6, 0.4, "wood", e, c)
    box(p + "_mattress", 0.3, 0.3, frame_h, w - 0.6, d - 1.4, 1.6, "fabric_bed", e, c, bevel=0.4)
    box(p + "_headboard", 0, d - 0.8, 0, w, 0.8, h + 1.5, "wood", e, c, bevel=0.15)
    for px in (1.0, w / 2 + 0.4):
        box(p + f"_pillow_{px:.0f}", px, d - 4.2, frame_h + 1.6, w / 2 - 1.4, 3.0, 1.0, "fabric_bed", e, c, bevel=0.45)
    box(p + "_blanket", 0.15, 0.15, frame_h + 0.9, w - 0.3, d * 0.62, 0.9, "fabric_blanket", e, c, bevel=0.3)
    box(p + "_blanket_fold", 0.15, d * 0.62 - 1.2, frame_h + 1.5, w - 0.3, 1.6, 0.4, "fabric_bed", e, c, bevel=0.15)


def wardrobe(p, w, d, h, e, c):
    box(p + "_body", 0, 0, 0, w, d, h, "wood_light", e, c, bevel=0.08)
    n = 3
    for i in range(1, n):
        box(p + f"_door_gap_{i}", i * w / n - 0.04, -0.02, 0.4, 0.08, 0.05, h - 0.8, "dark_line", e, c)
    for i in range(n):
        hx = i * w / n + (w / n - 0.6 if i % 2 == 0 else 0.4)
        box(p + f"_handle_{i}", hx, -0.25, h * 0.45, 0.2, 0.25, 2.5, "steel", e, c)
    box(p + "_plinth", 0.3, 0.2, -0.01, w - 0.6, d - 0.4, 0.5, "dark_line", e, c)


def shower(p, w, d, h, e, c):
    box(p + "_tray", 0, 0, 0, w, d, h, "white_gloss", e, c, bevel=0.1)
    cyl(p + "_drain", (w / 2, d / 2, h + 0.01), 0.5, 0.04, "steel", e, c, seg=20)
    box(p + "_glass_west", 0, 0, h, 0.15, d, 18, "glass", e, c)
    box(p + "_glass_south", 0, 0, h, w * 0.55, 0.15, 18, "glass", e, c)
    box(p + "_glass_rail", 0, 0, h + 18, w, 0.25, 0.25, "steel", e, c)
    cyl(p + "_pipe", (w / 2, d - 0.3, h + 10), 0.15, 18, "steel", e, c, seg=12)
    cyl(p + "_head", (w / 2, d - 1.6, h + 18.5), 1.0, 0.25, "steel", e, c, seg=24)
    box(p + "_arm", w / 2 - 0.12, d - 1.6, h + 18.6, 0.24, 1.4, 0.24, "steel", e, c)


def toilet(p, w, d, h, e, c):
    cyl(p + "_bowl_base", (w / 2, d * 0.42, 1.2), 1.2, 2.4, "white_gloss", e, c, r2=1.6, seg=32)
    bowl = cyl(p + "_bowl", (w / 2, d * 0.42, 2.7), 1.8, 0.6, "white_gloss", e, c, seg=32, bevel=0.1)
    bowl.scale = (1.0, 1.3, 1.0)
    seat = cyl(p + "_seat", (w / 2, d * 0.42, 3.1), 1.85, 0.2, "white_gloss", e, c, seg=32)
    seat.scale = (1.0, 1.3, 1.0)
    box(p + "_tank", 0.2, d - 1.6, 2.2, w - 0.4, 1.5, h + 1.6 - 2.2, "white_gloss", e, c, bevel=0.2)
    cyl(p + "_flush", (w / 2, d - 0.85, h + 1.65), 0.3, 0.1, "steel", e, c, seg=16)


def sink(p, w, d, h, e, c):
    box(p + "_vanity", 0.2, 0.2, 0, w - 0.2, d - 0.2, h - 1.0, "wood_light", e, c, bevel=0.06)
    box(p + "_vanity_gap", w / 2 - 0.04, 0.17, 0.4, 0.08, 0.05, h - 1.8, "dark_line", e, c)
    box(p + "_basin", 0, 0, h - 1.0, w, d, 1.0, "white_gloss", e, c, bevel=0.3)
    box(p + "_basin_hole", 0.8, 0.8, h - 0.05, w - 1.8, d - 1.6, 0.06, "dark_line", e, c, bevel=0.2)
    cyl(p + "_tap", (w - 0.6, d / 2, h + 0.8), 0.15, 1.6, "steel", e, c, seg=12)
    cyl(p + "_tap_spout", (w - 1.1, d / 2, h + 1.5), 0.12, 1.0, "steel", e, c, axis="X", seg=12)
    box(p + "_mirror", w - 0.2 + 0.25, 0.3, h + 3.0, 0.08, d - 0.6, 7.0, "steel", e, c)


# --------------------------------------------------------------------------------------------- parts (modules)
def conductors(k):
    """The wires of one part, in the order they leave it (role names map to WIRE_COLORS)."""
    p = k["p"].lower()
    if "ld2410" in p:
        return ["5V", "GND", "TX", "RX"]
    if "dht11" in p:
        return ["3V3", "GND", "SIG"]
    if "bh1750" in p or "tsl2561" in p:
        return ["3V3", "GND", "SDA", "SCL"]
    if "led" in p:
        return ["SIG", "GND"]
    if "sg90" in p:
        return ["5V", "GND", "SIG"]
    if "fc-51" in p:
        return ["3V3", "GND", "SIG"]
    if "r503" in p:
        return ["3V3", "GND", "TX", "RX"] + ["SIG"] * 8          # fingerprint (4) + 4x4 keypad (8 lines)
    if "exit button" in p:
        return ["SIG", "GND", "SIG"]
    if "button" in p:
        n = p.split(":", 1)[1].count("/") + 1 if ":" in p else 1
        return ["SIG"] * n + ["GND"]
    return ["SIG", "GND"]


def mount(k):
    """Origin (on the mounting surface) and rotation of a part. Local frame of every module:
    the board stands in the local XZ plane, its front (sensor side) looks along local +Y."""
    x, y, z = k["x"], k["y"], k["z"]
    p = k["p"].lower()
    rz = lambda deg: (0, 0, math.radians(deg))
    if k["r"] == "hub":
        return (x, y, -3.1), (math.radians(90), 0, 0), "flat"           # lies on the hub floor, front up
    if "washer" in p:
        return (x, y, 0), rz(-90), "floor"
    if "fc-51" in p and ("door" in p):
        return (x, y, z), rz(-90), "jamb"                               # looks east across the doorway
    if z >= 20:
        return (x, y, H - 0.6), (math.radians(-90), 0, 0), "ceiling"    # under the ceiling beam, front down
    if x < 1:
        return (T / 2, y, z), rz(-90), "wall"
    if 33 < y < 34:
        return (x, 34 - T / 2, z), rz(180), "wall"
    if 34 < y < 35:
        return (x, 34 + T / 2, z), rz(0), "wall"
    if 55 < x < 56:
        return (56 - T / 2, y, z), rz(90), "wall"
    if 0 < y < 1:
        return (x, T / 2, z), rz(0), "wall"
    if y < 0:
        return (x, -T / 2, z), rz(180), "wall"
    return (x, y, z), rz(0), "bracket"


def pcb(p, w, h, material, e, c, y0=0.0, t=0.16, z0=0.0, x0=None):
    x0 = -w / 2 if x0 is None else x0
    return box(p + "_pcb", x0, y0, z0, w, t, h, material, e, c, bevel=0.02)


def pins(p, n, x0, z, e, c, y0=0.16, pitch=0.254, length=0.6, down=True):
    """Male header along local X at height z, pins pointing down (-Z)."""
    box(p + "_header", x0 - 0.12, y0, z - 0.12, n * pitch, 0.25, 0.25, "black_plastic", e, c)
    for i in range(n):
        box(p + f"_pin_{i}", x0 + i * pitch - 0.03, y0 + 0.095, z - (length if down else -0.1), 0.06, 0.06, length,
            "gold", e, c)


def part_ld2410c(p, e, c):
    pcb(p, 2.2, 1.6, "pcb_black", e, c, z0=-0.8)
    for i, (ax, az) in enumerate(((-0.55, 0.25), (0.05, 0.25), (-0.55, -0.35), (0.05, -0.35))):
        box(p + f"_antenna_{i}", ax, 0.16, az, 0.42, 0.02, 0.42, "gold", e, c)
    box(p + "_chip", 0.55, 0.16, -0.25, 0.4, 0.08, 0.4, "chip", e, c)
    pins(p, 5, -0.5, -0.8, e, c, pitch=0.254)


def part_dht11(p, e, c):
    pcb(p, 1.5, 2.8, "pcb_blue", e, c, z0=-1.6)
    box(p + "_body", -0.6, 0.16, -0.5, 1.2, 0.55, 1.55, "dht_blue", e, c, bevel=0.05)
    for i in range(5):
        box(p + f"_grille_{i}", -0.5, 0.7, -0.35 + i * 0.27, 1.0, 0.03, 0.08, "dark_line", e, c)
    pins(p, 3, -0.25, -1.6, e, c)


def part_light_sensor(p, e, c, material, w=1.85, h=1.35, chip=0.3):
    pcb(p, w, h, material, e, c, z0=-h / 2)
    box(p + "_sensor", -chip / 2, 0.16, 0.0, chip, 0.12, chip, "chip", e, c)
    box(p + "_window", -chip / 4, 0.27, chip / 4, chip / 2, 0.02, chip / 2, "ir_led_clear", e, c)
    for hx in (-w / 2 + 0.25, w / 2 - 0.25):
        cyl(p + f"_hole_{hx:.1f}", (hx, 0.08, h / 2 - 0.25), 0.15, 0.18, "gold", e, c, axis="Y", seg=12)
    pins(p, 5, -0.5, -h / 2, e, c)


def part_led(p, e, c, k):
    cyl(p + "_holder", (0, 0.3, 0), 0.75, 0.6, "white_gloss", e, c, axis="Y", seg=32, bevel=0.1)
    led = cyl(p + "_bulb", (0, 0.75, 0), 0.25, 0.5, "led_" + str(k["id"]), e, c, axis="Y", seg=20)
    return led


def part_sg90(p, e, c, holder=True):
    """SG90 standing with its shaft up; body 2.25 x 1.18 x 2.27, tabs 3.2 wide."""
    box(p + "_body", -1.125, 0, 0, 2.25, 1.18, 2.27, "servo_blue", e, c, bevel=0.05)
    box(p + "_tabs", -1.6, 0, 1.55, 3.2, 1.18, 0.25, "servo_blue", e, c, bevel=0.03)
    cyl(p + "_top", (-0.4, 0.59, 2.45), 0.55, 0.35, "servo_blue", e, c, seg=24)
    cyl(p + "_shaft", (-0.4, 0.59, 2.75), 0.22, 0.3, "servo_white", e, c, seg=16)
    box(p + "_horn", -0.55, 0.39, 2.85, 1.9, 0.4, 0.15, "servo_white", e, c, bevel=0.1)
    cyl(p + "_cable", (0.9, 0.59, 0.5), 0.12, 0.8, "dark_line", e, c, axis="X", seg=8)


def part_fan(p, e, c):
    part_sg90(p, e, c)
    head = empty(p + "_fan_head", (-0.4, 0.59, 3.0), c, e, size=0.3)
    cyl(p + "_fan_motor", (0, 0, 0.8), 0.6, 1.6, "black_plastic", head, c, seg=20)
    hub = empty(p + "_fan_hub", (0, 0.7, 1.4), c, head, size=0.2)
    cyl(p + "_fan_spinner", (0, 0.1, 0), 0.35, 0.3, "fan_blade", hub, c, axis="Y", seg=16)
    for i in range(3):
        bl = empty(p + f"_blade_pivot_{i}", (0, 0.1, 0), c, hub, rot=(0, math.radians(i * 120), 0), size=0.1)
        b = box(p + f"_blade_{i}", -0.45, -0.05, 0.25, 0.9, 0.1, 2.0, "fan_blade", bl, c, bevel=0.08)
        b.rotation_euler = (0, math.radians(12), 0)
    cage = cyl(p + "_cage", (0, 0.8, 1.4), 2.5, 0.05, "steel", head, c, axis="Y", seg=32)
    cage.display_type = "WIRE"


def part_buttons(p, e, c, n, extra_buzzer=False):
    w = 1.6 * n + 0.6 + (1.6 if extra_buzzer else 0)
    box(p + "_plate", -w / 2, 0, -1.0, w, 0.25, 2.0, "white_gloss", e, c, bevel=0.08)
    caps = ["button_cap_yellow", "button_cap_blue", "button_cap_red"]
    for i in range(n):
        cx = -w / 2 + 1.1 + i * 1.6
        box(p + f"_switch_{i}", cx - 0.6, 0.25, -0.6, 1.2, 0.35, 1.2, "black_plastic", e, c, bevel=0.05)
        cyl(p + f"_cap_{i}", (cx, 0.75, 0), 0.4, 0.3, caps[i % 3], e, c, axis="Y", seg=20)
    if extra_buzzer:
        cyl(p + "_buzzer", (w / 2 - 0.9, 0.7, 0), 0.6, 0.9, "black_plastic", e, c, axis="Y", seg=24)


def part_fc51(p, e, c):
    pcb(p, 3.1, 1.4, "pcb_blue", e, c, z0=-0.7)
    cyl(p + "_ir_tx", (1.25, 0.6, 0.3), 0.25, 0.8, "ir_led_clear", e, c, axis="Y", seg=16)
    cyl(p + "_ir_rx", (1.25, 0.6, -0.3), 0.25, 0.8, "ir_led_dark", e, c, axis="Y", seg=16)
    box(p + "_pot", -0.2, 0.16, -0.35, 0.7, 0.45, 0.7, "pot_blue", e, c, bevel=0.03)
    cyl(p + "_pot_screw", (0.15, 0.65, 0), 0.2, 0.06, "servo_white", e, c, axis="Y", seg=12)
    box(p + "_chip", -1.0, 0.16, -0.3, 0.5, 0.1, 0.6, "chip", e, c)
    pins(p, 3, -1.45, -0.7, e, c)


def part_washer(p, e, c):
    # washer box 6 x 6 x 8 (local: width x along the wall, front +Y)
    box(p + "_cabinet", -3, 0.3, 0, 6, 6, 8, "white_gloss", e, c, bevel=0.25)
    cyl(p + "_door_ring", (0, 6.35, 3.8), 2.0, 0.25, "steel", e, c, axis="Y", seg=32)
    cyl(p + "_door_glass", (0, 6.45, 3.8), 1.6, 0.15, "screen", e, c, axis="Y", seg=32)
    box(p + "_panel", -2.8, 6.25, 6.6, 5.6, 0.1, 1.1, "black_plastic", e, c)
    cyl(p + "_knob", (1.8, 6.4, 7.15), 0.35, 0.25, "steel", e, c, axis="Y", seg=16)
    sv = empty(p + "_servo_mount", (0, 2.5, 8.0), c, e, size=0.4)
    part_sg90(p + "_servo", sv, c)


def part_door_lock(p, e, c):
    box(p + "_bracket", -2.0, 0, -1.6, 4.0, 0.3, 3.2, "steel", e, c, bevel=0.03)
    sv = empty(p + "_servo_mount", (0, 0.3, -1.2), c, e, rot=(0, 0, 0), size=0.3)
    part_sg90(p + "_servo", sv, c)
    box(p + "_bolt", -0.3, 0.6, 1.8, 3.5, 0.5, 0.5, "steel", e, c, bevel=0.05)


def part_keypad(p, e, c):
    box(p + "_keypad", -3.45, 0, -4.0, 6.9, 0.12, 7.6, "black_plastic", e, c, bevel=0.05)
    for r in range(4):
        for col in range(4):
            box(p + f"_key_{r}_{col}", -3.0 + col * 1.6, 0.12, -3.6 + r * 1.75, 1.3, 0.06, 1.4, "white_gloss", e, c,
                bevel=0.05)
    cyl(p + "_r503_ring", (0, 0.3, 5.2), 1.15, 0.6, "steel", e, c, axis="Y", seg=32)
    cyl(p + "_r503_sensor", (0, 0.62, 5.2), 0.8, 0.05, "pcb_blue", e, c, axis="Y", seg=32)


def part_pi5(p, e, c):
    # flat on the hub floor: local XZ is the floor plane after the mount rotation (local +Y = up)
    for sx, sz in ((-3.9, -2.4), (3.9, -2.4), (-3.9, 2.4), (3.9, 2.4)):
        cyl(p + f"_standoff_{sx}_{sz}", (sx, 0.4, sz), 0.2, 0.8, "gold", e, c, axis="Y", seg=8)
    pcb(p, 8.5, 5.6, "pcb_green", e, c, y0=0.8, t=0.16, z0=-2.8)
    y = 0.96
    box(p + "_soc_shield", -1.6, y, -1.4, 1.5, 0.15, 1.5, "steel", e, c)
    box(p + "_ram", 0.2, y, -1.4, 1.2, 0.12, 1.0, "chip", e, c)
    box(p + "_cooler", -2.6, y + 0.4, -2.2, 4.0, 0.8, 3.4, "black_plastic", e, c, bevel=0.05)
    cyl(p + "_cooler_fan", (-0.6, y + 1.22, -0.5), 1.2, 0.05, "dark_line", e, c, axis="Y", seg=32)
    box(p + "_gpio", -3.6, y, 2.15, 5.1, 0.85, 0.5, "black_plastic", e, c)
    for i in range(20):
        for j in range(2):
            box(p + f"_gpio_pin_{i}_{j}", -3.5 + i * 0.254, y + 0.85, 2.25 + j * 0.254, 0.06, 0.4, 0.06, "gold", e, c)
    box(p + "_usb_a_1", 2.6, y, -2.7, 1.7, 1.6, 1.4, "steel", e, c)
    box(p + "_usb_a_2", 2.6, y, -1.0, 1.7, 1.6, 1.4, "steel", e, c)
    box(p + "_ethernet", 2.6, y, 0.6, 1.7, 1.35, 1.6, "steel", e, c)
    box(p + "_usb_c", -3.5, y, -2.9, 0.9, 0.35, 0.7, "steel", e, c)
    for i in range(2):
        box(p + f"_micro_hdmi_{i}", -2.2 + i * 1.3, y, -2.9, 0.7, 0.3, 0.6, "steel", e, c)
    # 27 W supply brick beside it
    box(p + "_supply_27W", -4.2, 0, -5.8, 3.0, 2.4, 2.3, "white_gloss", e, c, bevel=0.2)


def part_router(p, e, c):
    box(p + "_body", -4.0, 0, -3.0, 8.0, 1.6, 6.0, "router_white", e, c, bevel=0.3)
    for i in range(4):
        cyl(p + f"_led_{i}", (-2.5 + i * 0.8, 1.62, -2.4), 0.1, 0.04, "led_status", e, c, axis="Y", seg=12)
    for i, ax in enumerate((-3.0, 3.0)):
        piv = empty(p + f"_antenna_pivot_{i}", (ax, 1.0, 3.0), c, e, rot=(math.radians(-20), 0, 0), size=0.2)
        cyl(p + f"_antenna_{i}", (0, 3.0, 0), 0.3, 6.0, "black_plastic", piv, c, axis="Y", seg=16)


def part_esp32(p, e, c, node, planned):
    # expansion board (black, screw terminals) with the DevKit plugged in
    pcb(p + "_expansion", 8.0, 6.0, "pcb_black", e, c, y0=0.6, t=0.16, z0=-3.0)
    for sx, sz in ((-3.6, -2.6), (3.6, -2.6), (-3.6, 2.6), (3.6, 2.6)):
        cyl(p + f"_standoff_{sx}_{sz}", (sx, 0.3, sz), 0.2, 0.6, "gold", e, c, axis="Y", seg=8)
    y = 0.76
    for row, zz in (("a", -2.75), ("b", 2.15)):
        for i in range(8):
            box(p + f"_terminal_{row}_{i}", -3.8 + i * 0.95, y, zz, 0.9, 0.8, 0.6, "terminal_green", e, c)
    for zz in (-1.6, 1.0):
        box(p + f"_socket_{zz}", -2.75, y, zz, 5.5, 0.85, 0.3, "black_plastic", e, c)
    dk = y + 0.85
    box(p + "_devkit_pcb", -2.75, dk, -1.4, 5.5, 0.16, 2.8, "pcb_black", e, c, bevel=0.02)
    box(p + "_wroom_pcb", -0.9, dk + 0.16, -1.25, 2.55, 0.08, 1.8, "pcb_blue", e, c)
    box(p + "_wroom_shield", -0.75, dk + 0.24, -1.1, 1.75, 0.3, 1.5, "steel", e, c)
    box(p + "_antenna_trace", 1.05, dk + 0.24, -1.1, 0.5, 0.02, 1.5, "gold", e, c)
    box(p + "_usb", -2.95, dk + 0.16, -0.4, 0.6, 0.3, 0.8, "steel", e, c)
    for i, bx in enumerate((-2.2, -1.6)):
        box(p + f"_button_{i}", bx, dk + 0.16, -1.3, 0.35, 0.25, 0.35, "black_plastic", e, c)
    box(p + "_label", -3.9, y, 2.95, 7.8, 0.05, 0.25, "label_" + node, e, c)
    if planned:
        for ob in list(e.children_recursive):
            if ob.type == "MESH":
                ob.data.materials[0] = MATS["planned_ghost"]


def part_power(p, e, c):
    box(p + "_supply_5V", -3.8, 0, -2.8, 4.0, 2.4, 2.4, "black_plastic", e, c, bevel=0.2)
    box(p + "_supply_label", -3.2, 2.41, -2.2, 2.8, 0.02, 1.2, "white_gloss", e, c)
    cyl(p + "_capacitor", (1.0, 0.65, -2.0), 0.5, 1.3, "capacitor", e, c, axis="Y", seg=24)
    cyl(p + "_cap_top", (1.0, 1.31, -2.0), 0.48, 0.02, "steel", e, c, axis="Y", seg=24)
    for i in range(3):
        pcb(p + f"_ina226_{i}", 2.3, 1.6, "pcb_purple", e, c, y0=0.1, t=0.16, z0=0.4, x0=-3.8 + i * 2.6)
        box(p + f"_ina226_{i}_shunt", -3.4 + i * 2.6, 0.26, 1.0, 0.8, 0.12, 0.4, "chip", e, c)
        box(p + f"_ina226_{i}_terminal", -2.6 + i * 2.6, 0.26, 1.3, 0.9, 0.7, 0.6, "terminal_green", e, c)


LED_MATS = []
LED_LIGHTS = []


def build_parts(root):
    items = []
    mat("led_status", (0.2, 1.0, 0.3), 0.3, emit=3.0)
    for k in COMP:
        node, planned = k["node"], k["st"] == "planned"
        node_name = {"living": "Living node", "bedroom": "Bedroom node", "door": "Door node (later)",
                     "hub": "Hub"}[node]
        c = coll(f"Parts/{node_name}") if k["r"] != "hub" else coll("Hub/Modules")
        loc, rot, kind = mount(k)
        name = f"Part_{k['id']:02d}_{safe(k['p'])}"
        e = empty(name, loc, c, root, rot=rot, size=1.5)
        e["part_id"], e["node"], e["status"], e["note"] = k["id"], node, k["st"], k.get("note", "")
        p = name
        lp = k["p"].lower()
        if k["r"] == "hub" and k["x"] < HUB["x"] + 6:                   # keep clear of the hub's west wall
            e = empty(p + "_offset", (0.5, 0, 0), c, e, size=0.1)
        if kind == "bracket":                                           # small wall shelf under fans
            sh = empty(p + "_shelf", (0, 0, 0), c, e, size=0.3)
            box(p + "_shelf_plate", -2.2, -2.2, -0.3, 4.4, 4.4, 0.3, "foam_white", sh, c, bevel=0.05)
        if "ld2410" in lp:
            part_ld2410c(p, e, c)
        elif "dht11" in lp:
            part_dht11(p, e, c)
        elif "bh1750" in lp:
            part_light_sensor(p, e, c, "pcb_blue")
        elif "tsl2561" in lp:
            part_light_sensor(p, e, c, "pcb_black", w=1.9, h=1.9, chip=0.25)
        elif "led" in lp:
            m = mat("led_" + str(k["id"]), (1.0, 0.95, 0.82), 0.1, alpha=0.9, emit=0.0, emit_color=(1.0, 0.9, 0.7))
            LED_MATS.append(m)
            part_led(p, e, c, k)
        elif "fan" in lp:
            part_fan(p, e, c)
        elif "washer" in lp:
            part_washer(p, e, c)
        elif "door lock" in lp:
            part_door_lock(p, e, c)
        elif "r503" in lp:
            part_keypad(p, e, c)
        elif "exit button" in lp:
            part_buttons(p, e, c, 1, extra_buzzer=True)
        elif "button" in lp:
            part_buttons(p, e, c, len(conductors(k)) - 1)
        elif "fc-51" in lp:
            part_fc51(p, e, c)
        elif "raspberry" in lp:
            part_pi5(p, e, c)
        elif "router" in lp:
            part_router(p, e, c)
        elif "esp32" in lp:
            part_esp32(p, e, c, node, planned)
        elif "supply" in lp:
            part_power(p, e, c)
        else:
            box(p + "_box", -1.2, 0, -1.2, 2.4, 2.4, 2.4, "label_" + node, e, c)
        if planned:
            for ob in bpy.data.objects[name].children_recursive:
                if ob.type == "MESH" and ob.data.materials and ob.data.materials[0].name != "planned_ghost":
                    ob.data.materials[0] = MATS["planned_ghost"]
        items.append(bpy.data.objects[name])
    return items


# --------------------------------------------------------------------------------------------- hub box
def build_hub(root):
    c = coll("Hub/Box")
    e = empty("Hub_box", (HUB["x"], HUB["y"], 0), c, root, size=2)
    box("Hub_base", 0, 0, -3.5, HUB["w"], HUB["d"], 0.4, "base_board", e, c, bevel=0.05)
    box("Hub_acrylic_floor", 0.1, 0.1, -3.1, HUB["w"] - 0.2, HUB["d"] - 0.2, 0.05, "acrylic", e, c)
    t = 0.3
    for nm, x, y, w, d in (("south", 0, 0, HUB["w"], t), ("north", 0, HUB["d"] - t, HUB["w"], t),
                           ("west", 0, t, t, HUB["d"] - 2 * t), ("east", HUB["w"] - t, t, t, HUB["d"] - 2 * t)):
        box(f"Hub_acrylic_{nm}", x, y, -3.1, w, d, HUB["h"], "acrylic", e, c, bevel=0.03)
    box("Hub_acrylic_lid", 0, 0, -3.1 + HUB["h"], HUB["w"], HUB["d"], t, "acrylic", e, c, bevel=0.03)
    # cable duct from the house's east side to the hub (under-floor level)
    box("Cable_duct_east", 80 - HUB["x"] - 0.5, 30.4 - HUB["y"], -2.6, HUB["x"] - 80 + 0.8, 2.2, 2.0, "conduit", e,
        c, bevel=0.1)
    # cables inside the hub: Ethernet Pi <-> router, 5V to the ESP32 boards, USB-C to the Pi
    cw = coll("Wires/Hub cables")
    poly_curve("Cable_ethernet_Pi_router", [(92.9, 36.6, -2.4), (92.9, 36.6, -0.4), (93.4, 39.5, -0.4),
                                            (93.4, 39.5, -1.2)], 0.22, "ethernet", root, cw)
    for i, (tx, ty) in enumerate(((88, 27.2), (97, 27.2))):
        for j, role in enumerate(("5V", "GND")):
            o = j * 0.18
            poly_curve(f"Cable_5V_to_ESP32_{i}_{role}", [(95.5 + o, 22.5 + o, -1.0), (95.5 + o, 26.0 + o, -1.0),
                                                       (tx + 1.5 + o, 26.0 + o, -1.0), (tx + 1.5 + o, ty + o, -1.6)],
                       WIRE_R * 1.6, "wire_" + role, root, cw)
    poly_curve("Cable_USB_C_Pi", [(85.6, 41.4, -2.0), (85.6, 41.1, -1.4), (85.5, 40.8, -2.0)], 0.2, "cable_white",
               root, cw)
    return e


# --------------------------------------------------------------------------------------------- wires
CABLE_R = 0.12                  # one sheathed cable per part, radius cm (about 2.4 mm)
# one distinct colour per part cable (in part order); names are for the legend (cables.md)
CABLE_PALETTE = [
    ("Red", (0.85, 0.08, 0.08)), ("Orange", (1.0, 0.45, 0.0)), ("Yellow", (1.0, 0.85, 0.05)),
    ("Lime", (0.55, 0.90, 0.10)), ("Green", (0.08, 0.55, 0.18)), ("Teal", (0.0, 0.50, 0.48)),
    ("Cyan", (0.10, 0.80, 0.92)), ("Sky blue", (0.40, 0.62, 1.0)), ("Blue", (0.08, 0.22, 0.90)),
    ("Navy", (0.06, 0.06, 0.40)), ("Purple", (0.50, 0.18, 0.80)), ("Magenta", (0.90, 0.08, 0.70)),
    ("Pink", (1.0, 0.55, 0.72)), ("Brown", (0.45, 0.24, 0.08)), ("Beige", (0.88, 0.78, 0.56)),
    ("Grey", (0.50, 0.50, 0.52)), ("White", (0.96, 0.96, 0.96)), ("Olive", (0.45, 0.50, 0.10)),
    ("Maroon", (0.45, 0.04, 0.10)), ("Gold", (0.85, 0.65, 0.15)), ("Salmon", (1.0, 0.50, 0.40)),
]
CABLES = []                     # legend rows filled by build_cables()
CABLE_ROUTES = {}               # part id -> cable polyline, filled by cable_routes() in main()


def esp_of(node):
    return next((c for c in COMP if c["r"] == "hub" and c["node"] == node and "esp32" in c["p"].lower()), None)


def cable_routes():
    """Tidy layout for one cable per part, with the page's topology (part -> its floor hole -> under the floor to
    the node's trunk -> out of the east wall -> its ESP32) but its own lane and height for every cable, ordered so
    the cables of one layer never cross. Layers (z under the floor): living from the south / from the north,
    bedroom (two layers), door node (later)."""
    groups = {}
    used = set()
    for k in COMP:
        r = ROUTES.get(k["id"])
        if not r:
            continue
        pts = [list(q) for q in r["pts"]]
        i_d = next(i for i, q in enumerate(pts) if abs(q[2] - FLOOR_Z) < 1e-6)
        # two parts going down at the same spot (a light's wire reaches the wall where a radar hangs): own hole,
        # 1.2 cm further along the wall
        while (round(pts[i_d][0], 1), round(pts[i_d][1], 1)) in used:
            along_y = pts[i_d][0] < 1 or pts[i_d][0] > W - 1
            for q in pts[1:i_d + 1]:
                if along_y:
                    q[1] += 1.2
                else:
                    q[0] += 1.2
        used.add((round(pts[i_d][0], 1), round(pts[i_d][1], 1)))
        hole = pts[i_d]
        node = k["node"]
        s = -1 if k["y"] > 34 else 1                    # bedroom trunk lies north of the exit: mirror y
        edge = 26.0 if s == 1 else 36.9                 # outer edge of the trunk band (real y)
        cls = "A" if s * hole[1] < s * edge else "B"    # source beyond the trunk (A) or between trunk and exit (B)
        groups.setdefault((node, s, cls), []).append((k, pts[:i_d], hole))
    layer_z = {("living", "A"): -0.85, ("living", "B"): -1.15, ("bedroom", "A"): -1.5, ("bedroom", "B"): -1.8,
               ("door", "A"): -2.15, ("door", "B"): -2.45}
    out = {}
    for (node, s, cls), items in groups.items():
        z = layer_z[(node, cls)]
        # in mirrored coordinates (y' = s*y) every cable runs "north" from trunk to exit; ordering that avoids crossings:
        # class A by x descending, class B by x ascending -> trunk y' ascending, turn x descending, exit y' ascending
        items.sort(key=lambda it: -it[2][0] if cls == "A" else it[2][0])
        e0 = 30.65 if s == 1 else 32.35
        for i, (k, prefix, hole) in enumerate(items):
            trunk = (26.0 + i * 0.35) if s == 1 else (36.75 - i * 0.35)    # living y 26 ->, bedroom y 36.75 <-
            ex = 79.45 - i * 0.32
            exit_y = (e0 + i * 0.32) if s == 1 else (e0 - i * 0.32)
            out[k["id"]] = {"prefix": [tuple(q) for q in prefix], "hole": hole, "z": z, "trunk": trunk, "ex": ex,
                            "exit_y": exit_y}
    # hub side: per node, turn x so cables heading to the board edge do not cross
    for node in ("living", "bedroom", "door"):
        esp = esp_of(node)
        ids = [k["id"] for k in COMP if k["id"] in out and k["node"] == node]
        if not esp or not ids:
            continue
        edge = esp["y"] + 3
        north = all(out[i]["exit_y"] < edge for i in ids)
        ids.sort(key=lambda i: -out[i]["exit_y"] if north else out[i]["exit_y"])
        x0 = esp["x"] + (0.5 if esp["x"] < HUB["x"] + 6 else 0) - 0.3 * (len(ids) - 1) / 2
        for j, i in enumerate(ids):
            out[i]["turn_x"] = x0 + j * 0.3
            out[i]["edge"] = edge
    routes = {}
    for k in COMP:
        c = out.get(k["id"])
        if not c:
            continue
        x, y = c["hole"][0], c["hole"][1]
        z = c["z"]
        pts = list(c["prefix"]) + [(x, y, 0.0), (x, y, z), (x, c["trunk"], z), (c["ex"], c["trunk"], z),
                                    (c["ex"], c["exit_y"], z), (c["turn_x"], c["exit_y"], z),
                                    (c["turn_x"], c["edge"] + (0.8 if c["exit_y"] > c["edge"] else -0.8), z),
                                    (c["turn_x"], c["edge"], -1.55)]
        clean = [pts[0]]
        for q in pts[1:]:
            if (Vector(q) - Vector(clean[-1])).length > 1e-4:
                clean.append(q)
        routes[k["id"]] = clean
    return routes


def font_label(name, text, loc, size, material, parent, collection, rot=(0, 0, 0)):
    cu = bpy.data.curves.new(name, "FONT")
    cu.body = text
    cu.size = size
    cu.align_x = "CENTER"
    cu.align_y = "CENTER"
    cu.extrude = 0.02
    ob = bpy.data.objects.new(name, cu)
    ob.data.materials.append(MATS[material])
    ob.location = loc
    ob.rotation_euler = rot
    collection.objects.link(ob)
    ob.parent = parent
    return ob


TAGS = []


def place_tag(tx, hy):
    """y for a number tag near a hole: not under a ceiling beam (top view) and not on another tag."""
    for dy in (-1.6, 1.8, -3.6, 3.8, -5.6, 5.8, -7.6, 7.8):
        ty = min(max(hy + dy, 1.2), D - 1.2)
        if any(b - 1.0 < ty < b + 2.6 for b in (16.2, 46.2)):
            continue
        if any(abs(tx - x) < 2.8 and abs(ty - y) < 1.9 for x, y in TAGS):
            continue
        TAGS.append((tx, ty))
        return ty
    TAGS.append((tx, hy - 1.6))
    return hy - 1.6


def build_cables(root):
    """One sheathed cable per part, each with its own colour, plus its number on the floor beside its hole and at
    the hub end. Writes the legend to docs/mockup/cables.md."""
    mat("label_text", (0.05, 0.05, 0.06), 0.5)
    routes = CABLE_ROUTES
    bundles = []
    CABLES.clear()
    TAGS.clear()
    for n, k in enumerate([k for k in COMP if k["id"] in routes]):
        cname, rgb = CABLE_PALETTE[n % len(CABLE_PALETTE)]
        planned = k["st"] == "planned"
        mname = f"cable_{safe(cname).lower()}" + ("_planned" if planned else "")
        mat(mname, rgb, 0.4, coat=0.3, alpha=0.4 if planned else 1.0)
        node = k["node"]
        node_name = {"living": "Living node", "bedroom": "Bedroom node", "door": "Door node (later)"}.get(node, node)
        c = coll(f"Wires/{node_name}")
        pts = routes[k["id"]]
        cv = poly_curve(f"Cable_{k['id']:02d}_{safe(k['p'])}_{cname.replace(' ', '')}", pts, CABLE_R, mname, root, c)
        cv["part_id"], cv["colour"], cv["conductors"] = k["id"], cname, ", ".join(conductors(k))
        length = sum((Vector(b) - Vector(a)).length for a, b in zip(pts, pts[1:]))
        # number on the floor next to the hole (the same number goes on the cable's tag in the real build)
        lc = coll("Labels")
        hole = next(q for q in pts if abs(q[2]) < 1e-6)
        tx = hole[0] + (2.2 if hole[0] < W - 4 else -2.2)
        ty = place_tag(tx, hole[1])
        tag = empty(f"Label_floor_{k['id']:02d}", (tx, ty, 0.0), lc, root, size=0.3)
        box(f"Label_floor_{k['id']:02d}_tag", -1.3, -0.85, 0.0, 2.6, 1.7, 0.08, mname if not planned else "white_gloss",
            tag, lc, bevel=0.2)
        box(f"Label_floor_{k['id']:02d}_plate", -1.05, -0.62, 0.08, 2.1, 1.24, 0.03, "white_gloss", tag, lc, bevel=0.1)
        font_label(f"Label_floor_{k['id']:02d}_text", f"{k['id']:02d}", (0, 0, 0.12), 1.15, "label_text", tag, lc)
        CABLES.append({"id": k["id"], "part": k["p"], "node": node, "colour": cname, "rgb": rgb,
                       "conductors": conductors(k), "length_cm": length, "planned": planned})
        bundles.append((k, [cv]))
    write_legend()
    return bundles


def write_legend():
    node_ar = {"living": "عقدة الصالة (ESP32 #1)", "bedroom": "عقدة غرفة النوم (ESP32 #2)", "door": "عقدة الباب (لاحقاً)"}
    lines = ["# Cable legend — one cable per part (generated by docs/mockup/blender/build_mockup.py)", "",
             "كل قطعة يخرج منها **كابل واحد** يجمع أسلاكها، بلون مختلف عن غيره (في Blender وفي التركيب الحقيقي:",
             "شريط/عازل حراري بنفس اللون على طرفي الكابل). **الرقم** مكتوب على الأرضية بجانب فتحة القطعة وعلى طرفي الكابل.",
             "الطول تقريبي من النموذج (من القطعة إلى طرف لوحة الـ ESP32)؛ **أضف 15 سم** للأطراف والتوصيل.", "",
             "| # | Part | Node | Cable colour | Wires inside | Length ≈ (cm) |", "|---|---|---|---|---|---|"]
    for c in CABLES:
        wires = ", ".join(c["conductors"])
        later = " (later)" if c["planned"] else ""
        lines.append(f"| {c['id']:02d} | {c['part']}{later} | {node_ar.get(c['node'], c['node'])} | {c['colour']} | "
                     f"{len(c['conductors'])}: {wires} | {c['length_cm']:.0f} |")
    lines += ["", "Wire roles: 5V red, GND black, 3V3 orange, SDA blue, SCL green, SIG yellow, TX white, RX purple.", ""]
    path = os.path.join(MOCKUP_DIR, "cables.md")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines))
    print("[mockup] cable legend ->", path)


def build_wires(root):
    if WIRE_COLOR_MODE == "cable":
        return build_cables(root)
    bundles = []
    for k in COMP:
        r = ROUTES.get(k["id"])
        if not r:
            continue
        node, planned = k["node"], k["st"] == "planned"
        node_name = {"living": "Living node", "bedroom": "Bedroom node", "door": "Door node (later)"}.get(node, node)
        c = coll(f"Wires/{node_name}")
        roles = conductors(k)
        n = len(roles)
        curves = []
        for j, role in enumerate(roles):
            o = (j - (n - 1) / 2) * WIRE_GAP
            pts = [(q[0] + o, q[1] + o, q[2]) for q in r["pts"]]
            if WIRE_COLOR_MODE == "node":
                m = f"wire_node_{node}" + ("_planned" if planned else "")
            else:
                m = f"wire_{role}" + ("_planned" if planned else "")
            curves.append(poly_curve(f"Wire_{k['id']:02d}_{safe(k['p'])}_{j + 1}_{role}", pts, WIRE_R, m, root, c))
        bundles.append((k, curves))
    return bundles


# --------------------------------------------------------------------------------------------- animation
def key_drop(e, f0, height=35.0, dur=12):
    final = e.location.copy()
    e.scale = (0, 0, 0)
    e.keyframe_insert("scale", frame=f0 - 1)
    e.scale = (1, 1, 1)
    e.keyframe_insert("scale", frame=f0)
    e.location = final + Vector((0, 0, height))
    e.keyframe_insert("location", frame=f0)
    e.location = final
    e.keyframe_insert("location", frame=f0 + dur)


def animate(walls_root, floor, hub, furniture, parts, bundles):
    walls_root.scale = (1, 1, 0.001)
    walls_root.keyframe_insert("scale", frame=F_WALLS[0])
    walls_root.scale = (1, 1, 1)
    walls_root.keyframe_insert("scale", frame=F_WALLS[1])
    hub.scale = (1, 1, 0.001)
    hub.keyframe_insert("scale", frame=F_WALLS[0] + 6)
    hub.scale = (1, 1, 1)
    hub.keyframe_insert("scale", frame=F_WALLS[1] + 6)
    for i, e in enumerate(furniture):
        key_drop(e, F_FURN + i * 4)
    for i, e in enumerate(parts):
        key_drop(e, F_PARTS + i * 3, height=25.0)
    # floor fades to transparent while the wires grow, then comes back
    a = bsdf_input(MATS["floor"], "Alpha")
    if a is not None:
        for f, v in ((F_FLOOR_FADE[0], 1.0), (F_FLOOR_FADE[1], 0.03), (F_FLOOR_BACK[0], 0.03), (F_FLOOR_BACK[1], 1.0)):
            a.default_value = v
            a.keyframe_insert("default_value", frame=f)
    if "Labels" in COLL:                                # the hole numbers appear when the cables start
        for ob in COLL["Labels"].objects:
            if ob.type == "EMPTY":
                ob.scale = (0, 0, 0)
                ob.keyframe_insert("scale", frame=F_WIRES - 6)
                ob.scale = (1, 1, 1)
                ob.keyframe_insert("scale", frame=F_WIRES)
    for i, (k, curves) in enumerate(bundles):
        f0 = F_WIRES + i * 2
        for cv in curves:
            cv.data.bevel_factor_end = 0.0
            cv.data.keyframe_insert("bevel_factor_end", frame=f0)
            cv.data.bevel_factor_end = 1.0
            cv.data.keyframe_insert("bevel_factor_end", frame=f0 + WIRE_GROW)
    # lights on at the end
    for m in LED_MATS:
        s = bsdf_input(m, "Emission Strength")
        if s is None:
            continue
        s.default_value = 0.0
        s.keyframe_insert("default_value", frame=F_LIGHTS[0])
        s.default_value = 25.0
        s.keyframe_insert("default_value", frame=F_LIGHTS[1])
    for ld in LED_LIGHTS:
        ld.energy = 0.0
        ld.keyframe_insert("energy", frame=F_LIGHTS[0])
        ld.energy = 1.5
        ld.keyframe_insert("energy", frame=F_LIGHTS[1])


# --------------------------------------------------------------------------------------------- cameras, lights, render
CM = 0.01


def camera(name, pos_cm, target_cm, lens=35, c=None):
    cam = bpy.data.cameras.new(name)
    cam.lens = lens
    cam.clip_start = 0.005
    cam.clip_end = 50
    ob = bpy.data.objects.new(name, cam)
    (c or coll("Cameras")).objects.link(ob)
    ob.location = Vector(pos_cm) * CM
    tgt = empty(name + "_target", Vector(target_cm) * CM, c or coll("Cameras"), size=0.02)
    con = ob.constraints.new("TRACK_TO")
    con.target = tgt
    con.track_axis = "TRACK_NEGATIVE_Z"
    con.up_axis = "UP_Y"
    return ob


CAMS = {}


def build_cameras_lights():
    c = coll("Cameras")
    CAMS["01_overview"] = (camera("Cam_01_overview", (135, -75, 95), (46, 27, 0), 35), F_END)
    CAMS["02_kitchen"] = (camera("Cam_02_kitchen", (32, 30, 42), (70, 10, 4), 24), F_END)
    CAMS["03_bedroom"] = (camera("Cam_03_bedroom", (62, 36, 44), (16, 51, 2), 24), F_END)
    CAMS["04_wires"] = (camera("Cam_04_wires_transparent_floor", (55, -60, 105), (55, 33, -2), 32), F_WIRES_HOLD)
    CAMS["05_hub"] = (camera("Cam_05_hub", (128, 8, 32), (93, 31, -1), 45), F_END)
    # top-down map of the cables (rendered with the furniture hidden, floor transparent)
    top = bpy.data.cameras.new("Cam_06_cables_top")
    top.type = "ORTHO"
    top.ortho_scale = 1.12
    top.clip_end = 50
    top_ob = bpy.data.objects.new("Cam_06_cables_top", top)
    c.objects.link(top_ob)
    top_ob.location = Vector((51.5, 30, 150)) * CM
    CAMS["06_cables_top"] = (top_ob, F_WIRES_HOLD)
    # orbiting camera for the assembly animation
    pivot = empty("Cam_anim_orbit", Vector((44, 30, 0)) * CM, c, size=0.05)
    cam = camera("Cam_anim", (44 + 145, 30 - 60, 95), (44, 30, 0), 32)
    cam.parent = pivot
    cam.location = Vector((145, -60, 95)) * CM
    edit = bpy.context.preferences.edit
    old = edit.keyframe_new_interpolation_type
    edit.keyframe_new_interpolation_type = "LINEAR"
    pivot.rotation_euler = (0, 0, math.radians(-35))
    pivot.keyframe_insert("rotation_euler", frame=1)
    pivot.rotation_euler = (0, 0, math.radians(55))
    pivot.keyframe_insert("rotation_euler", frame=F_END)
    edit.keyframe_new_interpolation_type = old
    # a small point light under every ceiling LED (switched on at the end of the animation)
    for k in COMP:
        if k["z"] >= 20:
            ld = bpy.data.lights.new(f"LED_light_{k['id']:02d}", "POINT")
            ld.energy = 0.0
            ld.color = (1.0, 0.88, 0.70)
            ld.shadow_soft_size = 0.004
            lo = bpy.data.objects.new(f"LED_light_{k['id']:02d}", ld)
            coll("Lights").objects.link(lo)
            lo.location = Vector((k["x"], k["y"], H - 2.0)) * CM
            LED_LIGHTS.append(ld)
    bpy.context.scene.camera = cam
    # sun + sky
    sun_d = bpy.data.lights.new("Sun", "SUN")
    sun_d.energy = 3.5
    sun_d.angle = math.radians(8)
    sun = bpy.data.objects.new("Sun", sun_d)
    coll("Lights").objects.link(sun)
    sun.rotation_euler = (math.radians(50), math.radians(10), math.radians(35))
    world = bpy.context.scene.world or bpy.data.worlds.new("World")
    bpy.context.scene.world = world
    if world.node_tree is None:
        world.use_nodes = True
    bg = next((n for n in world.node_tree.nodes if n.type == "BACKGROUND"), None)
    if bg:
        bg.inputs["Color"].default_value = (0.62, 0.68, 0.78, 1.0)
        bg.inputs["Strength"].default_value = 0.7
    # a large table under the mockup
    box("Table_top", -15, -15, -5.5, 135, 90, 2.0, mat("table", (0.42, 0.40, 0.38), 0.6), ROOT, coll("Environment"))


def setup_viewport():
    """Open the file clean: assembled (last frame), Material Preview, no camera / light / empty clutter,
    the view framed on the house. Cameras and lights stay on for rendering (only hidden in the viewport)."""
    for name in ("Cameras", "Lights", "Environment"):
        if name in COLL:
            COLL[name].hide_viewport = True
    for scr in bpy.data.screens:
        for area in scr.areas:
            if area.type != "VIEW_3D":
                continue
            for sp in area.spaces:
                if sp.type != "VIEW_3D":
                    continue
                sp.shading.type = "MATERIAL"
                sp.overlay.show_extras = False          # cameras, lights, empties
                sp.overlay.show_relationship_lines = False
                sp.clip_start = 0.001
                r3d = sp.region_3d
                r3d.view_perspective = "PERSP"
                r3d.view_location = Vector((50, 30, 2)) * CM
                r3d.view_distance = 1.45
                r3d.view_rotation = Euler((math.radians(55), 0, math.radians(35))).to_quaternion()


def setup_render(o):
    sc = bpy.context.scene
    if o["eevee"]:
        for eng in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
            try:
                sc.render.engine = eng
                break
            except TypeError:
                continue
    else:
        sc.render.engine = "CYCLES"
        sc.cycles.samples = o["samples"]
        sc.cycles.use_denoising = True
        try:
            prefs = bpy.context.preferences.addons["cycles"].preferences
            for dev_type in ("OPTIX", "CUDA", "HIP", "ONEAPI", "METAL"):
                try:
                    prefs.compute_device_type = dev_type
                    prefs.get_devices()
                    devs = [d for d in prefs.devices if d.type == dev_type]
                    if devs:
                        for d in prefs.devices:
                            d.use = d.type == dev_type
                        sc.cycles.device = "GPU"
                        print(f"[mockup] Cycles on GPU ({dev_type}): {', '.join(d.name for d in devs)}")
                        break
                except TypeError:
                    continue
            else:
                print("[mockup] no GPU found for Cycles, rendering on the CPU")
        except Exception as ex:
            print("[mockup] GPU setup failed, CPU will be used:", ex)
    sc.render.resolution_x, sc.render.resolution_y = o["res"]
    sc.render.resolution_percentage = 100
    try:
        sc.view_settings.view_transform = "AgX"
    except TypeError:
        pass


def render_stills():
    sc = bpy.context.scene
    out = os.path.join(MOCKUP_DIR, "renders")
    os.makedirs(out, exist_ok=True)
    sc.render.image_settings.file_format = "PNG"
    for name, (cam, frame) in CAMS.items():
        sc.camera = cam
        sc.frame_set(frame)
        sc.render.filepath = os.path.join(out, f"{name}.png")
        print("[mockup] rendering", sc.render.filepath)
        hide = [COLL[n] for n in ("Furniture",) if name == "06_cables_top" and n in COLL]
        for col in hide:
            col.hide_render = True
        bpy.ops.render.render(write_still=True)
        for col in hide:
            col.hide_render = False
    sc.camera = bpy.data.objects["Cam_anim"]


def render_anim(path):
    sc = bpy.context.scene
    sc.camera = bpy.data.objects["Cam_anim"]
    path = os.path.abspath(path)
    try:
        try:
            sc.render.image_settings.media_type = "VIDEO"
        except Exception:
            pass
        sc.render.image_settings.file_format = "FFMPEG"
        sc.render.ffmpeg.format = "MPEG4"
        sc.render.ffmpeg.codec = "H264"
        sc.render.ffmpeg.constant_rate_factor = "HIGH"
        sc.render.filepath = path
    except Exception as ex:
        print("[mockup] video output not available, writing PNG frames instead:", ex)
        sc.render.image_settings.file_format = "PNG"
        sc.render.filepath = os.path.splitext(path)[0] + "_frames/frame_"
    print("[mockup] rendering animation ->", sc.render.filepath)
    bpy.ops.render.render(animation=True)


# --------------------------------------------------------------------------------------------- main
ROOT = None


def main():
    global ROOT
    o = args()
    reset_scene()
    COLL.clear(); MATS.clear(); LED_MATS.clear(); LED_LIGHTS.clear(); CAMS.clear()
    make_materials()
    ROOT = empty("Mockup_root", (0, 0, 0), coll("House"), size=0.1)
    ROOT.scale = (CM, CM, CM)
    CABLE_ROUTES.clear()
    if WIRE_COLOR_MODE == "cable":
        CABLE_ROUTES.update(cable_routes())
    walls_root, floor = build_house(ROOT)
    furniture = build_furniture(ROOT)
    hub = build_hub(ROOT)
    parts = build_parts(ROOT)
    bundles = build_wires(ROOT)
    build_cameras_lights()
    animate(walls_root, floor, hub, furniture, parts, bundles)
    setup_render(o)
    setup_viewport()
    bpy.context.scene.frame_set(F_END)
    n_wires = sum(len(cs) for _, cs in bundles)
    what = f"{n_wires} cables (one per part)" if WIRE_COLOR_MODE == "cable" else f"{n_wires} conductors"
    print(f"[mockup] built from {DATA_PATH}: {len(furniture)} furniture, {len(parts)} parts, {what}, "
          f"{len(bpy.data.objects)} objects")
    if o["save"]:
        bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(o["save"]))
        print("[mockup] saved", os.path.abspath(o["save"]))
    if o["render"]:
        render_stills()
    if o["anim"]:
        render_anim(o["anim"])


main()
