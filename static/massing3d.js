/**
 * massing3d.js — Three.js r163 3D massing for Bogotá edificabilidad results.
 *
 * Coordinate convention used throughout:
 *   Local metric: x = East, y = North (both horizontal)
 *   Three.js world: Y-up. All geometry is added to a Group rotated
 *   -PI/2 around X so that local Z (ExtrudeGeometry depth) → world Y (up)
 *   and the ground footprint lies in the world XZ plane.
 *
 * Exports:
 *   buildMassing(calcData, container) → instance | null
 *   disposeMassing(instance)
 */

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const FLOOR_H = 3.0; // metres per floor (matches calc.py assumption)
const DEG2RAD = Math.PI / 180;

// ── Geometry helpers ─────────────────────────────────────────────────────────

function projectRing(ring, lon0, lat0) {
    const cosLat = Math.cos(lat0 * DEG2RAD);
    return ring.map(([lon, lat]) => [
        (lon - lon0) * cosLat * 111_319,
        (lat - lat0) * 111_319,
    ]);
}

function signedArea(pts) {
    let a = 0;
    for (let i = 0, n = pts.length; i < n; i++) {
        const j = (i + 1) % n;
        a += pts[i][0] * pts[j][1] - pts[j][0] * pts[i][1];
    }
    return a / 2;
}

function ensureCCW(pts) {
    return signedArea(pts) < 0 ? [...pts].reverse() : pts;
}

/**
 * Inset a CCW polygon by distance d (d > 0 = inward).
 * Uses bisector miter offset. Returns pts if d <= 0.
 */
function insetPolygon(pts, d) {
    if (d <= 0) return pts;
    const n = pts.length;
    const out = [];
    for (let i = 0; i < n; i++) {
        const prev = pts[(i - 1 + n) % n];
        const curr = pts[i];
        const next = pts[(i + 1) % n];
        const e1 = [curr[0] - prev[0], curr[1] - prev[1]];
        const e2 = [next[0] - curr[0], next[1] - curr[1]];
        const l1 = Math.hypot(e1[0], e1[1]) || 1e-9;
        const l2 = Math.hypot(e2[0], e2[1]) || 1e-9;
        // Inward normal for CCW polygon: rotate edge CCW by 90° → (-ey, ex)
        const n1 = [-e1[1] / l1, e1[0] / l1];
        const n2 = [-e2[1] / l2, e2[0] / l2];
        const bx = n1[0] + n2[0], by = n1[1] + n2[1];
        const bl = Math.hypot(bx, by);
        if (bl < 1e-9) {
            out.push([curr[0] + d * n1[0], curr[1] + d * n1[1]]);
            continue;
        }
        const dot = (n1[0] * bx + n1[1] * by) / bl;
        const scale = dot > 0.1 ? d / dot : d * 3; // clamp acute corners
        out.push([curr[0] + scale * bx / bl, curr[1] + scale * by / bl]);
    }
    return out;
}

function bbox2d(pts) {
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const [x, y] of pts) {
        if (x < minX) minX = x; if (x > maxX) maxX = x;
        if (y < minY) minY = y; if (y > maxY) maxY = y;
    }
    return { minX, maxX, minY, maxY, cx: (minX + maxX) / 2, cy: (minY + maxY) / 2 };
}

function makeShape2D(pts) {
    const s = new THREE.Shape();
    s.moveTo(pts[0][0], pts[0][1]);
    for (let i = 1; i < pts.length; i++) s.lineTo(pts[i][0], pts[i][1]);
    s.closePath();
    return s;
}

/** Flat filled polygon (no extrusion) — at local z = zOff. */
function flatMesh(pts, zOff, material) {
    const geo = new THREE.ShapeGeometry(makeShape2D(pts));
    const mesh = new THREE.Mesh(geo, material);
    mesh.position.z = zOff;
    return mesh;
}

/** Extruded solid from z=0 to z=height. */
function extrudeMesh(pts, height, material) {
    const geo = new THREE.ExtrudeGeometry(makeShape2D(pts), {
        depth: height, bevelEnabled: false,
    });
    return new THREE.Mesh(geo, material);
}

/**
 * Polygon outline as LineLoop at local z.
 * pts: [[x, y], ...]
 */
function outlineLoop(pts, zOff, color, dashed = false) {
    const verts = [];
    for (const [x, y] of pts) verts.push(x, y, zOff);
    verts.push(pts[0][0], pts[0][1], zOff); // close
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.Float32BufferAttribute(verts, 3));
    const mat = dashed
        ? new THREE.LineDashedMaterial({ color, dashSize: 0.5, gapSize: 0.3, linewidth: 1 })
        : new THREE.LineBasicMaterial({ color });
    const line = new THREE.Line(geo, mat);
    if (dashed) line.computeLineDistances();
    return line;
}

/** Annular (donut) flat mesh — lot outline minus footprint inset. */
function annularMesh(outer, inner, zOff, material) {
    const s = makeShape2D(outer);
    const hole = new THREE.Path();
    hole.moveTo(inner[0][0], inner[0][1]);
    for (let i = 1; i < inner.length; i++) hole.lineTo(inner[i][0], inner[i][1]);
    hole.closePath();
    s.holes.push(hole);
    const geo = new THREE.ShapeGeometry(s);
    const mesh = new THREE.Mesh(geo, material);
    mesh.position.z = zOff;
    return mesh;
}

// ── Main export ───────────────────────────────────────────────────────────────

export function buildMassing(calcData, container) {
    const d = calcData;
    const rings = d?.lote?.geojson_polygon;
    if (!rings || !rings[0] || rings[0].length < 3) return null;

    // Project WGS84 rings to local metric (centred at lot centroid).
    // Strip the GeoJSON closing vertex (= ring[0]) before processing: the duplicate
    // creates a zero-length edge in insetPolygon, corrupting the area by ~9×.
    const raw = rings[0];
    const last = raw[raw.length - 1];
    const open = (raw.length > 1 && last[0] === raw[0][0] && last[1] === raw[0][1])
        ? raw.slice(0, -1) : raw;
    const lon0 = open.reduce((s, p) => s + p[0], 0) / open.length;
    const lat0 = open.reduce((s, p) => s + p[1], 0) / open.length;
    const lotPts = ensureCCW(projectRing(open, lon0, lat0));
    const bb = bbox2d(lotPts);
    const span = Math.max(bb.maxX - bb.minX, bb.maxY - bb.minY);

    // Setbacks from calc result
    const metrics = d.metrics || {};
    const ant       = d.antejardin?.dimension_m  ?? null;   // front
    const aislPost  = metrics.aislamiento_posterior_m?.valor ?? null;
    const aislLat   = metrics.aislamiento_lateral_m?.valor  ?? null;  // 0 if continua

    // Which setbacks are missing?
    const missingAnt      = ant       === null;
    const missingPost     = aislPost  === null;
    const missingLat      = aislLat   === null;
    const anyMissing      = missingAnt || missingPost || missingLat;

    // Conservative uniform inset from known values
    const known = [ant, aislPost, aislLat].filter(v => v !== null && v >= 0);
    const uniformInset = known.length ? Math.max(...known) : 0;
    const footprint = uniformInset > 0.5 ? insetPolygon(lotPts, uniformInset) : lotPts;

    // Heights
    const pisos        = metrics.altura_base_pisos?.valor
                      || metrics.altura_maxima_pisos?.valor
                      || null;
    const heightM      = pisos ? pisos * FLOOR_H : null;
    const facadeAm     = metrics.retroceso_fachada_A_m?.valor ?? null; // max facade HEIGHT

    // ── Three.js scene setup ──────────────────────────────────────────────────
    const W = container.clientWidth  || 480;
    const H = container.clientHeight || 380;

    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(W, H);
    renderer.shadowMap.enabled = true;
    container.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0xf0ede8);

    const camera = new THREE.PerspectiveCamera(45, W / H, 0.5, 10_000);
    const camH = heightM ? heightM * 2.2 : span * 0.9;
    // In world space (after group rotation): x = East, y = up, z = -North
    camera.position.set(bb.cx + span * 1.2, camH, -(bb.cy - span * 1.4));
    camera.lookAt(bb.cx, (heightM || 0) * 0.4, -bb.cy);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.target.set(bb.cx, (heightM || 0) * 0.4, -bb.cy);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.minDistance = span * 0.3;
    controls.maxDistance = span * 12;
    controls.update();

    // Lights
    scene.add(new THREE.AmbientLight(0xffffff, 0.55));
    const sun = new THREE.DirectionalLight(0xffffff, 0.9);
    sun.position.set(span * 3, span * 4, -span * 2);
    sun.castShadow = true;
    scene.add(sun);

    // ── Geometry group: rotate so local Z (extrusion) becomes world Y (up)
    // After rotation.x = -PI/2:  local (x, y, z) → world (x, z, -y)
    // So the 2D footprint (local XY at z=0) → world XZ at y=0 ✓
    // and extrusion depth (local Z) → world Y (up) ✓
    const g = new THREE.Group();
    g.rotation.x = -Math.PI / 2;
    scene.add(g);

    // Ground / lot fill
    g.add(flatMesh(lotPts, -0.02, new THREE.MeshLambertMaterial({
        color: 0xd8d0b8, side: THREE.DoubleSide,
    })));

    // Lot outline
    g.add(outlineLoop(lotPts, 0.05, 0x888866));

    // Setback zone
    if (uniformInset > 0.5 && footprint !== lotPts && footprint.length >= 3) {
        const sbColor = anyMissing ? 0xffcccc : 0xffd9a0;
        g.add(annularMesh(lotPts, footprint, 0.03, new THREE.MeshLambertMaterial({
            color: sbColor, side: THREE.DoubleSide, transparent: true, opacity: 0.5,
        })));
        g.add(outlineLoop(footprint, 0.08,
            anyMissing ? 0xcc3333 : 0xcc7722,
            anyMissing /* dashed if incomplete */));
    }

    // Building volume
    if (heightM && footprint.length >= 3) {
        const buildMat = new THREE.MeshLambertMaterial({
            color: 0x5580c8, transparent: true, opacity: 0.72,
        });
        g.add(extrudeMesh(footprint, heightM, buildMat));

        // Building wireframe edges (top + bottom outlines)
        g.add(outlineLoop(footprint, 0.1, 0x2255a8));
        g.add(outlineLoop(footprint, heightM - 0.05, 0x2255a8));

        // Floor plates
        for (let f = 1; f <= pisos; f++) {
            const z = f * FLOOR_H;
            g.add(outlineLoop(footprint, z, 0x4466bb));
        }
    } else if (!heightM) {
        // Flat footprint with hatch if no height data
        if (footprint.length >= 3) {
            g.add(flatMesh(footprint, 0.06, new THREE.MeshLambertMaterial({
                color: 0x8899cc, side: THREE.DoubleSide, transparent: true, opacity: 0.45,
            })));
            g.add(outlineLoop(footprint, 0.08, 0x3355aa, true));
        }
    }

    // Facade height plane (A = retroceso_fachada_A_m) — horizontal translucent disc
    if (facadeAm && heightM && facadeAm > 0.5 && facadeAm < heightM + 10) {
        const pw = (bb.maxX - bb.minX) * 1.3;
        const ph = (bb.maxY - bb.minY) * 1.3;
        const planeGeo = new THREE.PlaneGeometry(pw, ph);
        const planeMesh = new THREE.Mesh(planeGeo, new THREE.MeshLambertMaterial({
            color: 0xffaa00, side: THREE.DoubleSide, transparent: true, opacity: 0.22,
        }));
        planeMesh.position.set(bb.cx, bb.cy, facadeAm);
        g.add(planeMesh);
        // Outline of the plane at facade height
        const planeOutline = [
            [bb.cx - pw / 2, bb.cy - ph / 2],
            [bb.cx + pw / 2, bb.cy - ph / 2],
            [bb.cx + pw / 2, bb.cy + ph / 2],
            [bb.cx - pw / 2, bb.cy + ph / 2],
        ];
        g.add(outlineLoop(planeOutline, facadeAm, 0xdd8800));
    }

    // Grid on the ground (subtle)
    const gridHelper = new THREE.GridHelper(span * 3, Math.ceil(span / 5), 0xcccccc, 0xe8e8e8);
    gridHelper.position.set(bb.cx, 0, -bb.cy);
    scene.add(gridHelper);

    // ── Animation loop ────────────────────────────────────────────────────────
    let animId;
    function animate() {
        animId = requestAnimationFrame(animate);
        controls.update();
        renderer.render(scene, camera);
    }
    animate();

    // Resize handler
    function onResize() {
        const W = container.clientWidth;
        const H = container.clientHeight;
        if (!W || !H) return;
        camera.aspect = W / H;
        camera.updateProjectionMatrix();
        renderer.setSize(W, H);
    }
    const resizeObs = new ResizeObserver(onResize);
    resizeObs.observe(container);

    // ── Metadata for UI overlay ───────────────────────────────────────────────
    const meta = {
        pisos,
        heightM,
        uniformInset,
        ant, aislPost, aislLat,
        missingAnt, missingPost, missingLat, anyMissing,
        facadeAm,
        areaLote: d.lote?.area_m2?.valor ?? null,
        areaFootprint: footprint !== lotPts && footprint.length >= 3
            ? Math.abs(signedArea(footprint))
            : null,
    };

    return {
        dispose() {
            cancelAnimationFrame(animId);
            resizeObs.disconnect();
            controls.dispose();
            renderer.dispose();
            if (renderer.domElement.parentNode === container) {
                container.removeChild(renderer.domElement);
            }
        },
        meta,
    };
}

export function disposeMassing(instance) {
    instance?.dispose();
}
