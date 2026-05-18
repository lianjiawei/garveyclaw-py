import { FLOOR_TILE_SIZE, WALL_BITMASK_COUNT, WALL_GRID_COLS, WALL_PIECE_HEIGHT, WALL_PIECE_WIDTH } from './constants.js';
import { buildDynamicCatalog } from './furnitureCatalog.js';
import { decodeCharacterImage, setCharacterTemplates } from './spriteData.js';
import { setFloorSprites } from './floorTiles.js';
import { setWallSprites } from './wallTiles.js';
export async function loadAssetBundleFromBaseUrl(baseUrl) {
    const assetIndex = await fetchJson(joinUrl(baseUrl, 'asset-index.json'));
    const characters = await Promise.all(assetIndex.characters.map((path) => loadCharacter(joinUrl(baseUrl, path))));
    const floors = await Promise.all(assetIndex.floors.map((path) => loadSingleSprite(joinUrl(baseUrl, path), FLOOR_TILE_SIZE, FLOOR_TILE_SIZE)));
    const walls = await Promise.all(assetIndex.walls.map((path) => loadWallSet(joinUrl(baseUrl, path))));
    const furniture = await loadFurnitureBundle(baseUrl, assetIndex.furnitureDirs);
    const defaultLayout = assetIndex.defaultLayout ? await fetchJson(joinUrl(baseUrl, assetIndex.defaultLayout)) : undefined;
    const bundle = { characters, floors, walls, furniture, defaultLayout };
    applyAssetBundle(bundle);
    return bundle;
}
export function applyAssetBundle(bundle) {
    setCharacterTemplates(bundle.characters);
    setFloorSprites(bundle.floors);
    setWallSprites(bundle.walls);
    buildDynamicCatalog(bundle.furniture);
}
async function loadFurnitureBundle(baseUrl, furnitureDirs) {
    const groups = await Promise.all(furnitureDirs.map(async (dir) => {
        const manifest = await fetchJson(joinUrl(baseUrl, `${dir}/manifest.json`));
        const flattened = flattenManifest(manifest);
        const sprites = await Promise.all(flattened.map(async (asset) => ({
            id: asset.id,
            sprite: await loadSingleSprite(joinUrl(baseUrl, `${dir}/${asset.file}`), asset.width, asset.height),
        })));
        return { catalog: flattened, sprites };
    }));
    const catalog = [];
    const sprites = {};
    for (const group of groups) {
        catalog.push(...group.catalog);
        for (const sprite of group.sprites)
            sprites[sprite.id] = sprite.sprite;
    }
    return { catalog, sprites };
}
function flattenManifest(manifest) {
    const inherited = {
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
                width: manifest.width,
                height: manifest.height,
                footprintW: manifest.footprintW,
                footprintH: manifest.footprintH,
                isDesk: manifest.category === 'desks',
                canPlaceOnWalls: manifest.canPlaceOnWalls,
                canPlaceOnSurfaces: manifest.canPlaceOnSurfaces,
                backgroundTiles: manifest.backgroundTiles,
                groupId: manifest.id,
            }];
    }
    return flattenNode({ type: 'group', groupType: manifest.groupType, rotationScheme: manifest.rotationScheme, members: manifest.members ?? [] }, inherited);
}
function flattenNode(node, inherited) {
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
    const results = [];
    for (const member of node.members) {
        const childProps = { ...inherited };
        if (node.groupType === 'rotation' && node.rotationScheme)
            childProps.rotationScheme = node.rotationScheme;
        if (node.groupType === 'state') {
            if (node.orientation)
                childProps.orientation = node.orientation;
            if (node.state)
                childProps.state = node.state;
        }
        if (node.groupType === 'animation') {
            const orient = node.orientation ?? inherited.orientation ?? '';
            const state = node.state ?? inherited.state ?? '';
            childProps.animationGroup = `${inherited.groupId}_${orient}_${state}`.toUpperCase();
            if (node.state)
                childProps.state = node.state;
        }
        if (node.orientation && !childProps.orientation)
            childProps.orientation = node.orientation;
        results.push(...flattenNode(member, childProps));
    }
    return results;
}
async function loadCharacter(url) {
    const image = await loadImage(url);
    return decodeCharacterImage(image);
}
async function loadWallSet(url) {
    const image = await loadImage(url);
    const canvas = document.createElement('canvas');
    canvas.width = image.width;
    canvas.height = image.height;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(image, 0, 0);
    const sprites = [];
    for (let mask = 0; mask < WALL_BITMASK_COUNT; mask++) {
        const ox = (mask % WALL_GRID_COLS) * WALL_PIECE_WIDTH;
        const oy = Math.floor(mask / WALL_GRID_COLS) * WALL_PIECE_HEIGHT;
        sprites.push(readSprite(ctx, ox, oy, WALL_PIECE_WIDTH, WALL_PIECE_HEIGHT));
    }
    return sprites;
}
async function loadSingleSprite(url, width, height) {
    const image = await loadImage(url);
    const canvas = document.createElement('canvas');
    canvas.width = image.width;
    canvas.height = image.height;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(image, 0, 0);
    return readSprite(ctx, 0, 0, width, height);
}
function readSprite(ctx, x, y, width, height) {
    const imageData = ctx.getImageData(x, y, width, height).data;
    const sprite = [];
    for (let row = 0; row < height; row++) {
        const line = [];
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
async function loadImage(url) {
    return await new Promise((resolve, reject) => {
        const image = new Image();
        image.crossOrigin = 'anonymous';
        image.onload = () => resolve(image);
        image.onerror = () => reject(new Error(`Failed to load image: ${url}`));
        image.src = url;
    });
}
async function fetchJson(url) {
    const response = await fetch(url);
    if (!response.ok)
        throw new Error(`Failed to fetch ${url}: ${response.status}`);
    return (await response.json());
}
function joinUrl(baseUrl, path) {
    return `${baseUrl.replace(/\/$/, '')}/${path.replace(/^\//, '')}`;
}
