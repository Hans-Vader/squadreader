"""
Static metadata loader for Squad display-name enrichment.

Pulls four JSON files from squadreplay.com (cached locally under
sqreader/data/static/). The reader uses them to attach small
display-friendly hints onto each snapshot WITHOUT bloating the
NDJSON line — most metadata tables are sent to the frontend once
separately, only per-entity lookups land in the per-tick output.

Source URLs (downloaded once, hand-refresh on Squad updates):
  https://cota.squadreplay.com/squad_pools.json
  https://cota.squadreplay.com/static/data/vehicle_factions.json
  https://cota.squadreplay.com/api/map-config
  https://cota.squadreplay.com/api/layer-bounds

A fifth table, capzones.json (static cap-zone geometry), is produced locally
by scripts/fetch_capzones.py from SquadCalc rather than downloaded here.

A sixth, custom_maps.json, is written by hand and is the only one that is
OPTIONAL: it carries workshop/modded maps, which none of the upstream sources
know about. See `_load_custom_maps` for the format.

This module is intentionally read-only and side-effect free at import
time. Callers do `meta = load_metadata()` once at startup.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _default_data_dir() -> Path:
    """Locate ``data/static``, in a source tree *or* a compiled binary.

    Source layout puts it two levels above this module. A Nuitka onefile build
    unpacks the bundled copy next to the module tree, which usually lines up —
    but ``__file__`` inside an extracted onefile is not something to bet the
    map rendering on, and a miss here fails *silently*: `_load_json` returns
    None for every table, metadata comes back empty, and maps just don't
    render with nothing in the log to explain it.

    So try the candidates in order and return the first that really exists,
    falling back to the source-relative guess so behaviour is unchanged when
    nothing is bundled.
    """
    here = Path(__file__).resolve()
    candidates = [here.parents[2] / "data" / "static"]
    # Compiled: alongside the executable, and alongside the extraction root.
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        exe = Path(sys.executable).resolve().parent
        candidates += [exe / "data" / "static",
                       here.parents[1] / "data" / "static"]
    # Explicit escape hatch for odd deployments.
    env = os.environ.get("SQREADER_DATA_DIR")
    if env:
        candidates.insert(0, Path(env))
    for c in candidates:
        if c.is_dir():
            return c
    return candidates[-1] if env else here.parents[2] / "data" / "static"


DEFAULT_DATA_DIR = _default_data_dir()


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


log = logging.getLogger(__name__)

_NORM_RE = re.compile(r"[^a-z0-9]")
# Must agree with httpsrv._SQMAP_NAME_RE: a texture that fails there is
# refused with a 400 the admin never sees, and the map silently stays blank.
_TEXTURE_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# Shorter than this and a key stops being a map name and starts being a
# wildcard: "AB" would match "Abandoned Quarry RAAS v1".
_MIN_CUSTOM_KEY = 3


def _norm(s: str) -> str:
    """Letters and digits only, lowercased.

    The admin writing custom_maps.json cannot know how the game spells the
    layer — `Hrodna_Border_RAAS_v1` and `Hrodna Border RAAS v1` are both
    plausible and only one is real. Normalising both sides makes the question
    moot.
    """
    return _NORM_RE.sub("", s.lower())


def _corner(v: Any) -> dict[str, float] | None:
    """An {x, y} pair of finite numbers, or None if it is anything else."""
    if not isinstance(v, dict):
        return None
    x, y = v.get("x"), v.get("y")
    # bool is an int; a corner of {"x": true} is a typo, not a coordinate.
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    if isinstance(y, bool) or not isinstance(y, (int, float)):
        return None
    if not (math.isfinite(x) and math.isfinite(y)):
        return None
    return {"x": float(x), "y": float(y)}


def _custom_entry(key: str, entry: Any) -> tuple[str, dict[str, Any]] | None:
    """One validated custom_maps entry, or None with the reason logged.

    Validation is not politeness here. An entry that reaches the snapshot
    missing a corner still produces a `layer` block, and the viewer treats any
    layer block as authoritative — `snap.gameState?.layer ?? fallbackMap(...)`
    in draw.ts never reaches the fallback once the left side is an object. So a
    half-written entry renders WORSE than no entry at all, and the one thing
    this must not do is accept one.
    """
    if not isinstance(entry, dict):
        log.warning("custom_maps: ignoring %r — value is not an object", key)
        return None
    norm = _norm(key)
    if len(norm) < _MIN_CUSTOM_KEY:
        log.warning("custom_maps: ignoring %r — the key needs at least %d "
                    "letters or digits", key, _MIN_CUSTOM_KEY)
        return None
    texture = entry.get("texture")
    if not isinstance(texture, str) or not _TEXTURE_RE.match(texture):
        log.warning("custom_maps: ignoring %r — texture %r must be the image's "
                    "bare filename, no extension and no spaces (%s)",
                    key, texture, _TEXTURE_RE.pattern)
        return None
    top_left, bottom_right = _corner(entry.get("topLeft")), \
        _corner(entry.get("bottomRight"))
    if top_left is None or bottom_right is None:
        log.warning('custom_maps: ignoring %r — topLeft and bottomRight must '
                    'both be {"x": <number>, "y": <number>}', key)
        return None
    # mapName/mapId are read downstream (to_raw_layer_key, the heatmap's
    # bounds) and the admin has no reason to know that, so fill them in from
    # the key they did write. The validated values are re-pinned last so the
    # entry cannot carry a rejected shape through.
    return norm, {"mapName": key, "mapId": key.replace(" ", ""), **entry,
                  "texture": texture,
                  "topLeft": top_left, "bottomRight": bottom_right}


def _load_custom_maps(path: Path) -> list[tuple[str, dict[str, Any]]]:
    """Workshop/modded map bounds, as (normalised key, entry) longest-first.

    The file is optional and hand-written, keyed by MAP name so one entry
    covers every layer of that mod::

        {"Hrodna Border": {"texture": "HrodnaBorder",
                           "topLeft":     {"x": -200000, "y": -200000},
                           "bottomRight": {"x":  200000, "y":  200000}}}

    Sorted longest-first so `Hrodna Border Night` beats `Hrodna Border` on a
    layer name both match. Bad entries are dropped rather than raised on: this
    runs at reader startup, and a typo here must cost one map, not the match.
    """
    raw = _load_json(path)
    if not isinstance(raw, dict):
        return []
    by_norm: dict[str, dict[str, Any]] = {}
    for key, entry in raw.items():
        hit = _custom_entry(key, entry)
        if hit is None:
            continue
        norm, value = hit
        if norm in by_norm:
            # "Hrodna Border" and "Hrodna_Border" are one key to us. An admin
            # hedging between the two spellings would otherwise keep editing
            # the dead one and see nothing change, which is precisely the
            # confusion this file exists to end.
            log.warning("custom_maps: %r replaces an earlier entry that spells "
                        "the same map differently", key)
        by_norm[norm] = value
    return sorted(by_norm.items(), key=lambda kv: len(kv[0]), reverse=True)


@dataclass
class Metadata:
    # Raw tables (kept around so callers can pass them to the frontend
    # once at session start; never re-emitted per snapshot).
    vehicle_factions: dict[str, list[str]] = field(default_factory=dict)
    squad_pools: dict[str, Any] = field(default_factory=dict)
    map_config: dict[str, Any] = field(default_factory=dict)
    layer_bounds: dict[str, Any] = field(default_factory=dict)
    # Static cap-zone geometry per layer, produced offline by
    # scripts/fetch_capzones.py from SquadCalc. Keyed by full display layer
    # name (same keys as layer_bounds). Missing file → {} → merge is a no-op.
    capzones: dict[str, Any] = field(default_factory=dict)
    # Hand-written workshop/modded map bounds, (normalised key, entry) pairs
    # longest-first. Optional — missing file → [] → lookups are unchanged.
    custom_maps: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    # Derived reverse indices, built once at construction:
    _role_keyword_to_pool: dict[str, tuple[str, str]] = field(default_factory=dict)
    # vehicle_pools sub-table: { short_key: kind } e.g. {"2A6_Desert": "MBT"}
    _vehicle_pools: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, data_dir: Path | None = None) -> "Metadata":
        d = data_dir or DEFAULT_DATA_DIR
        m = cls(
            vehicle_factions=_load_json(d / "vehicle_factions.json") or {},
            squad_pools=_load_json(d / "squad_pools.json") or {},
            map_config=_load_json(d / "map_config.json") or {},
            layer_bounds=_load_json(d / "layer_bounds.json") or {},
            capzones=_load_json(d / "capzones.json") or {},
            custom_maps=_load_custom_maps(d / "custom_maps.json"),
        )
        # Build derived indices
        for pool_key, pool in (m.squad_pools.get("infantryPools") or {}).items():
            label = pool.get("label") or pool_key
            for role_kw in pool.get("roles", []):
                m._role_keyword_to_pool[role_kw.lower()] = (pool_key, label)
        # Also fold the pool key itself in lowercase as a fallback
        # (so "LAT" in "USMC_LAT_01" matches infantryPools["LAT"] directly).
        for pool_key, pool in (m.squad_pools.get("infantryPools") or {}).items():
            m._role_keyword_to_pool.setdefault(
                pool_key.lower(), (pool_key, pool.get("label") or pool_key))
        m._vehicle_pools = m.squad_pools.get("vehiclePools") or {}
        return m

    # ---- per-entity lookups (used at snapshot time) -----------------------

    def vehicle_faction(self, class_short: str | None) -> list[str] | None:
        if not class_short:
            return None
        # vehicle_factions is keyed by exact BP class name including _C suffix
        return self.vehicle_factions.get(class_short)

    def vehicle_kind(self, class_short: str | None) -> str | None:
        """Returns the high-level kind ('MBT', 'APC', 'Transport', ...)."""
        if not class_short:
            return None
        # vehiclePools is keyed by short name: BP_<short>_C
        if class_short.startswith("BP_") and class_short.endswith("_C"):
            short = class_short[3:-2]
            kind = self._vehicle_pools.get(short)
            if kind:
                return kind
        return None

    def role_pool(self, role_id: str | None) -> dict[str, str] | None:
        """
        Map a Squad role FName (e.g. 'USMC_LAT_01' / 'USMC_Rifleman_01')
        to its infantry pool ({key, label}). Returns None if unrecognized.

        Strategy: tokenize by '_' and check each token (lowercased) against
        the reverse index built from infantryPools.{roles, key-as-self}.
        """
        if not role_id or role_id == "None":
            return None
        for tok in role_id.split("_"):
            hit = self._role_keyword_to_pool.get(tok.lower())
            if hit:
                key, label = hit
                return {"key": key, "label": label}
        return None

    def map_bounds(self, map_id: str | None) -> dict[str, Any] | None:
        if not map_id:
            return None
        return self.map_config.get(map_id)

    def layer_bounds_for(self, layer_name: str | None) -> dict[str, Any] | None:
        """Bounds + minimap texture for a layer, or None if we don't know it.

        Three passes: an exact custom key, then the stock table, then a loose
        custom match. So a custom entry can only shadow a stock layer by naming
        it exactly — never by merely appearing inside its name — while the
        loose pass still catches everything a modded layer name carries.

        That last pass matches a substring rather than a prefix on purpose: a
        community puts its tag in FRONT of the map name ("SEC 26 Sumari AAS
        v1"), so anchoring at the start would miss exactly the names this is
        for. It is safe to be loose there because it runs only after the stock
        table has already declined the name.
        """
        if not layer_name:
            return None
        if not self.custom_maps:
            return self.layer_bounds.get(layer_name)
        norm = _norm(layer_name)
        for key, entry in self.custom_maps:
            if key == norm:
                return entry
        stock = self.layer_bounds.get(layer_name)
        if stock is not None:
            return stock
        for key, entry in self.custom_maps:
            # Longest-first, so the first hit is the most specific one.
            if key in norm:
                return entry
        return None

    def capzones_for(self, layer_name: str | None) -> list[dict[str, Any]]:
        """Static cap-zone points for a layer, or [] if none/unknown.

        Keyed identically to layer_bounds (full display layer name), so the
        caller passes the same game_state["mapName"] it uses for bounds — no
        RawLayerKey conversion at runtime (that happens once, offline).
        """
        if not layer_name:
            return []
        pts = self.capzones.get(layer_name)
        return pts if isinstance(pts, list) else []


__all__ = ["Metadata", "DEFAULT_DATA_DIR"]
