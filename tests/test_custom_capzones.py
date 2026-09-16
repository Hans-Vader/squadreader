"""Tests for the custom_capzones.json overlay — cap-zone shapes for a layer
scripts/fetch_capzones.py will never see.

That script walks layer_bounds.json and rewrites capzones.json wholesale, so a
modded layer is absent from both and an entry pasted into the generated file
disappears on the next run. The overlay is the hand-written side of it, keyed by
LAYER name (unlike custom_maps.json, which is keyed by map name) because the
geometry is per layer: RAAS v1 and RAAS v2 are different flag sets.

The spelling is the same coin-flip as in custom_maps: the layer dump says
`SU_Hrodna_Border_RAAS_v2`, the live reader may say `SU Hrodna Border RAAS v2`,
and nobody knows which until the map has run. So the key is normalised.
"""
import json

from sqreader.squad.metadata import Metadata

ZONE = {"name": "Kowale Hill", "cluster": "A1",
        "position": {"x": -92905.4, "y": -148918.5},
        "geometry": [{"type": "sphere", "dx": 0.0, "dy": 0.0,
                      "radius": 7610.2}]}
CUSTOM = {"SU_Hrodna_Border_RAAS_v2": [ZONE]}
STOCK = {"Narva RAAS v1": [{"name": "Church", "cluster": "A2",
                            "position": {"x": 0, "y": 0}, "geometry": []}]}


def _md(tmp_path, custom=CUSTOM, stock=STOCK) -> Metadata:
    (tmp_path / "capzones.json").write_text(json.dumps(stock))
    if custom is not None:
        (tmp_path / "custom_capzones.json").write_text(json.dumps(custom))
    return Metadata.load(tmp_path)


def test_spaced_layer_name_finds_the_underscored_key(tmp_path):
    md = _md(tmp_path)
    assert md.capzones_for("SU Hrodna Border RAAS v2") == [ZONE]


def test_exact_key_hit(tmp_path):
    md = _md(tmp_path)
    assert md.capzones_for("SU_Hrodna_Border_RAAS_v2") == [ZONE]


def test_another_layer_of_the_same_map_is_not_covered(tmp_path):
    # Per layer, not per map: v1 has its own flags and we were given none.
    md = _md(tmp_path)
    assert md.capzones_for("SU Hrodna Border RAAS v1") == []


def test_stock_layers_still_resolve(tmp_path):
    md = _md(tmp_path)
    assert md.capzones_for("Narva RAAS v1")[0]["name"] == "Church"


def test_no_custom_file_changes_nothing(tmp_path):
    md = _md(tmp_path, custom=None)
    assert md.capzones_for("Narva RAAS v1")[0]["name"] == "Church"
    assert md.capzones_for("SU Hrodna Border RAAS v2") == []


def test_custom_entry_overrides_a_stock_layer(tmp_path):
    # Naming a stock layer in full is deliberate — the only way to fix geometry
    # SquadCalc has wrong.
    md = _md(tmp_path, custom={"Narva RAAS v1": [ZONE]})
    assert md.capzones_for("Narva RAAS v1") == [ZONE]


def test_malformed_entries_are_skipped_not_fatal(tmp_path):
    md = _md(tmp_path, custom={"Broken": "not a list", "SU_Hrodna_Border_RAAS_v2": [ZONE]})
    assert md.capzones_for("Broken") == []
    assert md.capzones_for("SU Hrodna Border RAAS v2") == [ZONE]


def test_unknown_layer_and_none_are_empty(tmp_path):
    md = _md(tmp_path)
    assert md.capzones_for("Some Other Map RAAS v1") == []
    assert md.capzones_for(None) == []
