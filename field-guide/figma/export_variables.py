"""Export Field Guide tokens as a Figma variable plan (code -> Figma).

    python field-guide/figma/export_variables.py > figma-variables.json

Two collections:
  _Primitives  hidden from publishing, scopes [] - raw palette colors, the alpha
               steps semantic tokens use (Figma aliases can't add opacity, so
               "ink 70%" becomes its own primitive ink/70), and raw dimensions.
  Semantic     every semantic token, aliased to a primitive, scoped to where it
               may be used, with WEB code syntax = var(--fg-...).
Shadows become an effect style. Line height and letter spacing stay code-only
(unitless multipliers / em have no clean Figma variable type).
"""
import importlib.util, json, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("fg_build", os.path.join(HERE, "..", "build.py"))
build = importlib.util.module_from_spec(spec); spec.loader.exec_module(build)
tokens = build.flatten(build.load_tokens())

def rgba(hex_value, a=1.0):
    h = hex_value.lstrip("#")
    return {"r": int(h[0:2], 16) / 255, "g": int(h[2:4], 16) / 255, "b": int(h[4:6], 16) / 255, "a": a}

COLOR_SCOPES = {
    "color.surface": ["FRAME_FILL", "SHAPE_FILL"],
    "color.text": ["TEXT_FILL"],
    "color.border": ["STROKE_COLOR"],
    "color.signal": ["FRAME_FILL", "SHAPE_FILL", "STROKE_COLOR"],  # never text: Field Guide rule
}
ACTION_SCOPES = {
    "primary": ["FRAME_FILL", "SHAPE_FILL", "TEXT_FILL", "STROKE_COLOR"],
    "selected": ["FRAME_FILL", "SHAPE_FILL", "STROKE_COLOR"],
    "secondary-fill": ["FRAME_FILL", "SHAPE_FILL"],
    "secondary-hover": ["FRAME_FILL", "SHAPE_FILL"],
    "focus-ring": ["STROKE_COLOR"],
    "subtle-text": ["TEXT_FILL"],
    "subtle-border": ["STROKE_COLOR"],
}
DIM_SCOPES = {"space": ["GAP"], "radius": ["CORNER_RADIUS"], "size": ["WIDTH_HEIGHT"], "font.size": ["FONT_SIZE"]}

primitives, semantic, effects = {}, [], []
palette_name = {}  # resolved hex -> palette key
for path, (value, ttype, ext) in tokens.items():
    if path.startswith("color.palette."):
        key = path.split(".")[-1]
        palette_name[value.lower()] = key
        primitives[f"color/{key}"] = {"type": "COLOR", "value": rgba(value), "code": f"var({build.css_name(path)})"}

for path, (value, ttype, ext) in tokens.items():
    group, leaf = path.rsplit(".", 1)
    name = path.replace(".", "/")
    code = f"var({build.css_name(path)})"
    if path.startswith("color.") and not path.startswith("color.palette."):
        base = build.resolve(value, tokens).lower()
        pal = palette_name[base]
        alpha = ext.get("fieldguide.alpha")
        prim = f"color/{pal}" if alpha is None else f"color/{pal}/{round(alpha * 100):02d}"
        if prim not in primitives:
            primitives[prim] = {"type": "COLOR", "value": rgba(base, alpha), "code": None}
        scopes = ACTION_SCOPES[leaf] if group == "color.action" else COLOR_SCOPES[group]
        semantic.append({"name": name, "type": "COLOR", "alias": prim, "scopes": scopes, "code": code})
    elif group in DIM_SCOPES:
        px = float(str(value).replace("px", ""))
        prim = f"dimension/{int(px) if px.is_integer() else px}"
        primitives.setdefault(prim, {"type": "FLOAT", "value": px, "code": None})
        semantic.append({"name": name, "type": "FLOAT", "alias": prim, "scopes": DIM_SCOPES[group], "code": code})
    elif group == "font.weight":
        semantic.append({"name": name, "type": "FLOAT", "value": value, "scopes": ["FONT_WEIGHT"], "code": code})
    elif group == "font.family":
        semantic.append({"name": name, "type": "STRING", "value": value[0], "scopes": ["FONT_FAMILY"], "code": code})
    elif group == "shadow":
        m = re.match(r"(-?\d+)px (-?\d+)px (\d+)px rgb\((\d+) (\d+) (\d+) / ([\d.]+)\)", value)
        x, y, blur, r, g, b, a = m.groups()
        effects.append({"name": "Elevation/" + leaf.capitalize(), "x": int(x), "y": int(y), "blur": int(blur),
                        "color": {"r": int(r) / 255, "g": int(g) / 255, "b": int(b) / 255, "a": float(a)}, "code": code})

print(json.dumps({"primitives": primitives, "semantic": semantic, "effects": effects}, indent=1))
