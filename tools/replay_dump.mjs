// Re-simulate a .repl26a replay with the visualiser's engine (WebAssembly) and dump, per champion
// turn, what that champion could know plus the actions it took. Driven by tools/replay_dump.py.
//
//   node tools/replay_dump.mjs <replay.repl26a> <engine.wasm> <viewer-chunks-dir> <out.jsonl> [--probe] [--old-rules]
//
// Output: one JSON object per line. The first is {"kind":"static", ...} with the map, camps,
// groups, spawn area and precomputed distance fields; then one {"kind":"turn", ...} per champion
// turn, holding the state from that champion's perspective (fog of war as the engine applies it),
// every camp's health, and the inputs the champion made before its turn ended; last an "end"
// record. The snapshot decoders are imported from the viewer bundle so no layout is transcribed.
//
// Engine conventions found by probing: Managed_Bitboard_Union(a, b, out); the distance field is
// Managed_Bitboard_DistanceField(walls, roots, visited, field, max); camp and group accessors take
// 64-bit handles; Game_GetCamp lists only the map builder's half of a symmetric map, so camps come
// from the timeline snapshot and groups from cardinal connectivity.
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const [replayPath, wasmPath, chunksDir, outPath, ...flags] = process.argv.slice(2);
const PROBE = flags.includes("--probe");
const OLD_RULES = flags.includes("--old-rules");
const panel = await import(pathToFileURL(path.join(chunksDir, "PanelChrome-BXlTYAwV.js")).href);
const decodeTimeline = panel.I;   // 40152-byte timeline snapshot
const decodeState = panel.F;      // full game snapshot from a perspective (fog of war applied)

// Gems, gold and respawn per camp type (wolf, elk, gem cluster, gold cluster, monolith, golem).
const RULES = OLD_RULES
  ? { 0: [22, 90, 40], 1: [36, 160, 60], 2: [24, 0, 50], 3: [0, 35, 50], 4: [200, 300, 150], 5: [36, 160, 80] }
  : { 0: [4, 40, 40], 1: [15, 90, 60], 2: [7, 0, 50], 3: [0, 20, 50], 4: [200, 300, 150], 5: [36, 160, 80] };

// ---- engine ------------------------------------------------------------------------------------
const textDecoder = new TextDecoder();
let memory;
const view = () => new DataView(memory.buffer);
const wasi = {
  random_get(ptr, len) { crypto.getRandomValues(new Uint8Array(memory.buffer, ptr, len)); return 0; },
  clock_time_get(id, prec, out) { view().setBigUint64(out, BigInt(Date.now()) * 1000000n, true); return 0; },
  environ_sizes_get(a, b) { view().setUint32(a, 0, true); view().setUint32(b, 0, true); return 0; },
  environ_get() { return 0; },
  args_sizes_get(a, b) { view().setUint32(a, 0, true); view().setUint32(b, 0, true); return 0; },
  args_get() { return 0; },
  fd_close() { return 0; },
  fd_fdstat_get(fd, out) { const v = view(); v.setUint8(out, 2); v.setUint16(out + 2, 0, true); v.setBigUint64(out + 8, 0n, true); v.setBigUint64(out + 16, 0n, true); return 0; },
  fd_prestat_get() { return 8; },
  fd_prestat_dir_name() { return 8; },
  fd_read(fd, iovs, n, out) { view().setUint32(out, 0, true); return 0; },
  fd_seek() { return 70; },
  fd_write(fd, iovs, n, out) {
    const v = view(); let total = 0; let text = "";
    for (let i = 0; i < n; i++) { const p = v.getUint32(iovs + i * 8, true), l = v.getUint32(iovs + i * 8 + 4, true); text += textDecoder.decode(new Uint8Array(memory.buffer, p, l)); total += l; }
    if (text.trim()) process.stderr.write(text);
    v.setUint32(out, total, true); return 0;
  },
  proc_exit(code) { throw new Error(`engine exited with code ${code}`); },
};
const wasmModule = await WebAssembly.compile(fs.readFileSync(wasmPath));
const instance = await WebAssembly.instantiate(wasmModule, { wasi_snapshot_preview1: wasi, deep_wasm: { on_assert_fail_impl: () => 1 } });
const E = instance.exports;
memory = E.memory;
E._initialize();
const outBuf = E.Engine_Malloc(32);
const args = E.Engine_Malloc(128);

function cstring(ptr) { const b = new Uint8Array(memory.buffer, ptr); let n = 0; while (b[n]) n++; return textDecoder.decode(b.subarray(0, n)); }
function check(code) { if (code !== 0) throw new Error(`engine error ${code}: ${cstring(E.GetLastErrorMsg())}`); }
const outHandle = () => view().getBigUint64(outBuf, true);
const outPtr = () => view().getUint32(outBuf, true);
const outU32 = (off = 0) => view().getUint32(outBuf + off, true);
const outI32 = (off = 0) => view().getInt32(outBuf + off, true);

// ---- load the replay and the map ----------------------------------------------------------------
const replayBytes = fs.readFileSync(replayPath);
const rp = E.Engine_Malloc(replayBytes.length);
new Uint8Array(memory.buffer, rp, replayBytes.length).set(replayBytes);
check(E.Game_FromReplayFileBytes(rp, replayBytes.length, outBuf));
const game = outHandle();
E.Engine_Free(rp);

check(E.Game_GetBitboardMetadata(game, outBuf));
const meta = { width: outI32(0), height: outI32(4), chunkedWidth: outU32(8), chunkedHeight: outU32(12), chunkStride: outU32(16) };
const W = meta.width, H = meta.height;
function bitTest(ptr, x, y) {
  const a = (y >> 3) * meta.chunkStride + (x >> 3), o = ((y & 7) << 3) + (x & 7);
  return (view().getBigUint64(ptr + a * 8, true) & (1n << BigInt(o))) !== 0n;
}
function boardToTiles(ptr) { const t = []; for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) if (bitTest(ptr, x, y)) t.push([x, y]); return t; }
function gameBoard(fn) { check(E[fn](game, outBuf)); return boardToTiles(outPtr()); }
const walls = gameBoard("Game_GetWallBuffer"), holes = gameBoard("Game_GetHoleBuffer");
const bushes = gameBoard("Game_GetBushBuffer"), altars = gameBoard("Game_GetAltarBuffer");
check(E.Game_GetTeamASpawnArea(game, outBuf));
const spawnA = { minX: outI32(0), minY: outI32(4), maxX: outI32(8), maxY: outI32(12) };
check(E.Game_GetNumChampsPerTeam(game, outBuf)); const champsPerTeam = outI32(0);

// ---- managed bitboards and distance fields -----------------------------------------------------
function newBoard() { check(E.Managed_Bitboard_Create(W, H, outBuf)); return outHandle(); }
function newField() { check(E.Managed_DistanceField_Create(W, H, outBuf)); return outHandle(); }
function setTiles(board, tiles) { for (const [x, y] of tiles) check(E.Managed_Bitboard_Set(board, x, y, 1)); }
function importRaw(board, fn) {
  check(E[fn](game, outBuf)); const src = outPtr();
  check(E.Managed_Bitboard_GetRawBufferSize(board, outBuf)); const size = outU32();
  const tmp = E.Engine_Malloc(size);
  new Uint8Array(memory.buffer, tmp, size).set(new Uint8Array(memory.buffer, src, size));
  check(E.Managed_Bitboard_ImportRawBuffer(board, tmp, size)); E.Engine_Free(tmp);
}
function setBits(h) { check(E.Managed_Bitboard_NumSetBits(h, outBuf)); return outU32(); }
function fieldLinear(field) {
  check(E.Managed_DistanceField_GetLinearBufferSize(field, outBuf)); const size = outU32();
  const tmp = E.Engine_Malloc(size);
  check(E.Managed_DistanceField_ExportLinearBuffer(field, tmp, size));
  const u16 = Array.from(new Uint16Array(memory.buffer.slice(tmp, tmp + size)));
  E.Engine_Free(tmp); return u16;
}
function distanceField(wallsBoard, rootTiles, max = 65535) {
  const roots = newBoard(); setTiles(roots, rootTiles);
  const visited = newBoard(); const field = newField();
  try {
    check(E.Managed_Bitboard_DistanceField(wallsBoard, roots, visited, field, max));
    return fieldLinear(field);
  } finally {
    for (const h of [roots, visited]) check(E.Managed_Bitboard_Destroy(h));
    check(E.Managed_DistanceField_Destroy(field));
  }
}
const wallsB = newBoard(); importRaw(wallsB, "Game_GetWallBuffer");
const holesB = newBoard(); importRaw(holesB, "Game_GetHoleBuffer");
const altarsB = newBoard(); importRaw(altarsB, "Game_GetAltarBuffer");
const terrain1 = newBoard(); check(E.Managed_Bitboard_Union(wallsB, holesB, terrain1));
const blocked = newBoard(); check(E.Managed_Bitboard_Union(terrain1, altarsB, blocked));
const campBoard = newBoard();
const blockedWithCamps = newBoard();

// ---- timeline -----------------------------------------------------------------------------------
check(E.Game_Visualiser_Timeline_GetMain(game, outBuf)); const timeline = outHandle();
check(E.Game_Visualiser_Timeline_Info(game, timeline, args, args + 8, args + 16));
const size = view().getUint32(args, true);
const snapTimeline = E.Engine_Malloc(E.Game_Visualiser_TimelineSnapshotSize());
const snapState = E.Engine_Malloc(E.Game_Visualiser_SnapshotSize());
function perspective(team, champion) { const p = args + 48; view().setUint32(p, team, true); view().setUint32(p + 4, champion, true); return p; }
function timelineState() { check(E.Game_Visualiser_ReadTimelineState(game, snapTimeline)); return decodeTimeline(view(), snapTimeline); }
function stateFrom(team, champion) { check(E.Game_Visualiser_ReadState(game, perspective(team, champion), snapState)); return decodeState(view(), snapState, undefined); }
function visionTester(team, champion) { check(E.Game_Visualiser_GetVisionBuffer(game, perspective(team, champion), outBuf)); const p = outPtr(); return (x, y) => bitTest(p, x, y); }

// Camps and groups from the first snapshot (the whole map, both halves).
check(E.Game_Visualiser_Timeline_Seek(game, timeline, 0, 0));
const camps = timelineState().camps.filter(c => c.position).map((c, i) => ({ index: i, type: c.type, x: c.position.x, y: c.position.y, group: -1 }));
const groups = [];
{
  const byPos = new Map(camps.map(c => [`${c.x},${c.y}`, c]));
  for (const c of camps) {
    if (c.group >= 0) continue;
    const g = { index: groups.length, gems: 0, gold: 0, cooldown: 0, camps: [] };
    groups.push(g);
    const stack = [c];
    while (stack.length) {
      const d = stack.pop();
      if (d.group >= 0) continue;
      d.group = g.index; g.camps.push(d.index);
      const r = RULES[d.type] || [0, 0, 50];
      g.gems += r[0]; g.gold += r[1]; g.cooldown = Math.max(g.cooldown, r[2]);
      for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) { const n = byPos.get(`${d.x + dx},${d.y + dy}`); if (n && n.group < 0) stack.push(n); }
    }
  }
}
setTiles(campBoard, camps.map(c => [c.x, c.y]));
check(E.Managed_Bitboard_Union(blocked, campBoard, blockedWithCamps));
if (PROBE) console.error(`probe: ${camps.length} camps in ${groups.length} groups; blocked tiles ${setBits(blocked)} with camps ${setBits(blockedWithCamps)}`);

const blockedSet = new Set([...walls, ...holes, ...altars].map(([x, y]) => y * W + x));
const campSet = new Set(camps.map(c => c.y * W + c.x));
function neighbours(x, y) { const o = []; for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) if ((dx || dy) && x + dx >= 0 && y + dy >= 0 && x + dx < W && y + dy < H) o.push([x + dx, y + dy]); return o; }
const free = ([x, y]) => !blockedSet.has(y * W + x) && !campSet.has(y * W + x);
const altarRing = altars.flatMap(([ax, ay]) => neighbours(ax, ay).filter(free));
const spawnTilesA = [];
for (let y = spawnA.minY; y <= spawnA.maxY; y++) for (let x = spawnA.minX; x <= spawnA.maxX; x++) spawnTilesA.push([x, y]);
if (PROBE) { const f = distanceField(blockedWithCamps, altarRing); console.error(`probe: altar field holes unreachable=${holes.every(([x, y]) => f[y * W + x] === 65535)} max=${Math.max(...f.filter(v => v < 65535))}`); }

// Team B's spawn tiles: where its champions stand on the first play input.
let spawnTilesB = [];
for (let p = 0; p < size && !spawnTilesB.length; p++) {
  check(E.Game_Visualiser_Timeline_Seek(game, timeline, p, 0));
  const ts = timelineState();
  if (ts.phase !== 12) spawnTilesB = ts.champions.filter(c => c.team === 1 && c.position).map(c => [c.position.x, c.position.y]);
}
const spawnFields = [distanceField(blockedWithCamps, spawnTilesA), spawnTilesB.length ? distanceField(blockedWithCamps, spawnTilesB) : null];
// Per-group, per-team anchor fields: the anchor is the walkable ring tile nearest that team's
// spawn (World.finish_init), and the field is rooted on that one tile, so reading it at a
// champion's position gives the bot's own travel estimate.
const groupFields = [0, 1].map(team => groups.map(g => {
  const ring = g.camps.flatMap(ci => neighbours(camps[ci].x, camps[ci].y).filter(free));
  const sf = spawnFields[team];
  if (!ring.length || !sf) return null;
  let best = ring[0], bestD = 65535;
  for (const [x, y] of ring) { const d = sf[y * W + x]; if (d < bestD) { bestD = d; best = [x, y]; } }
  return distanceField(blockedWithCamps, [best]);
}));
const out = fs.createWriteStream(outPath);
out.write(JSON.stringify({
  kind: "static", width: W, height: H, champsPerTeam, oldRules: OLD_RULES, walls, holes, bushes, altars, spawnA, spawnB: spawnTilesB, camps, groups,
  altarField: distanceField(blockedWithCamps, altarRing),
  spawnFieldA: spawnFields[0], spawnFieldB: spawnFields[1],
  groupFields: groupFields[0], groupFieldsB: groupFields[1],
}) + "\n");

function compactState(st, team, champion, see) {
  const self = st.champions.find(c => c.team === team && c.entity.index === champion);
  const champs = st.champions.filter(c => c.position && c.alive !== false).map(c => ({
    i: c.entity.index, team: c.team, type: c.type, x: c.position.x, y: c.position.y,
    hp: Math.round(c.health ?? -1), max: Math.round(c.maxHealth ?? -1), gems: c.gems ?? null, gold: c.gold ?? null,
    str: c.strength ? Math.round(c.strength.effective) : null, focus: c.focus ? Math.round(c.focus.effective) : null,
    cd: c.cooldowns, items: c.items ?? null, cons: c.consumables ?? null, buffs: (c.buffs || []).map(b => b.type),
    rf: c.roundFlags ?? null, resp: c.respawnTimer ?? null, ward: c.ward ?? null,
  }));
  const visibleCamps = st.camps.filter(c => c.position && see(c.position.x, c.position.y)).map(c => ({ x: c.position.x, y: c.position.y, hp: Math.round(c.health ?? -1) }));
  const me = self ? { x: self.position?.x, y: self.position?.y, hp: Math.round(self.health ?? 0), max: Math.round(self.maxHealth ?? 0), gems: self.gems, gold: self.gold, alive: self.alive, resp: self.respawnTimer, cd: self.cooldowns, items: self.items, cons: self.consumables, buffs: (self.buffs || []).map(b => b.type), str: self.strength ? Math.round(self.strength.effective) : null, focus: self.focus ? Math.round(self.focus.effective) : null, type: self.type, ward: self.ward ?? null } : null;
  return { self: me, champs, camps: visibleCamps, groups: st.groups.map(g => (g.rewardAvailable ? 1 : 0) | ((g.captureTeam ?? 0) << 1)), gems: [Number(st.submittedGems[0]), Number(st.submittedGems[1])] };
}

let spawnB = null, current = null, turns = 0;
function flush() { if (current) { out.write(JSON.stringify(current) + "\n"); turns++; current = null; } }
const t0 = Date.now();
for (let p = 0; p < size; p++) {
  // One seek per input: ReplayNext advances by engine step, not by input.
  check(E.Game_Visualiser_Timeline_Seek(game, timeline, p, 0));
  const ts = timelineState();
  if (PROBE && p < 36) console.error(`probe: p=${p} round=${ts.round} phase=${ts.phase} team=${ts.team} champ=${ts.currentChampion} await=${ts.awaitingInput} step=${ts.step.kind}/${ts.step.phase} actor=${ts.step.actor.kind}:${ts.step.actor.index}`);
  if (ts.phase === 12) continue;                                   // draft
  if (!spawnB && ts.round >= 1) { const b = ts.champions.filter(c => c.team === 1 && c.position); if (b.length) spawnB = b.map(c => [c.position.x, c.position.y]); }
  const step = ts.step;
  if (ts.awaitingInput && (!current || current.team !== ts.team || current.champ !== ts.currentChampion || current.round !== ts.round)) {
    flush();
    const see = visionTester(ts.team, ts.currentChampion);
    current = {
      kind: "turn", round: ts.round, team: ts.team, champ: ts.currentChampion, pos: p,
      state: compactState(stateFrom(ts.team, ts.currentChampion), ts.team, ts.currentChampion, see),
      campHp: Object.fromEntries(ts.camps.filter(c => c.position).map(c => [`${c.position.x},${c.position.y}`, Math.round(c.health)])),
      actions: [],
    };
  }
  if (current && step && step.actor && step.actor.kind === 1 && step.actor.index === current.champ && step.round === ts.round) {
    const a = { k: step.kind };
    if (step.from && step.from.x >= 0) a.from = [step.from.x, step.from.y];
    if (step.to && step.to.x >= 0) a.to = [step.to.x, step.to.y];
    if (step.abilitySlot !== undefined) a.slot = step.abilitySlot;
    if (step.abilityType !== undefined) a.ab = step.abilityType;
    current.actions.push(a);
  }
  if (PROBE && p > 400) break;
}
flush();
out.write(JSON.stringify({ kind: "end", inputs: size, turns, spawnB, seconds: (Date.now() - t0) / 1000 }) + "\n");
out.end();
console.error(`dumped ${turns} champion turns from ${size} inputs in ${((Date.now() - t0) / 1000).toFixed(1)}s`);
