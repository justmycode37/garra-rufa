import * as THREE from 'three';
import { createBodyMotion, type HumanRig } from './body-motion';
import type { ConnectionGraph } from './connection-graph';
import { FIGURE_ORIENTATION } from './hand-layout';

/** One compact patch of two adjoining graph cells follows the animated nodes. */
export function createBodyHighlight(human: ConnectionGraph & { rig: HumanRig }, color: THREE.Color) {
  const nodes = human.spacing.map((_, i) => new THREE.Vector3().fromArray(human.nodes, i * 3));
  const neighbors = nodes.map(() => new Set<number>());
  for (let i = 0; i < human.edges.length; i += 2) {
    const a = human.edges[i], b = human.edges[i + 1];
    neighbors[a].add(b); neighbors[b].add(a);
  }
  const triangles: number[][] = [];
  neighbors.forEach((links, a) => {
    for (const b of links) if (b > a) {
      for (const c of links) if (c > b && neighbors[b].has(c)) triangles.push([a, b, c]);
    }
  });

  // Prefer larger cells facing the viewer, then join a second cell across
  // one edge. This stays legible without coloring an entire hand or leg.
  const motion = createBodyMotion(human.rig);
  const center = new THREE.Vector3(), normal = new THREE.Vector3();
  const cross = new THREE.Vector3(), edge = new THREE.Vector3();
  const poseTimes = [5.5, 11, 21, 34];
  const cells = motion.focusRegions.map((region, regionIndex) => {
    motion.update(poseTimes[regionIndex]);
    const posed = nodes.map((point, i) => motion.deform(point.clone(), human.rig.nodeWeights, i * human.rig.boneCount).applyEuler(FIGURE_ORIENTATION));
    const candidates: { ids: number[]; area: number; score: number }[] = [];
    for (const ids of triangles) {
      center.copy(nodes[ids[0]]).add(nodes[ids[1]]).add(nodes[ids[2]]).multiplyScalar(1 / 3);
      const distance = center.distanceToSquared(region.center);
      if (distance > region.radius ** 2) continue;
      const [a, b, c] = ids.map(id => posed[id]);
      const area = Math.abs(cross.subVectors(b, a).cross(edge.subVectors(c, a)).z);
      if (area < .0005) continue;
      let facing = 0;
      for (const id of ids) {
        normal.fromArray(human.normals, id * 3).add(nodes[id]);
        motion.deform(normal, human.rig.nodeWeights, id * human.rig.boneCount).applyEuler(FIGURE_ORIENTATION).sub(posed[id]);
        facing += normal.z;
      }
      if (facing < .6) continue;
      candidates.push({ ids, area, score: distance * .1 + .001 / area });
    }
    candidates.sort((a, b) => a.score - b.score);
    const best = candidates[0];
    if (!best) return;
    const adjacent = candidates.filter(candidate => {
      const shared = candidate.ids.filter(id => best.ids.includes(id));
      if (shared.length !== 2) return false;
      const a = posed[shared[0]], b = posed[shared[1]];
      const firstTip = posed[best.ids.find(id => !shared.includes(id))!];
      const secondTip = posed[candidate.ids.find(id => !shared.includes(id))!];
      const side = (point: THREE.Vector3) => (b.x - a.x) * (point.y - a.y) - (b.y - a.y) * (point.x - a.x);
      // Shared-edge triangles must sit on opposite sides, never overlap.
      return side(firstTip) * side(secondTip) < 0;
    }).sort((a, b) => b.area - a.area)[0];
    return adjacent ? [...best.ids, ...adjacent.ids] : best.ids;
  });

  const geometry = new THREE.BufferGeometry();
  const positions = new THREE.Float32BufferAttribute(new Float32Array(18), 3).setUsage(THREE.DynamicDrawUsage);
  geometry.setAttribute('position', positions);
  // The flat cell spans the curved skin mask, so draw its fill before the
  // black graph without letting that invisible mask cut holes in the color.
  const material = new THREE.MeshBasicMaterial({ color, side: THREE.DoubleSide, transparent: true, depthWrite: false, depthTest: false });
  const mesh = new THREE.Mesh(geometry, material);
  mesh.frustumCulled = false;
  mesh.renderOrder = .5;
  mesh.visible = false;

  return {
    mesh,
    update(region: number, strength: number, nodePosition: (index: number) => THREE.Vector3) {
      const cell = cells[region - 1];
      mesh.visible = !!cell && strength > 0;
      if (!mesh.visible || !cell) return;
      cell.forEach((id, i) => {
        const point = nodePosition(id);
        positions.setXYZ(i, point.x, point.y, point.z);
      });
      geometry.setDrawRange(0, cell.length);
      positions.needsUpdate = true;
      material.opacity = strength;
    },
    dispose() { geometry.dispose(); material.dispose(); },
  };
}
