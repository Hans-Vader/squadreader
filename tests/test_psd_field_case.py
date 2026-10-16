"""PlayerStateData fields survive a rename that only changes letter case.

A Squad update (10-2026) renamed FireTeamIndex to FireteamIndex — same offset
(0x38 in the live layout), same IntProperty. The reader asked for the old
spelling, got nothing, and fireTeamIndex went null for every player: no
fireteam leader's map marker could be attributed any more.
"""
from __future__ import annotations

import struct
from types import SimpleNamespace

from sqreader.squad.snapshot import read_player

PS = 0x7000_0000
PSD_OFF = 0x6D0


class FakeMem:
    def __init__(self) -> None:
        self.regions: dict[int, bytes] = {}

    def try_read(self, addr: int, size: int) -> bytes | None:
        for base, data in self.regions.items():
            if base <= addr and addr + size <= base + len(data):
                return data[addr - base:addr - base + size]
        return None

    def _r(self, addr: int, size: int) -> bytes:
        b = self.try_read(addr, size)
        if b is None:
            raise OSError(f"unmapped {addr:#x}")
        return b

    def read_u8(self, a: int) -> int:
        return self._r(a, 1)[0]

    def read_i32(self, a: int) -> int:
        return struct.unpack("<i", self._r(a, 4))[0]

    def read_u64(self, a: int) -> int:
        return struct.unpack("<Q", self._r(a, 8))[0]


def player_with(psd_offsets: dict[str, int]) -> dict:
    mem = FakeMem()
    block = bytearray(0x80)
    struct.pack_into("<i", block, 0x0C, 3)    # NumDeaths
    struct.pack_into("<i", block, 0x38, 1)    # fire-team index
    struct.pack_into("<i", block, 0x3C, 0)    # fire-team position
    mem.regions[PS] = bytes(0x6D0) + bytes(block)
    paths = SimpleNamespace(
        ps_offsets={"PlayerStateData": PSD_OFF},
        psd_offsets=psd_offsets,
        ps_bool_masks={},
        pc_stats_index_off=None,
        pc_voice_channel_off=None,
    )
    return read_player(mem, None, None, paths, PS)  # type: ignore[arg-type]


def test_renamed_fireteam_index_is_still_read():
    out = player_with({"NumDeaths": 0x0C, "FireteamIndex": 0x38, "FireTeamPosition": 0x3C})
    assert out["stats"]["fireTeamIndex"] == 1
    assert out["stats"]["fireTeamPosition"] == 0
    assert out["stats"]["deaths"] == 3


def test_the_old_spelling_still_works():
    out = player_with({"NumDeaths": 0x0C, "FireTeamIndex": 0x38, "FireTeamPosition": 0x3C})
    assert out["stats"]["fireTeamIndex"] == 1


def test_a_field_that_is_really_gone_stays_absent():
    out = player_with({"NumDeaths": 0x0C, "FireTeamPosition": 0x3C})
    assert out["stats"]["fireTeamIndex"] is None
