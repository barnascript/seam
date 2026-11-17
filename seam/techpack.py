"""Tech pack building and comparing.

Reading a sketch can be fuzzy. Everything after that is plain code: sizes,
grading, fabric use and cost always come out the same for the same input, so a
factory can trust the numbers.
"""
from copy import deepcopy

SIZES = ["XS", "S", "M", "L", "XL"]
BASE = "M"

FABRICS = {
    "cotton_jersey": {"label": "Cotton jersey 180 gsm", "price_per_m_cents": 380, "colour": "#d9d4c7"},
    "organic_poplin": {"label": "Organic cotton poplin 120 gsm", "price_per_m_cents": 460, "colour": "#eeebe2"},
    "linen": {"label": "Linen plain weave 160 gsm", "price_per_m_cents": 720, "colour": "#cdbf9f"},
    "viscose_crepe": {"label": "Viscose crepe 140 gsm", "price_per_m_cents": 510, "colour": "#2e3a4f"},
    "denim": {"label": "Denim 12 oz", "price_per_m_cents": 640, "colour": "#3b5878"},
    "wool_twill": {"label": "Wool blend twill 280 gsm", "price_per_m_cents": 1190, "colour": "#5a5048"},
}

# Points of measure at size M, in cm, measured flat. grade = change per size step.
GARMENTS = {
    "tee": {
        "label": "T-shirt",
        "fabric_m": 1.2,
        "cmt_cents": 220,
        "default_fabric": "cotton_jersey",
        "poms": [
            ("A", "Chest width, 1 cm below armhole", 52, 2.5, 1.0),
            ("B", "Body length from high point shoulder", 70, 1.5, 1.0),
            ("C", "Shoulder width", 45, 1.2, 0.5),
            ("D", "Sleeve length", 21, 0.5, 0.5),
            ("E", "Sleeve opening", 17, 0.6, 0.5),
            ("F", "Neck width", 18, 0.5, 0.5),
            ("G", "Hem width", 51, 2.5, 1.0),
        ],
        "base_notes": [
            "Overlock all inside seams, 4-thread.",
            "Rib neckband 2 cm, twin-needle topstitch.",
            "Coverstitch hem 2.5 cm on body and sleeves.",
        ],
        "features": {
            "long_sleeve": {"label": "Long sleeve", "cmt": 30, "pom": {"D": 60, "E": 10}, "note": "Long sleeve, 6 cm cuff rib."},
            "v_neck": {"label": "V-neck", "cmt": 20, "note": "V-neck with mitred rib at centre front."},
            "chest_pocket": {"label": "Chest pocket", "cmt": 25, "fabric": 0.05, "note": "Patch pocket on left chest, 11 x 13 cm."},
        },
    },
    "shirt": {
        "label": "Shirt",
        "fabric_m": 1.7,
        "cmt_cents": 480,
        "default_fabric": "organic_poplin",
        "poms": [
            ("A", "Chest width, 2.5 cm below armhole", 56, 2.5, 1.0),
            ("B", "Back length from collar seam", 76, 1.5, 1.0),
            ("C", "Shoulder width", 47, 1.2, 0.5),
            ("D", "Sleeve length from shoulder", 64, 0.75, 1.0),
            ("E", "Cuff length", 24, 0.5, 0.5),
            ("F", "Collar length", 40, 1.5, 0.5),
            ("G", "Hem width", 54, 2.5, 1.0),
        ],
        "base_notes": [
            "Single-needle seams, 12 stitches per 3 cm.",
            "Two-piece collar with fused stand.",
            "7 buttons on centre front placket.",
        ],
        "trims": [("Buttons, 11 mm corozo", 9, "pcs", 6)],
        "features": {
            "chest_pocket": {"label": "Chest pocket", "cmt": 25, "fabric": 0.05, "note": "Patch pocket on left chest with pointed base."},
            "button_down": {"label": "Button-down collar", "cmt": 20, "trims": [("Buttons, 9 mm corozo", 2, "pcs", 5)], "note": "Button-down collar points."},
            "short_sleeve": {"label": "Short sleeve", "cmt": -40, "fabric": -0.2, "pom": {"D": 25, "E": 36}, "note": "Short sleeve, 3 cm turn-up hem."},
        },
    },
    "dress": {
        "label": "Dress",
        "fabric_m": 2.3,
        "cmt_cents": 620,
        "default_fabric": "viscose_crepe",
        "poms": [
            ("A", "Bust width", 46, 2.5, 1.0),
            ("B", "Waist width", 38, 2.5, 1.0),
            ("C", "Hip width", 50, 2.5, 1.0),
            ("D", "Length from high point shoulder", 105, 1.0, 1.0),
            ("E", "Shoulder width", 37, 1.0, 0.5),
            ("F", "Armhole straight", 21, 0.75, 0.5),
            ("G", "Hem sweep", 70, 2.5, 1.5),
        ],
        "base_notes": [
            "French seams throughout.",
            "Neckline finished with bias facing, understitched.",
            "Narrow rolled hem 0.6 cm.",
        ],
        "features": {
            "back_zip": {"label": "Back zip", "cmt": 45, "trims": [("Invisible zip, 55 cm", 1, "pcs", 38)], "note": "Invisible zip at centre back, 55 cm."},
            "pockets": {"label": "Side pockets", "cmt": 40, "fabric": 0.15, "note": "In-seam side pockets, pocket bag in self fabric."},
            "lined": {"label": "Lined", "cmt": 120, "trims": [("Lining, cupro", 1.6, "m", 290)], "note": "Fully lined in cupro, lining hem 2 cm above main hem."},
        },
    },
    "trousers": {
        "label": "Trousers",
        "fabric_m": 1.4,
        "cmt_cents": 540,
        "default_fabric": "wool_twill",
        "poms": [
            ("A", "Waist width, relaxed", 40, 2.5, 1.0),
            ("B", "Hip width, 18 cm below waist", 52, 2.5, 1.0),
            ("C", "Front rise", 28, 0.75, 0.5),
            ("D", "Inseam", 80, 0.5, 1.0),
            ("E", "Thigh width", 31, 1.25, 0.5),
            ("F", "Knee width", 22, 0.75, 0.5),
            ("G", "Leg opening", 19, 0.5, 0.5),
        ],
        "base_notes": [
            "Safety-stitch inside seams, pressed open at outseam.",
            "Waistband 4 cm, interfaced.",
            "Blind-stitch hem 4 cm.",
        ],
        "trims": [("Waistband button, 15 mm", 1, "pcs", 8), ("Hook and bar", 1, "pcs", 6)],
        "features": {
            "zip_fly": {"label": "Zip fly", "cmt": 35, "trims": [("Metal zip, 18 cm", 1, "pcs", 32)], "note": "Front zip fly with J-stitch."},
            "belt_loops": {"label": "Belt loops", "cmt": 20, "note": "6 belt loops, 1.2 cm wide."},
            "back_pockets": {"label": "Back welt pockets", "cmt": 60, "fabric": 0.08, "note": "Two double-welt back pockets, 13 cm opening."},
        },
    },
}

LABEL_TRIMS = [("Main label, woven", 1, "pcs", 4), ("Care label, printed", 1, "pcs", 2), ("Thread", 1, "set", 6)]


def _round_half(x: float) -> float:
    return round(x * 2) / 2


def build(code: str, name: str, garment: str, season: str | None, fabric: str | None,
          features: list[str] | None, overrides: dict | None = None,
          designer_notes: list[str] | None = None, read_from: dict | None = None) -> dict:
    if garment not in GARMENTS:
        raise ValueError(f"unknown garment {garment!r}")
    g = GARMENTS[garment]
    fabric = fabric or g["default_fabric"]
    if fabric not in FABRICS:
        raise ValueError(f"unknown fabric {fabric!r}")
    features = sorted({f for f in (features or []) if f in g["features"]})
    overrides = {k: float(v) for k, v in (overrides or {}).items()}
    fab = FABRICS[fabric]

    base_values = {pom: base for pom, _, base, _, _ in g["poms"]}
    for f in features:
        base_values.update(g["features"][f].get("pom", {}))
    base_values.update({k: v for k, v in overrides.items() if k in base_values})

    measurements = []
    for pom, label, _, grade, tol in g["poms"]:
        base = base_values[pom]
        values = {}
        for i, size in enumerate(SIZES):
            step = i - SIZES.index(BASE)
            values[size] = _round_half(base + step * grade)
        measurements.append({"pom": pom, "label": label, "grade": grade, "tolerance": tol, "values": values})

    fabric_m = g["fabric_m"] + sum(g["features"][f].get("fabric", 0) for f in features)
    fabric_m = round(fabric_m, 2)
    bom = [{"item": "Main fabric", "description": fab["label"], "qty": fabric_m, "unit": "m",
            "unit_cost_cents": fab["price_per_m_cents"]}]
    trims = list(g.get("trims", []))
    for f in features:
        trims += g["features"][f].get("trims", [])
    for desc, qty, unit, cost in trims + LABEL_TRIMS:
        bom.append({"item": "Trim", "description": desc, "qty": qty, "unit": unit, "unit_cost_cents": cost})

    fabric_cents = round(fabric_m * fab["price_per_m_cents"])
    trims_cents = round(sum(b["qty"] * b["unit_cost_cents"] for b in bom[1:]))
    cmt_cents = g["cmt_cents"] + sum(g["features"][f]["cmt"] for f in features)

    construction = list(g["base_notes"]) + [g["features"][f]["note"] for f in features]

    return {
        "header": {
            "style_code": code, "name": name, "garment": garment, "garment_label": g["label"],
            "season": season, "base_size": BASE, "sizes": SIZES,
            "fabric": {"code": fabric, **fab},
        },
        "features": [{"code": f, "label": g["features"][f]["label"]} for f in features],
        "overrides": overrides,
        "measurements": measurements,
        "bom": bom,
        "construction": construction,
        "designer_notes": list(designer_notes or []),
        "fabric_per_size_m": {s: round(fabric_m * (1 + 0.04 * (i - SIZES.index(BASE))), 2) for i, s in enumerate(SIZES)},
        "cost": {
            "fabric_cents": fabric_cents,
            "trims_cents": trims_cents,
            "cmt_cents": cmt_cents,
            "target_fob_cents": fabric_cents + trims_cents + cmt_cents,
        },
        "read_from": read_from or {},
    }


def inputs_of(body: dict) -> dict:
    """The inputs that produced a body, so an edit can rebuild from them."""
    h = body["header"]
    return {
        "code": h["style_code"], "name": h["name"], "garment": h["garment"], "season": h["season"],
        "fabric": h["fabric"]["code"], "features": [f["code"] for f in body["features"]],
        "overrides": dict(body.get("overrides", {})), "designer_notes": list(body.get("designer_notes", [])),
        "read_from": body.get("read_from", {}),
    }


def apply_edit(body: dict, edit: dict) -> dict:
    inp = inputs_of(body)
    if edit.get("fabric"):
        inp["fabric"] = edit["fabric"]
    if edit.get("features") is not None:
        inp["features"] = edit["features"]
    for pom, value in (edit.get("measurements") or {}).items():
        inp["overrides"][pom] = value
    if edit.get("designer_note"):
        inp["designer_notes"] = inp["designer_notes"] + [edit["designer_note"]]
    return build(**inp)


def diff(old: dict, new: dict) -> dict:
    """What changed between two versions, and whether a factory quote still holds.

    Fabric, features and measurements change what the factory makes, so their
    quote goes stale. A designer note on its own does not.
    """
    changes = []
    of, nf = old["header"]["fabric"], new["header"]["fabric"]
    if of["code"] != nf["code"]:
        changes.append({"area": "fabric", "label": "Main fabric", "from": of["label"], "to": nf["label"], "affects_quote": True})

    old_feat = {f["code"]: f["label"] for f in old["features"]}
    new_feat = {f["code"]: f["label"] for f in new["features"]}
    for code in sorted(new_feat.keys() - old_feat.keys()):
        changes.append({"area": "feature", "label": new_feat[code], "from": None, "to": "added", "affects_quote": True})
    for code in sorted(old_feat.keys() - new_feat.keys()):
        changes.append({"area": "feature", "label": old_feat[code], "from": "removed", "to": None, "affects_quote": True})

    old_m = {m["pom"]: m for m in old["measurements"]}
    for m in new["measurements"]:
        prev = old_m.get(m["pom"])
        if not prev:
            continue
        moved = [s for s in SIZES if prev["values"][s] != m["values"][s]]
        if moved:
            changes.append({
                "area": "measurement", "label": f'{m["pom"]} {m["label"]}',
                "from": prev["values"][BASE], "to": m["values"][BASE],
                "sizes": moved, "affects_quote": True,
            })

    for note in new.get("designer_notes", [])[len(old.get("designer_notes", [])):]:
        changes.append({"area": "note", "label": "Designer note", "from": None, "to": note, "affects_quote": False})

    return {
        "changes": changes,
        "affects_quote": any(c["affects_quote"] for c in changes),
        "cost_from_cents": old["cost"]["target_fob_cents"],
        "cost_to_cents": new["cost"]["target_fob_cents"],
    }


def catalogue() -> dict:
    return {
        "sizes": SIZES,
        "fabrics": [{"code": k, **v} for k, v in FABRICS.items()],
        "garments": [
            {"code": k, "label": g["label"], "default_fabric": g["default_fabric"],
             "features": [{"code": fk, "label": fv["label"]} for fk, fv in g["features"].items()]}
            for k, g in GARMENTS.items()
        ],
    }


def clone(body: dict) -> dict:
    return deepcopy(body)
