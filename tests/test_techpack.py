import pytest

from seam import techpack


def build(**kw):
    args = dict(code="X", name="X", garment="tee", season=None, fabric=None, features=[])
    args.update(kw)
    return techpack.build(**args)


def test_grading_steps_out_from_base_size():
    b = build()
    chest = next(m for m in b["measurements"] if m["pom"] == "A")
    assert chest["values"] == {"XS": 47.0, "S": 49.5, "M": 52.0, "L": 54.5, "XL": 57.0}


def test_feature_changes_measurements_and_cost():
    short = build()
    long = build(features=["long_sleeve"])
    sleeve = lambda b: next(m for m in b["measurements"] if m["pom"] == "D")["values"]["M"]
    assert sleeve(short) == 21 and sleeve(long) == 60
    assert long["cost"]["cmt_cents"] == short["cost"]["cmt_cents"] + 30


def test_cost_adds_up():
    b = build(garment="trousers", features=["zip_fly", "back_pockets"])
    c = b["cost"]
    assert c["target_fob_cents"] == c["fabric_cents"] + c["trims_cents"] + c["cmt_cents"]
    assert any("zip" in x["description"].lower() for x in b["bom"])


def test_override_regrades_every_size():
    b = build(overrides={"A": 55})
    chest = next(m for m in b["measurements"] if m["pom"] == "A")
    assert chest["values"]["M"] == 55 and chest["values"]["XS"] == 50 and chest["values"]["XL"] == 60


def test_same_input_same_output():
    assert build(features=["v_neck", "chest_pocket"]) == build(features=["chest_pocket", "v_neck"])


def test_unknown_inputs_rejected():
    with pytest.raises(ValueError):
        build(garment="cape")
    with pytest.raises(ValueError):
        build(fabric="silk_unicorn")


def test_note_only_change_keeps_quote():
    a = build()
    b = techpack.apply_edit(a, {"designer_note": "Bar-tack pocket corners"})
    d = techpack.diff(a, b)
    assert [c["area"] for c in d["changes"]] == ["note"]
    assert d["affects_quote"] is False


def test_fabric_and_measurement_changes_need_new_quote():
    a = build()
    b = techpack.apply_edit(a, {"fabric": "linen", "measurements": {"B": 72}})
    d = techpack.diff(a, b)
    areas = {c["area"] for c in d["changes"]}
    assert areas == {"fabric", "measurement"}
    assert d["affects_quote"] is True
    m = next(c for c in d["changes"] if c["area"] == "measurement")
    assert m["from"] == 70 and m["to"] == 72 and m["sizes"] == techpack.SIZES
    assert d["cost_to_cents"] > d["cost_from_cents"]
