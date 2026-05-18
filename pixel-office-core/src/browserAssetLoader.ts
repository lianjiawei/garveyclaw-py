import { CHAR_FRAME_H, CHAR_FRAME_W, FLOOR_TILE_SIZE, WALL_BITMASK_COUNT, WALL_GRID_COLS, WALL_PIECE_HEIGHT, WALL_PIECE_WIDTH } from './constants.js';
import { buildDynamicCatalog } from './furnitureCatalog.js';
import { decodeCharacterImage, setCharacterTemplates } from './spriteData.js';
import { setFloorSprites } from './floorTiles.js';
import { setWallSprites } from './wallTiles.js';
import type { CharacterDirectionSprites, LoadedAssetData, OfficeLayout, PixelOfficeAssetBundle, SpriteData } from './types.js';

interface AssetIndex {
  characters: string[];
  floors: string[];
  walls: string[];
  defaultLayout: string | null;
  furnitureDirs: string[];
}

interface ManifestAsset {
  type: 'asset';
  id: string;
  file: string;
  width: number;
  height: number;
  footprintW: number;
  footprintH: number;
  orientation?: string;
  state?: string;
  frame?: number;
  mirrorSide?: boolean;
}

interface ManifestGroup {
  type: 'group';
  groupType: 'rotation' | 'state' | 'animation';
  rotationScheme?: string;
  orientation?: string;
  state?: string;
  members: Array<ManifestAsset | ManifestGroup>;
}

interface FurnitureManifest {
  id: string;
  name: string;
  category: string;
  canPlaceOnWalls: boolean;
  canPlaceOnSurfaces: boolean;
  backgroundTiles: number;
  type: 'asset' | 'group';
  file?: string;
  width?: number;
  height?: number;
  footprintW?: number;
  footprintH?: number;
  groupType?: string;
  rotationScheme?: string;
  members?: Array<ManifestAsset | ManifestGroup>;
}

interface InheritedProps {
  groupId: string;
  name: string;
  category: string;
  canPlaceOnWalls: boolean;
  canPlaceOnSurfaces: boolean;
  backgroundTiles: number;
  orientation?: string;
  state?: string;
  rotationScheme?: string;
  animationGroup?: string;
}

export async function loadAssetBundleFromBaseUrl(baseUrl: string): Promise<PixelOfficeAssetBundle> {
  const assetIndex = await fetchJson<AssetIndex>(joinUrl(baseUrl, 'asset-index.json'));
  const characters = await Promise.all(assetIndex.characters.map((path) => loadCharacter(joinUrl(baseUrl, path))));
  const floors = await Promise.all(assetIndex.floors.map((path) => loadSingleSprite(joinUrl(baseUrl, path), FLOOR_TILE_SIZE, FLOOR_TILE_SIZE)));
  const walls = await Promise.all(assetIndex.walls.map((path) => loadWallSet(joinUrl(baseUrl, path))));
  const furniture = await loadFurnitureBundle(baseUrl, assetIndex.furnitureDirs);
  const defaultLayout = assetIndex.defaultLayout ? await fetchJson<OfficeLayout>(joinUrl(baseUrl, assetIndex.defaultLayout)) : undefined;
  const bundle: PixelOfficeAssetBundle = { characters, floors, walls, furniture, defaultLayout };
  applyAssetBundle(bundle);
  return bundle;
}

export function applyAssetBundle(bundle: PixelOfficeAssetBundle): void {
  setCharacterTemplates(bundle.characters);
  setFloorSprites(bundle.floors);
  setWallSprites(bundle.walls);
  buildDynamicCatalog(bundle.furniture);
}

async function loadFurnitureBundle(baseUrl: string, furnitureDirs: string[]): Promise<LoadedAssetData> {
  const groups = await Promise.all(furnitureDirs.map(async (dir) => {
    const manifest = await fetchJson<FurnitureManifest>(joinUrl(baseUrl, `${dir}/manifest.json`));
    const flattened = flattenManifest(manifest);
    const sprites = await Promise.all(flattened.map(async (asset) => ({
      id: asset.id,
      sprite: await loadSingleSprite(joinUrl(baseUrl, `${dir}/${asset.file}`), asset.width, asset.height),
    })));
    return { catalog: flattened, sprites };
  }));
  const catalog: LoadedAssetData['catalog'] = [];
  const sprites: LoadedAssetData['sprites'] = {};
  for (const group of groups) {
    catalog.push(...group.catalog);
    for (const sprite of group.sprites) sprites[sprite.id] = sprite.sprite;
  }
  return { catalog, sprites };
}

function flattenManifest(manifest: FurnitureManifest): LoadedAssetData['catalog'] {
  const inherited: InheritedProps = {
    groupId: manifest.id,
    name: manifest.name,
    category: manifest.category,
    canPlaceOnWalls: manifest.canPlaceOnWalls,
    canPlaceOnSurfaces: manifest.canPlaceOnSurfaces,
    backgroundTiles: manifest.backgroundTiles,
  };
  if (manifest.type === 'asset') {
    return [{
      id: manifest.id,
      name: manifest.name,
      label: manifest.name,
      category: manifest.category,
      file: manifest.file ?? `${manifest.id}.png`,
      width: manifest.width!,
      height: manifest.height!,
      footprintW: manifest.footprintW!,
      footprintH: manifest.footprintH!,
      isDesk: manifest.category === 'desks',
      canPlaceOnWalls: manifest.canPlaceOnWalls,
      canPlaceOnSurfaces: manifest.canPlaceOnSurfaces,
      backgroundTiles: manifest.backgroundTiles,
      groupId: manifest.id,
    }];
  }
  return flattenNode({ type: 'group', groupType: manifest.groupType as 'rotation' | 'state' | 'animation', rotationScheme: manifest.rotationScheme, members: manifest.members ?? [] }, inherited);
}

function flattenNode(node: ManifestAsset | ManifestGroup, inherited: InheritedProps): LoadedAssetData['catalog'] {
  if (node.type === 'asset') {
    return [{
      id: node.id,
      name: inherited.name,
      label: inherited.name,
      category: inherited.category,
      file: node.file,
      width: node.width,
      height: node.height,
      footprintW: node.footprintW,
      footprintH: node.footprintH,
      isDesk: inherited.category === 'desks',
      canPlaceOnWalls: inherited.canPlaceOnWalls,
      canPlaceOnSurfaces: inherited.canPlaceOnSurfaces,
      backgroundTiles: inherited.backgroundTiles,
      groupId: inherited.groupId,
      orientation: node.orientation ?? inherited.orientation,
      state: node.state ?? inherited.state,
      mirrorSide: node.mirrorSide,
      rotationScheme: inherited.rotationScheme,
      animationGroup: inherited.animationGroup,
      frame: node.frame,
    }];
  }
  const results: LoadedAssetData['catalog'] = [];
  for (const member of node.members) {
    const childProps: InheritedProps = { ...inherited };
    if (node.groupType === 'rotation' && node.rotationScheme) childProps.rotationScheme = node.rotationScheme;
    if (node.groupType === 'state') {
      if (node.orientation) childProps.orientation = node.orientation;
      if (node.state) childProps.state = node.state;
    }
    if (node.groupType === 'animation') {
      const orient = node.orientation ?? inherited.orientation ?? '';
      const state = node.state ?? inherited.state ?? '';
      childProps.animationGroup = `${inherited.groupId}_${orient}_${state}`.toUpperCase();
      if (node.state) childProps.state = node.state;
    }
    if (node.orientation && !childProps.orientation) childProps.orientation = node.orientation;
    results.push(...flattenNode(member, childProps));
  }
  return results;
}

async function loadCharacter(url: string): Promise<CharacterDirectionSprites> {
  const image = await loadImage(url);
  return decodeCharacterImage(image);
}

async function loadWallSet(url: string): Promise<SpriteData[]> {
  const image = await loadImage(url);
  const canvas = document.createElement('canvas');
  canvas.width = image.width;
  canvas.height = image.height;
  const ctx = canvas.getContext('2d')!;
  ctx.drawImage(image, 0, 0);
  const sprites: SpriteData[] = [];
  for (let mask = 0; mask < WALL_BITMASK_COUNT; mask++) {
    const ox = (mask % WALL_GRID_COLS) * WALL_PIECE_WIDTH;
    const oy = Math.floor(mask / WALL_GRID_COLS) * WALL_PIECE_HEIGHT;
    sprites.push(readSprite(ctx, ox, oy, WALL_PIECE_WIDTH, WALL_PIECE_HEIGHT));
  }
  return sprites;
}

async function loadSingleSprite(url: string, width: number, height: number): Promise<SpriteData> {
  const image = await loadImage(url);
  const canvas = document.createElement('canvas');
  canvas.width = image.width;
  canvas.height = image.height;
  const ctx = canvas.getContext('2d')!;
  ctx.drawImage(image, 0, 0);
  return readSprite(ctx, 0, 0, width, height);
}

function readSprite(ctx: CanvasRenderingContext2D, x: number, y: number, width: number, height: number): SpriteData {
  const imageData = ctx.getImageData(x, y, width, height).data;
  const sprite: SpriteData = [];
  for (let row = 0; row < height; row++) {
    const line: string[] = [];
    for (let col = 0; col < width; col++) {
      const idx = (row * width + col) * 4;
      const a = imageData[idx + 3];
      if (a < 2) {
        line.push('');
        continue;
      }
      const r = imageData[idx];
      const g = imageData[idx + 1];
      const b = imageData[idx + 2];
      const base = `#${r.toString(16).padStart(2, '0')}${g.toString(16).padStart(2, '0')}${b.toString(16).padStart(2, '0')}`.toUpperCase();
      line.push(a >= 255 ? base : `${base}${a.toString(16).padStart(2, '0').toUpperCase()}`);
    }
    sprite.push(line);
  }
  return sprite;
}

async function loadImage(url: string): Promise<HTMLImageElement> {
  return await new Promise((resolve, reject) => {
    const image = new Image();
    image.crossOrigin = 'anonymous';
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error(`Failed to load image: ${url}`));
    image.src = url;
  });
}

async function fetchJson<T>(url: string): Promise<T> {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Failed to fetch ${url}: ${response.status}`);
  return (await response.json()) as T;
}

function joinUrl(baseUrl: string, path: string): string {
  return `${baseUrl.replace(/\/$/, '')}/${path.replace(/^\//, '')}`;
}
