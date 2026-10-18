"""Rows that come from memory which is no longer what the walk thought it was.

b0db7147's first frame (Fallujah, one second in) carried a "player" with an
empty id, a name of stray characters, team 21159352 and a 396-character role
id, its soldier attached at the world origin; the viewer listed it as the
passenger of an emplaced ZU-23 there. Two vehicle spawners in the same frame
had class names of the same garbage.
"""
from __future__ import annotations

from sqreader.squad.snapshot import (
    _ident_or_none,
    drop_phantom_entities,
    plausible_player,
)

# What that role id really was: pool bytes of "ObjectProperty",
# "NameProperty", ... decoded two at a time as UTF-16.
GARBAGE_ROLE = "敪瑣牐灯牥祴̄慎敭牐"


def test_a_team_in_the_millions_is_not_a_player():
    assert not plausible_player({"teamId": 21159352, "name": "瀸놫"})
    assert not plausible_player({"teamId": -7})


def test_real_and_joining_teams_are_kept():
    for team in (1, 2, 0, -1, None):
        assert plausible_player({"teamId": team, "name": "MaG"})


def test_a_transform_at_exactly_the_origin_is_no_position():
    """The emplaced ZU-23 base sat at (0, 0, 0) for the whole match."""
    from types import SimpleNamespace

    from sqreader.squad import snapshot as snap

    paths = SimpleNamespace(actor_root_component_off=0x10, scene_attach_parent_off=0x20,
                            scene_component_to_world_translation_off=0x30)
    pm = SimpleNamespace(read_u64=lambda a: 0x1000 if a == 0x5010 else 0)
    orig_vec, orig_yaw = snap.read_fvector, snap._world_yaw
    try:
        snap._world_yaw = lambda pm, root, paths: 0.0
        snap.read_fvector = lambda pm, a: SimpleNamespace(x=0.0, y=0.0, z=0.0)
        assert "position" not in snap.read_root_pos_yaw(pm, 0x5000, paths)
        snap.read_fvector = lambda pm, a: SimpleNamespace(x=0.0, y=12.5, z=0.0)
        assert snap.read_root_pos_yaw(pm, 0x5000, paths)["position"]["y"] == 12.5
    finally:
        snap.read_fvector, snap._world_yaw = orig_vec, orig_yaw


def test_identifier_fnames_pass_and_garbage_does_not():
    assert _ident_or_none("USA_Rifleman_01") == "USA_Rifleman_01"
    assert _ident_or_none("BP_LAV25_C") == "BP_LAV25_C"
    assert _ident_or_none("Weapon.M4-A1") == "Weapon.M4-A1"
    assert _ident_or_none(GARBAGE_ROLE) is None
    assert _ident_or_none("x" * 200) is None
    assert _ident_or_none("") is None
    assert _ident_or_none(None) is None


def test_markers_and_projectiles_of_a_map_change_are_left_out():
    """b0db7147 frame 0: seven "None" markers (squad 5017313, owner 0xf) and
    "None" projectiles; every real one has a class."""
    markers = [
        {"type": "BP_MapMarker_Action_MoveSL", "squad": 3, "position": {"x": 5.0, "y": 6.0, "z": 0.0}},
        {"type": "None", "squad": 5017313, "position": {"x": -21.4, "y": -21.5, "z": -80.0}},
        {"type": "BP_MapMarker_EnemyDirector", "squad": 911659},
        {"type": None, "squad": 0},
    ]
    projectiles = [{"classShort": "BP_Projectile_762_C"}, {"classShort": "None"}, {"classShort": None}]
    crate = {"classShort": "BP_Ammocrate_C", "position": {"x": 0.0, "y": 0.0, "z": 0.0}}
    spawner = {"classShort": "BP_VehicleSpawner_C", "position": {"x": 0.0, "y": 0.0, "z": 6.9e-310}}
    fob = {"classShort": "BP_FOBRadio_RGF_C", "position": {"x": 0.0, "y": 40.0, "z": 0.0}}
    mk, pr = drop_phantom_entities(markers, projectiles, [crate, fob], [spawner])
    assert [m["type"] for m in mk] == ["BP_MapMarker_Action_MoveSL"]
    assert [p["classShort"] for p in pr] == ["BP_Projectile_762_C"]
    assert "position" not in crate and "position" not in spawner
    assert fob["position"]["y"] == 40.0
