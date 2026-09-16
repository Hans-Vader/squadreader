# sqreader

Read a running Squad dedicated server's process memory to produce game
snapshots — players, vehicles, capture zones, deployables, projectiles — then
record whole matches for replay and compute per-player stats and ELO. It is
**read-only**: it never writes to the game.

## Example output

`sqreader snapshot --pretty` prints one JSON snapshot of the live match:

```json
{
  "timestamp": "2026-07-15T18:31:24.082Z",
  "server": "squad",
  "gameState": { "mapName": "Narva RAAS v1", "matchState": "InProgress", "elapsedSec": 842 },
  "teams": [ { "id": 1, "factionId": "USA", "tickets": 640 } ],
  "players": [ { "name": "…", "teamId": 1, "soldier": { "position": { "x": 0, "y": 0 }, "health": 100 } } ],
  "captureZones": [ { "name": "Warehouse", "owningTeam": 1, "capturePercent": 1.0 } ],
  "vehicles": [ { "classShort": "BP_M1A2", "team": 1 } ]
}
```

`sqreader serve` records matches and serves the replay player + stats dashboard
in the browser: a map with every player, vehicle, marker and capture zone
moving in real time, a scrubbable timeline, a kill feed and a scoreboard.

You can watch one without installing anything — the replays at
[squadreader.com/replays](https://squadreader.com/replays) are produced by this
agent and played back by the viewer in `frontend/`.

## Requirements

- **Linux** — the reader depends on `/proc/<pid>/mem`; it does not run on Windows or macOS.
- **Python ≥ 3.10.**
- Permission to read the game process's memory: run as **root**, or grant the Python process `CAP_SYS_PTRACE` (and `CAP_DAC_READ_SEARCH`).
- A running **Squad dedicated server** on the same host. Offsets are reverse-engineered for Squad **v10.4 / SDK v10.4.1**.
- Node ≥ 18 **only** if you want to rebuild the web UI — a prebuilt `frontend/dist` is committed, so normal use needs no Node.

## How a match is recorded

Two tiers, so the replay is smooth without the reader stealing the box:

- a **full snapshot** about once a second — every player, vehicle, deployable,
  marker, capture zone and projectile;
- **position-only frames at 4 Hz** in between, re-reading just where everything
  is, so movement plays back fluidly.

Both go into one `.sqrx` per match (zstd-compressed NDJSON, ~6-10x smaller than
raw). A recording is written once and never edited, and nothing is ever
interpolated: an entity that fails a freshness check is left out of that frame
rather than guessed at.

## Install

```bash
git clone https://github.com/cagrianilokumus/squadreader.git
cd squadreader
pip install -e .

# one-line summary of the current match (quickest sanity check)
sudo sqreader summary

# record matches + serve replays/stats (default http://127.0.0.1:8080)
sudo sqreader serve
```

On a standard single-instance box **no configuration is needed** — the Squad
server is auto-detected by its process name.

## Configuration

Copy `sqreader.config.example.json` to `sqreader.config.json` (gitignored) and
edit only what your box needs. Resolution order is **CLI flag > config file >
built-in default**, so every value can also be passed on the command line.

| Key | Default | Meaning |
|-----|---------|---------|
| `squad_process_name` | `SquadGameServer` | the Squad server binary name (a Squad constant) |
| `squad_binary_pattern` | `/home/.*/serverfiles/.*SquadGameServer` | pgrep pattern to pick the right instance on a multi-instance box |
| `squad_log_glob` | `/home/*/serverfiles/…/SquadGame.log` | server log the kill-feed reads |
| `server_id` | `squad` | label written into each snapshot and used as the stats-DB partition key |

Output directories are `serve`/`record` flags (`--recordings-dir`, `--stats-db`,
`--icons-dir`, `--sqmaps-dir`, `--frontend-dir`) and default next to the repo.
Example systemd units and an nginx reverse-proxy are in [`deploy/`](deploy/).

### Modded / Steam Workshop maps

The bundled map table covers the stock layers. A workshop map is not in it, so
the recorder attaches no layer to its frames and the viewer draws a bare grid —
everything else (players, vehicles, markers, kill feed, stats) works as normal.

To give a modded map its minimap, add it to `data/static/custom_maps.json`
(create it; it is optional and loaded only if present):

```json
{
  "Hrodna Border": {
    "texture":     "HrodnaBorder",
    "topLeft":     { "x": -200000, "y": -200000 },
    "bottomRight": { "x":  200000, "y":  200000 }
  }
}
```

Then drop the minimap image next to the stock ones as
`sqmaps/HrodnaBorder.webp` (`.png`, `.jpg` also work).

- **The key is normally the MAP name, not the layer name** — one entry covers
  every RAAS/AAS/Invasion/Seed layer of that mod (a single layer can still
  claim its own entry, see below). Matching ignores case, spaces and underscores
  and tolerates a community tag in front, so `Hrodna Border` finds both
  `Hrodna_Border_RAAS_v1` and `SEC 26 Hrodna Border RAAS v1`. A more specific
  key wins (`Hrodna Border Night` beats `Hrodna Border`), and keys under three
  characters are ignored so a typo cannot swallow unrelated maps.
- **`texture` is the filename without extension** and must match
  `[A-Za-z0-9_-]+` — no spaces.
- **`topLeft`/`bottomRight` are the minimap's world corners in centimetres.**
  The SDK requires them to form a square. Ask the mod author for the exact
  values; failing that, a centred square of the advertised map size is a good
  first guess (4 km → `±200000`), then check a replay and adjust.

A **layer** name as the key also works and beats the map key. That is for Seed
and Skirmish layers: Squad plays those on a cropped minimap, so their extent is
a small box inside the map. Only write one if you have that cropped image — and
set `mapName`/`mapId` as well, or the layer is filed under its own name instead
of under its map in recordings and stats. Without an entry the layer falls back
to the map key and draws on the full minimap: correct, just zoomed out.

What a layer entry must never do is carry the cropped corners from a layer dump
while pointing at the full-map texture. The image is stretched across whatever
bounds the entry gives it, so the whole map ends up squeezed into the small box.

An entry that is missing its corners, or whose `texture` the server would
refuse, is dropped with a warning at startup rather than used — a half-written
entry hides the map the viewer would otherwise have guessed. Changes are read at
startup, so restart the reader.

#### Cap zones on a modded RAAS layer

The flag shapes come from `data/static/capzones.json`, which
`scripts/fetch_capzones.py` regenerates from the stock layer list — a modded
layer is never in it, and an entry pasted in there is gone after the next run.
Put them in `data/static/custom_capzones.json` instead (also optional, also
hand-written), keyed by **layer** name, same shape as the generated file:

```json
{
  "SU Hrodna Border RAAS v2": [
    {
      "name": "Kowale Hill",
      "cluster": "A1",
      "position": { "x": -92905.4, "y": -148918.5 },
      "geometry": [{ "type": "sphere", "dx": 0, "dy": 0, "radius": 7610.2 }]
    }
  ]
}
```

This one is keyed per layer, not per map: RAAS v1 and v2 are different flag
sets, so each layer needs its own entry. Case, spaces and underscores are
ignored — the spelling in a layer dump (`SU_Hrodna_Border_RAAS_v2`) matches what
the game reports — but unlike the map keys above, the key is the WHOLE layer
name: a community tag in front of it is not tolerated here. An entry wins over
the generated table. If you have the mod's layer dump in SquadCalc's
`/api/get/layer` shape, `extract_capzone_data` in `sqreader/squad/capzones.py`
turns it into exactly this list.

Known gaps:

- **Cap-zone geometry has no upstream source for modded layers.** SquadCalc,
  which feeds the generated table, does not carry workshop layers, so RAAS
  flags stay unrendered until someone writes the file above. AAS layers are
  unaffected — there the live capture zones carry their own positions.
- **Overriding a stock layer** (by naming it exactly) replaces its extent, but
  its cap zones keep the stock layer's coordinates unless you override those in
  `custom_capzones.json` as well. Expect the flags to sit wrong otherwise.

## What data it collects and where it writes

The reader only observes what the game already holds in memory, and **by
default writes only to local files — nothing is sent anywhere.** The single
opt-in exception is the optional central push: if you run `sqreader enroll`,
*finished* matches (stats + `.sqrx` replays) are pushed to a
central platform you chose. See [`PRIVACY.md`](PRIVACY.md) for exactly what is
sent and how to turn it off.

| Data | Source | Written to |
|------|--------|------------|
| Player names, EOS ids, positions, kills/deaths, roles | game memory | `stats/player_stats.db` (SQLite) + snapshots |
| Steam IDs | RCON (only if you configure it) | `stats/player_stats.db` |
| Full per-tick match capture | game memory | `recordings/*.sqrx` (+ `.meta.json`) |
| Ad-hoc snapshots | `snapshot` / `watch` | `captures/*.ndjson` |

See [PRIVACY.md](PRIVACY.md) for what is stored, how long, and how to delete it.

## Known limitations

- **Linux only** (depends on `/proc/<pid>/mem`).
- **Squad-version-specific.** Memory offsets are reverse-engineered for Squad v10.4 / SDK v10.4.1. A Squad update can move them — `sqreader doctor` re-verifies every offset against the live binary and reports drift, and startup discovery self-heals the two anchor addresses; a larger layout change needs new offsets.
- **Anti-cheat detectors have blind spots.** They flag only memory-verified signals (no guessing), so many cheat classes are simply not detectable this way.
- **One game server per reader instance.**
- **Modded maps need a hand-written entry.** Bounds, minimap and cap-zone geometry for a workshop map cannot be derived from the game; see [Modded / Steam Workshop maps](#modded--steam-workshop-maps).

## Legal

Use this only on a Squad server you **own or are authorized to administer**, and
in accordance with Squad's Terms of Service and EULA. The game art under
`icons/`, `sqmaps/`, and the static data under `data/static/` are the property
of Offworld Industries and their respective sources, bundled for interoperability
only — see [NOTICE](NOTICE).

## Upgrading

Re-run the installer. It is safe to run again: recordings, stats and an existing
enrolment are left alone, and your `sqreader.config.json` is not overwritten.

```
curl -fsSL https://squadreader.com/install.sh | bash
```

A box that is already enrolled needs no token — the installer signs the download
with the agent's own credentials. From 1.3.2 onward the installed unit can apply
a signed release on its own: the agent stages it, waits for a moment when no
match is in progress, and restarts into it, keeping the previous binary beside
it in case the new one will not start.

## Contributing & License

- [CONTRIBUTING.md](CONTRIBUTING.md) — dev setup, tests, and the DCO sign-off.
- [SECURITY.md](SECURITY.md) — reporting a vulnerability.
- **AGPL-3.0-or-later with the Commons Clause** — see [LICENSE](LICENSE).

  In plain terms: read it, run it, change it, share it — including at work.
  Just do not **sell** it, and that includes selling hosting or support whose
  value comes substantially from this tool. Running a game server that happens
  to use sqreader is not selling it, donations and paid whitelists included:
  the value there is the server, not this.

  Everything the AGPL says still holds. If you modify it and let other people
  use it over a network, you owe them your modified source.

  The added condition means sqreader is **source-available, not open source**
  in the Open Source Initiative's sense. That is deliberate.
