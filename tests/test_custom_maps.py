"""Tests for the custom_maps.json overlay — the hook that makes workshop /
modded maps render.

A modded layer is absent from data/static/layer_bounds.json, so the exact-name
lookup misses, no `layer` block is attached to the snapshot, and the viewer
draws a bare grid. The overlay lets an admin supply the bounds and the minimap
filename themselves, keyed by MAP name so one entry covers every layer of that
mod.

The whole point is that the admin does not know how the game spells the layer
("Hrodna_Border_RAAS_v1" vs "Hrodna Border RAAS v1" — only visible once the map
has actually run), so the matching has to be separator- and case-blind. That is
what most of these pin.
"""
import json

from sqreader.squad.metadata import Metadata

CUSTOM = {
    "Hrodna Border": {
        "texture": "HrodnaBorder",
        "topLeft": {"x": -200000, "y": -200000},
        "bottomRight": {"x": 200000, "y": 200000},
    },
    # Shares a prefix with the one above: the longer key has to win, or every
    # Hrodna layer would take the shorter entry's bounds.
    "Hrodna Border Night": {
        "texture": "HrodnaBorderNight",
        "topLeft": {"x": -100000, "y": -100000},
        "bottomRight": {"x": 100000, "y": 100000},
    },
    # Too short to be a safe key — a stray one like this must not hijack
    # unrelated layers.
    "AB": {"texture": "Nope", "topLeft": {"x": 0, "y": 0},
           "bottomRight": {"x": 1, "y": 1}},
}

BOUNDS_OK = {"topLeft": {"x": -1, "y": -1}, "bottomRight": {"x": 1, "y": 1}}

VANILLA = {
    "Narva RAAS v1": {
        "texture": "T_Narva_Minimap", "mapId": "Narva", "mapName": "Narva",
        "gameMode": "RAAS",
        "topLeft": {"x": -138970, "y": -140207},
        "bottomRight": {"x": 141029, "y": 139792},
    },
}


def _md(tmp_path, custom=CUSTOM, vanilla=VANILLA) -> Metadata:
    (tmp_path / "layer_bounds.json").write_text(json.dumps(vanilla))
    if custom is not None:
        (tmp_path / "custom_maps.json").write_text(json.dumps(custom))
    return Metadata.load(tmp_path)


def test_underscored_layer_name_finds_the_spaced_key(tmp_path):
    # How the game spells it is not knowable in advance — both must land.
    md = _md(tmp_path)
    assert md.layer_bounds_for("Hrodna_Border_RAAS_v1")["texture"] \
        == "HrodnaBorder"
    assert md.layer_bounds_for("Hrodna Border AAS v2")["texture"] \
        == "HrodnaBorder"


def test_exact_key_hit(tmp_path):
    md = _md(tmp_path)
    assert md.layer_bounds_for("Hrodna Border")["texture"] == "HrodnaBorder"


def test_longest_prefix_wins(tmp_path):
    md = _md(tmp_path)
    assert md.layer_bounds_for("Hrodna Border Night RAAS v1")["texture"] \
        == "HrodnaBorderNight"


def test_short_keys_never_match(tmp_path):
    md = _md(tmp_path)
    assert md.layer_bounds_for("Abandoned Quarry RAAS v1") is None


def test_bounds_survive_the_merge(tmp_path):
    md = _md(tmp_path)
    lb = md.layer_bounds_for("Hrodna_Border_Invasion_v1")
    assert lb["topLeft"] == {"x": -200000, "y": -200000}
    assert lb["bottomRight"] == {"x": 200000, "y": 200000}


def test_map_name_is_filled_in_from_the_key(tmp_path):
    # to_raw_layer_key and the heatmap both read mapName/mapId; an entry the
    # admin wrote without them must not hand downstream code a None.
    md = _md(tmp_path)
    lb = md.layer_bounds_for("Hrodna_Border_RAAS_v1")
    assert lb["mapName"] == "Hrodna Border"
    assert lb["mapId"] == "HrodnaBorder"


def test_vanilla_layers_still_resolve(tmp_path):
    md = _md(tmp_path)
    assert md.layer_bounds_for("Narva RAAS v1")["texture"] == "T_Narva_Minimap"


def test_unknown_layer_still_returns_none(tmp_path):
    md = _md(tmp_path)
    assert md.layer_bounds_for("Some Other Map RAAS v1") is None


def test_no_custom_file_changes_nothing(tmp_path):
    md = _md(tmp_path, custom=None)
    assert md.layer_bounds_for("Narva RAAS v1")["texture"] == "T_Narva_Minimap"
    assert md.layer_bounds_for("Hrodna_Border_RAAS_v1") is None


def test_malformed_entries_are_skipped_not_fatal(tmp_path):
    md = _md(tmp_path, custom={"Broken": "not a dict", "": {"texture": "x"},
                               "Good Map": {"texture": "GoodMap", **BOUNDS_OK}})
    assert md.layer_bounds_for("Narva RAAS v1") is not None
    assert md.layer_bounds_for("Good_Map_RAAS_v1")["texture"] == "GoodMap"


def test_community_tag_in_front_still_matches(tmp_path):
    # Communities put their tag BEFORE the map name — 43 of 963 archive
    # matches, per mapFallback.ts. Anchoring at the start would miss them.
    md = _md(tmp_path)
    assert md.layer_bounds_for("SEC 26 Hrodna Border RAAS v1")["texture"] \
        == "HrodnaBorder"
    assert md.layer_bounds_for("[GE] Hrodna_Border_AAS_v1")["texture"] \
        == "HrodnaBorder"


def test_loose_custom_key_never_shadows_a_stock_layer(tmp_path):
    # The whole risk of matching loosely: a modded Narva remake keyed "Narva"
    # must not drag the stock Narva layers onto the mod's guessed bounds.
    md = _md(tmp_path, custom={"Narva": {"texture": "NarvaRemake", **BOUNDS_OK}})
    assert md.layer_bounds_for("Narva RAAS v1")["texture"] == "T_Narva_Minimap"


def test_exact_custom_key_does_shadow_a_stock_layer(tmp_path):
    # Naming a stock layer exactly is unambiguous intent, and the only way to
    # override one.
    md = _md(tmp_path, custom={"Narva RAAS v1":
                               {"texture": "NarvaRemake", **BOUNDS_OK}})
    assert md.layer_bounds_for("Narva RAAS v1")["texture"] == "NarvaRemake"


def test_entry_without_bounds_is_rejected(tmp_path):
    # A layer block with null corners is worse than none: the viewer treats
    # any layer block as authoritative and stops falling back (draw.ts:47).
    md = _md(tmp_path, custom={"Hrodna Border": {"texture": "HrodnaBorder"}})
    assert md.layer_bounds_for("Hrodna_Border_RAAS_v1") is None


def test_non_numeric_and_nonfinite_corners_are_rejected(tmp_path):
    for bad in ({"x": "0", "y": 0}, {"x": True, "y": 1}, {"x": float("nan"),
                "y": 1}, {"x": 0}, [0, 0]):
        md = _md(tmp_path, custom={"Hrodna Border": {
            "texture": "HrodnaBorder", "topLeft": bad,
            "bottomRight": {"x": 1, "y": 1}}})
        assert md.layer_bounds_for("Hrodna_Border_RAAS_v1") is None, bad


def test_texture_that_the_server_would_refuse_is_rejected(tmp_path):
    # httpsrv._SQMAP_NAME_RE answers 400 to these, which the admin never sees.
    for bad in ("Hrodna Border", "Hrodna.webp", "../etc/passwd", "", 7):
        md = _md(tmp_path, custom={"Hrodna Border":
                                   {"texture": bad, **BOUNDS_OK}})
        assert md.layer_bounds_for("Hrodna_Border_RAAS_v1") is None, bad


def test_two_spellings_of_one_key_last_one_wins(tmp_path):
    # Both normalise to "hrodnaborder". Silently keeping the first would have
    # the admin editing a dead entry forever.
    md = _md(tmp_path, custom={
        "Hrodna Border": {"texture": "Old", **BOUNDS_OK},
        "Hrodna_Border": {"texture": "Corrected", **BOUNDS_OK}})
    assert md.layer_bounds_for("Hrodna_Border_RAAS_v1")["texture"] \
        == "Corrected"
