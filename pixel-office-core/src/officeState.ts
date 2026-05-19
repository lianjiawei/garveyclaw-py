import {
  AUTO_ON_FACING_DEPTH,
  AUTO_ON_SIDE_DEPTH,
  FURNITURE_ANIM_INTERVAL_SEC,
  TILE_SIZE,
} from './constants.js';
import { createCharacter, updateCharacter } from './characters.js';
import { getAnimationFrames, getCatalogEntry, getOnStateType } from './furnitureCatalog.js';
import { createDefaultLayout, getBlockedTiles, layoutToFurnitureInstances, layoutToSeats, layoutToTileMap } from './layout.js';
import { findPath, getWalkableTiles } from './tileMap.js';
import type {
  AgentMode,
  Character,
  FurnitureInstance,
  OfficeLayout,
  PixelOfficeAgentInput,
  PlacedFurniture,
  Seat,
  TileType as TileTypeVal,
} from './types.js';
import { CharacterState, TileType } from './types.js';

const ELECTRONICS_AUTO_ON_DISTANCE = 4;

export class OfficeState {
  layout: OfficeLayout;
  tileMap: TileTypeVal[][];
  seats: Map<string, Seat>;
  blockedTiles: Set<string>;
  furniture: FurnitureInstance[];
  walkableTiles: Array<{ col: number; row: number }>;
  characters: Map<number, Character> = new Map();
  cameraFollowId: number | null = null;
  furnitureAnimTimer = 0;

  constructor(layout?: OfficeLayout) {
    this.layout = layout ?? createDefaultLayout();
    this.tileMap = layoutToTileMap(this.layout);
    this.seats = layoutToSeats(this.layout.furniture);
    this.blockedTiles = getBlockedTiles(this.layout.furniture);
    this.furniture = layoutToFurnitureInstances(this.layout.furniture);
    this.walkableTiles = getWalkableTiles(this.tileMap, this.blockedTiles);
  }

  getLayout(): OfficeLayout {
    return this.layout;
  }

  setLayout(layout: OfficeLayout): void {
    this.layout = layout;
    this.tileMap = layoutToTileMap(layout);
    this.seats = layoutToSeats(layout.furniture);
    this.blockedTiles = getBlockedTiles(layout.furniture);
    this.furniture = layoutToFurnitureInstances(layout.furniture);
    this.walkableTiles = getWalkableTiles(this.tileMap, this.blockedTiles);
    for (const ch of this.characters.values()) {
      if (ch.seatId && this.seats.has(ch.seatId)) {
        const seat = this.seats.get(ch.seatId)!;
        seat.assigned = true;
        this.placeCharacterAtSeat(ch, seat);
        continue;
      }

      ch.seatId = null;
      const seatId = this.findFreeSeat();
      if (seatId) {
        const seat = this.seats.get(seatId)!;
        seat.assigned = true;
        ch.seatId = seatId;
        this.placeCharacterAtSeat(ch, seat);
        continue;
      }

      if (!this.isCharacterOnWalkableTile(ch)) {
        this.moveCharacterToWalkable(ch.id);
      }
    }
  }

  update(dt: number): void {
    const prevFrame = Math.floor(this.furnitureAnimTimer / FURNITURE_ANIM_INTERVAL_SEC);
    this.furnitureAnimTimer += dt;
    const newFrame = Math.floor(this.furnitureAnimTimer / FURNITURE_ANIM_INTERVAL_SEC);
    if (newFrame !== prevFrame) {
      this.rebuildFurnitureInstances();
    }

    for (const ch of this.characters.values()) {
      this.withOwnSeatUnblocked(ch, () =>
        updateCharacter(ch, dt, this.walkableTiles, this.seats, this.tileMap, this.blockedTiles),
      );
      if (ch.bubbleType === 'waiting') {
        ch.bubbleTimer -= dt;
        if (ch.bubbleTimer <= 0) {
          ch.bubbleType = null;
          ch.bubbleTimer = 0;
        }
      }
      if (ch.statusTimer > 0) {
        ch.statusTimer -= dt;
        if (ch.statusTimer <= 0) {
          ch.statusText = '';
          ch.statusDetail = '';
          ch.statusTimer = 0;
        }
      }
    }
  }

  getCharacters(): Character[] {
    return Array.from(this.characters.values());
  }

  setAgents(agents: PixelOfficeAgentInput[]): void {
    const nextIds = new Set(agents.map((agent) => agent.id));
    for (const existingId of Array.from(this.characters.keys())) {
      if (!nextIds.has(existingId)) this.characters.delete(existingId);
    }
    for (const agent of agents) this.upsertAgent(agent);
  }

  upsertAgent(agent: PixelOfficeAgentInput): void {
    const existing = this.characters.get(agent.id);
    if (!existing) {
      const seatId = agent.seatId ?? this.findFreeSeat();
      if (seatId) this.seats.get(seatId)!.assigned = true;
      const seat = seatId ? this.seats.get(seatId)! : null;
      const character = createCharacter(agent.id, agent.palette ?? 0, seatId ?? null, seat, agent.hueShift ?? 0);
      character.label = agent.label;
      character.currentTool = agent.currentTool ?? null;
      character.isActive = agent.isActive ?? true;
      this.characters.set(agent.id, character);
      return;
    }
    existing.label = agent.label ?? existing.label;
    existing.currentTool = agent.currentTool ?? existing.currentTool;
    existing.isActive = agent.isActive ?? existing.isActive;
    if (typeof agent.palette === 'number') existing.palette = agent.palette;
    if (typeof agent.hueShift === 'number') existing.hueShift = agent.hueShift;
    if (agent.seatId !== undefined && agent.seatId !== existing.seatId) {
      this.reassignSeat(existing.id, agent.seatId);
    }
  }

  removeAgent(id: number): void {
    const ch = this.characters.get(id);
    if (!ch) return;
    if (ch.seatId) {
      const seat = this.seats.get(ch.seatId);
      if (seat) seat.assigned = false;
    }
    this.characters.delete(id);
  }

  setAgentTool(id: number, tool: string | null): void {
    const ch = this.characters.get(id);
    if (!ch) return;
    ch.currentTool = tool;
  }

  setAgentStatus(id: number, text: string, detail?: string | null, ttlSeconds?: number | null): void {
    const ch = this.characters.get(id);
    if (!ch) return;
    ch.statusText = text;
    ch.statusDetail = detail ?? '';
    ch.statusTimer = ttlSeconds ?? 0;
  }

  setAgentActive(id: number, isActive: boolean, tool?: string | null): void {
    const ch = this.characters.get(id);
    if (!ch) return;
    ch.isActive = isActive;
    if (tool !== undefined) ch.currentTool = tool;
    if (!isActive) {
      ch.seatTimer = -1;
      ch.path = [];
      ch.moveProgress = 0;
    }
    this.rebuildFurnitureInstances();
  }

  moveAgentTo(id: number, col: number, row: number): boolean {
    const ch = this.characters.get(id);
    if (!ch) return false;
    const path = findPath(ch.tileCol, ch.tileRow, col, row, this.tileMap, this.blockedTiles);
    if (path.length === 0 && (ch.tileCol !== col || ch.tileRow !== row)) return false;
    ch.path = path;
    ch.moveProgress = 0;
    if (path.length > 0) ch.state = 'walk';
    return true;
  }

  stopAgent(id: number): void {
    const ch = this.characters.get(id);
    if (!ch) return;
    ch.path = [];
    ch.moveProgress = 0;
    ch.state = 'idle';
    ch.isActive = false;
  }

  sendAgentToSeat(id: number): boolean {
    const ch = this.characters.get(id);
    if (!ch || !ch.seatId) return false;
    const seat = this.seats.get(ch.seatId);
    if (!seat) return false;
    ch.isActive = true;
    const path = this.withOwnSeatUnblocked(ch, () =>
      findPath(ch.tileCol, ch.tileRow, seat.seatCol, seat.seatRow, this.tileMap, this.blockedTiles),
    );
    if (path.length === 0) {
      if (ch.tileCol === seat.seatCol && ch.tileRow === seat.seatRow) {
        ch.state = 'type';
        ch.dir = seat.facingDir;
        return true;
      }
      return false;
    }
    ch.path = path;
    ch.moveProgress = 0;
    ch.state = 'walk';
    return true;
  }

  private ensureWorkSeat(ch: Character): void {
    if (ch.seatId) {
      const seat = this.seats.get(ch.seatId);
      if (seat && this.isWorkSeat(seat)) return;
    }

    const workSeatId = this.findFreeWorkSeat();
    if (workSeatId) this.reassignSeat(ch.id, workSeatId);
  }

  seatAgentNow(id: number): boolean {
    const ch = this.characters.get(id);
    if (!ch || !ch.seatId) return false;
    const seat = this.seats.get(ch.seatId);
    if (!seat) return false;
    ch.tileCol = seat.seatCol;
    ch.tileRow = seat.seatRow;
    ch.x = seat.seatCol * TILE_SIZE + TILE_SIZE / 2;
    ch.y = seat.seatRow * TILE_SIZE + TILE_SIZE / 2;
    ch.path = [];
    ch.moveProgress = 0;
    ch.dir = seat.facingDir;
    ch.state = 'type';
    ch.isActive = true;
    return true;
  }

  wanderAgent(id: number): void {
    const ch = this.characters.get(id);
    if (!ch) return;
    ch.isActive = false;
    ch.path = [];
    ch.moveProgress = 0;
    ch.state = 'idle';
    ch.wanderTimer = 0.1;
  }

  setAgentMode(id: number, mode: AgentMode, tool?: string | null): void {
    const ch = this.characters.get(id);
    if (!ch) return;

    switch (mode) {
      case 'working': {
        this.clearBubble(id);
        ch.currentTool = tool ?? 'Write';
        this.ensureWorkSeat(ch);
        ch.isActive = true;
        this.sendAgentToSeat(id);
        this.rebuildFurnitureInstances();
        return;
      }
      case 'thinking': {
        this.clearBubble(id);
        ch.currentTool = tool ?? 'Read';
        this.ensureWorkSeat(ch);
        ch.isActive = true;
        this.sendAgentToSeat(id);
        this.rebuildFurnitureInstances();
        return;
      }
      case 'waiting': {
        ch.currentTool = tool ?? null;
        this.ensureWorkSeat(ch);
        ch.isActive = true;
        this.sendAgentToSeat(id);
        this.showBubble(id, 'waiting');
        this.rebuildFurnitureInstances();
        return;
      }
      case 'blocked': {
        ch.currentTool = tool ?? ch.currentTool ?? 'Task';
        this.ensureWorkSeat(ch);
        ch.isActive = true;
        this.sendAgentToSeat(id);
        this.showBubble(id, 'permission');
        this.rebuildFurnitureInstances();
        return;
      }
      case 'idle': {
        ch.currentTool = tool ?? null;
        this.clearBubble(id);
        this.wanderAgent(id);
        this.rebuildFurnitureInstances();
        return;
      }
    }
  }

  private rebuildFurnitureInstances(): void {
    const autoOnTiles = new Set<string>();
    const activeSeats: Seat[] = [];
    for (const ch of this.characters.values()) {
      if (!ch.isActive || !ch.seatId) continue;
      const seat = this.seats.get(ch.seatId);
      if (!seat) continue;
      activeSeats.push(seat);
      const dCol = seat.facingDir === 2 ? 1 : seat.facingDir === 1 ? -1 : 0;
      const dRow = seat.facingDir === 0 ? 1 : seat.facingDir === 3 ? -1 : 0;
      for (let d = 1; d <= AUTO_ON_FACING_DEPTH; d++) {
        autoOnTiles.add(`${seat.seatCol + dCol * d},${seat.seatRow + dRow * d}`);
      }
      for (let d = 1; d <= AUTO_ON_SIDE_DEPTH; d++) {
        const baseCol = seat.seatCol + dCol * d;
        const baseRow = seat.seatRow + dRow * d;
        if (dCol !== 0) {
          autoOnTiles.add(`${baseCol},${baseRow - 1}`);
          autoOnTiles.add(`${baseCol},${baseRow + 1}`);
        } else {
          autoOnTiles.add(`${baseCol - 1},${baseRow}`);
          autoOnTiles.add(`${baseCol + 1},${baseRow}`);
        }
      }
    }

    if (autoOnTiles.size === 0 && activeSeats.length === 0) {
      this.furniture = layoutToFurnitureInstances(this.layout.furniture);
      return;
    }

    const animFrame = Math.floor(this.furnitureAnimTimer / FURNITURE_ANIM_INTERVAL_SEC);
    const modifiedFurniture: PlacedFurniture[] = this.layout.furniture.map((item) => {
      const entry = getCatalogEntry(item.type);
      if (!entry) return item;
      const isNearbyElectronics =
        entry.category === 'electronics' && this.isFurnitureNearAnySeat(item, entry.footprintW, entry.footprintH, activeSeats);
      for (let dr = 0; dr < entry.footprintH; dr++) {
        for (let dc = 0; dc < entry.footprintW; dc++) {
          if (isNearbyElectronics || autoOnTiles.has(`${item.col + dc},${item.row + dr}`)) {
            let onType = getOnStateType(item.type);
            if (onType !== item.type) {
              const frames = getAnimationFrames(onType);
              if (frames && frames.length > 1) {
                onType = frames[animFrame % frames.length];
              }
              return { ...item, type: onType };
            }
            return item;
          }
        }
      }
      return item;
    });

    this.furniture = layoutToFurnitureInstances(modifiedFurniture);
  }

  private isFurnitureNearAnySeat(item: PlacedFurniture, footprintW: number, footprintH: number, seats: Seat[]): boolean {
    if (seats.length === 0) return false;
    const minCol = item.col;
    const maxCol = item.col + footprintW - 1;
    const minRow = item.row;
    const maxRow = item.row + footprintH - 1;
    for (const seat of seats) {
      const dc = seat.seatCol < minCol ? minCol - seat.seatCol : seat.seatCol > maxCol ? seat.seatCol - maxCol : 0;
      const dr = seat.seatRow < minRow ? minRow - seat.seatRow : seat.seatRow > maxRow ? seat.seatRow - maxRow : 0;
      if (dc + dr <= ELECTRONICS_AUTO_ON_DISTANCE) return true;
    }
    return false;
  }

  reassignSeat(id: number, seatId: string | null | undefined): void {
    const ch = this.characters.get(id);
    if (!ch) return;
    if (ch.seatId) {
      const oldSeat = this.seats.get(ch.seatId);
      if (oldSeat) oldSeat.assigned = false;
    }
    if (seatId && this.seats.has(seatId)) {
      const seat = this.seats.get(seatId)!;
      seat.assigned = true;
      ch.seatId = seatId;
      ch.dir = seat.facingDir;
      this.sendAgentToSeat(id);
    } else {
      ch.seatId = null;
    }
  }

  private ownSeatKey(ch: Character): string | null {
    if (!ch.seatId) return null;
    const seat = this.seats.get(ch.seatId);
    if (!seat) return null;
    return `${seat.seatCol},${seat.seatRow}`;
  }

  private withOwnSeatUnblocked<T>(ch: Character, fn: () => T): T {
    const key = this.ownSeatKey(ch);
    if (key) this.blockedTiles.delete(key);
    const result = fn();
    if (key) this.blockedTiles.add(key);
    return result;
  }

  showBubble(id: number, bubbleType: 'permission' | 'waiting'): void {
    const ch = this.characters.get(id);
    if (!ch) return;
    ch.bubbleType = bubbleType;
    ch.bubbleTimer = bubbleType === 'waiting' ? 2 : 0;
  }

  clearBubble(id: number): void {
    const ch = this.characters.get(id);
    if (!ch) return;
    ch.bubbleType = null;
    ch.bubbleTimer = 0;
  }

  moveCharacterToWalkable(id: number): void {
    const ch = this.characters.get(id);
    if (!ch || this.walkableTiles.length === 0) return;
    const spawn = this.walkableTiles[Math.floor(Math.random() * this.walkableTiles.length)];
    ch.tileCol = spawn.col;
    ch.tileRow = spawn.row;
    ch.x = spawn.col * TILE_SIZE + TILE_SIZE / 2;
    ch.y = spawn.row * TILE_SIZE + TILE_SIZE / 2;
    ch.path = [];
    ch.moveProgress = 0;
  }

  private placeCharacterAtSeat(ch: Character, seat: Seat): void {
    ch.tileCol = seat.seatCol;
    ch.tileRow = seat.seatRow;
    ch.x = seat.seatCol * TILE_SIZE + TILE_SIZE / 2;
    ch.y = seat.seatRow * TILE_SIZE + TILE_SIZE / 2;
    ch.path = [];
    ch.moveProgress = 0;
    ch.dir = seat.facingDir;
    ch.state = ch.isActive ? CharacterState.TYPE : CharacterState.IDLE;
    ch.wanderTimer = 0.2;
  }

  private isCharacterOnWalkableTile(ch: Character): boolean {
    if (ch.tileRow < 0 || ch.tileRow >= this.layout.rows || ch.tileCol < 0 || ch.tileCol >= this.layout.cols) {
      return false;
    }
    const tile = this.tileMap[ch.tileRow]?.[ch.tileCol];
    if (tile === undefined || tile === TileType.VOID || tile === TileType.WALL) return false;
    return !this.blockedTiles.has(`${ch.tileCol},${ch.tileRow}`);
  }

  private findFreeSeat(): string | null {
    const electronicsTiles = this.getElectronicsTiles();

    const preferred: string[] = [];
    const fallback: string[] = [];

    for (const [seatId, seat] of this.seats) {
      if (seat.assigned) continue;

      (this.seatFacesElectronics(seat, electronicsTiles) ? preferred : fallback).push(seatId);
    }

    if (preferred.length > 0) return preferred[Math.floor(Math.random() * preferred.length)];
    if (fallback.length > 0) return fallback[Math.floor(Math.random() * fallback.length)];
    return null;
  }

  private findFreeWorkSeat(): string | null {
    const electronicsTiles = this.getElectronicsTiles();
    const preferred: string[] = [];

    for (const [seatId, seat] of this.seats) {
      if (seat.assigned) continue;
      if (this.seatFacesElectronics(seat, electronicsTiles)) preferred.push(seatId);
    }

    if (preferred.length > 0) return preferred[Math.floor(Math.random() * preferred.length)];
    return null;
  }

  private isWorkSeat(seat: Seat): boolean {
    return this.seatFacesElectronics(seat, this.getElectronicsTiles());
  }

  private getElectronicsTiles(): Set<string> {
    const electronicsTiles = new Set<string>();
    for (const item of this.layout.furniture) {
      const entry = getCatalogEntry(item.type);
      if (!entry || entry.category !== 'electronics') continue;
      for (let dr = 0; dr < entry.footprintH; dr++) {
        for (let dc = 0; dc < entry.footprintW; dc++) {
          electronicsTiles.add(`${item.col + dc},${item.row + dr}`);
        }
      }
    }
    return electronicsTiles;
  }

  private seatFacesElectronics(seat: Seat, electronicsTiles: Set<string>): boolean {
    const dCol = seat.facingDir === 2 ? 1 : seat.facingDir === 1 ? -1 : 0;
    const dRow = seat.facingDir === 0 ? 1 : seat.facingDir === 3 ? -1 : 0;

    for (let d = 1; d <= 3; d++) {
      const tileCol = seat.seatCol + dCol * d;
      const tileRow = seat.seatRow + dRow * d;
      if (electronicsTiles.has(`${tileCol},${tileRow}`)) return true;
      if (dCol !== 0) {
        if (electronicsTiles.has(`${tileCol},${tileRow - 1}`) || electronicsTiles.has(`${tileCol},${tileRow + 1}`)) {
          return true;
        }
      } else if (
        electronicsTiles.has(`${tileCol - 1},${tileRow}`) ||
        electronicsTiles.has(`${tileCol + 1},${tileRow}`)
      ) {
        return true;
      }
    }
    return false;
  }
}
