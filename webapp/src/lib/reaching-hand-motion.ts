import * as THREE from 'three';

export type ReachingHandRig = {
  boneCount: number;
  jointNames: string[];
  joints: number[];
  curlAxes: number[];
  surfaceWeights: number[];
  nodeWeights: number[];
};

export type ReachingHandGraph = {
  schemaVersion: 2;
  positions: number[];
  triangles: number[];
  nodes: number[];
  normals: number[];
  surfaceNormals: number[];
  spacing: number[];
  edges: number[];
  rig: ReachingHandRig;
  body: {
    vertexIds: number[]; nodeMask: boolean[];
    originalNodeIds: number[]; originalNodes: number[];
    growthNodeIds: number[];
    originalNodeWeights: number[]; originalNodeNormals: number[];
    positions: number[]; nodes: number[];
    surfaceNormals: number[]; nodeNormals: number[];
    surfaceWeights: number[]; nodeWeights: number[];
  };
};

/** Reject stale geometry before any animation frame can dereference it. */
export function isReachingHandGraph(value: unknown): value is ReachingHandGraph {
  const graph = value as Partial<ReachingHandGraph> | null;
  if (!graph || graph.schemaVersion !== 2 || !graph.body || !graph.rig) return false;
  const { body, rig } = graph;
  const arrays = [graph.positions, graph.triangles, graph.nodes, graph.normals, graph.surfaceNormals, graph.spacing, graph.edges,
    rig.joints, rig.curlAxes, rig.surfaceWeights, rig.nodeWeights,
    body.vertexIds, body.nodeMask, body.positions, body.nodes, body.surfaceNormals, body.nodeNormals,
    body.surfaceWeights, body.nodeWeights, body.originalNodeIds, body.originalNodes, body.originalNodeWeights, body.originalNodeNormals, body.growthNodeIds];
  if (!arrays.every(Array.isArray)) return false;
  return graph.surfaceNormals!.length === graph.positions!.length
    && body.positions.length === graph.positions!.length
    && body.nodes.length === graph.nodes!.length
    && body.growthNodeIds.length * 3 === graph.nodes!.length
    && body.growthNodeIds.every(id => Number.isInteger(id) && id >= 0 && id < body.nodeMask.length && body.nodeMask[id])
    && body.originalNodes.length === body.originalNodeIds.length * 3
    && body.originalNodeNormals.length === body.originalNodes.length
    && body.originalNodeWeights.length === body.originalNodeIds.length * rig.boneCount;
}

/** Independent phalanges retain their length as the fingers relax into the reach. */
export function createReachingHandMotion(rig: ReachingHandRig, side: 0 | 1) {
  const pivots = Array.from({ length: rig.boneCount }, (_, i) => new THREE.Vector3().fromArray(rig.joints, i * 3));
  const axes = Array.from({ length: rig.boneCount }, (_, i) => new THREE.Vector3().fromArray(rig.curlAxes, i * 3).normalize());
  const angles = new Float32Array(rig.boneCount);
  const offset = new THREE.Vector3(), cross = new THREE.Vector3();
  // Wrist, then MCP/PIP/DIP for thumb, index, middle, ring and little finger.
  // Thumb opposition stays close to the palm; the other fingers form a cascade.
  const rest = side === 0
    ? [.10, -.50, -.25, 0, .06, .10, .04, .35, .50, .18, .40, .58, .22, .48, .63, .22]
    : [.02, -.50, -.25, 0, -.30, 0, .04, .10, .35, .15, .16, .45, .20, .25, .52, .22];
  const enteringCurl = [.22, .10, .12, 0, .24, .42, .18, .28, .38, .16, .30, .40, .18, .32, .42, .20];

  function deform(point: THREE.Vector3, weights: ArrayLike<number>, start: number) {
    // Distal before proximal: fingertip bends follow their knuckle and wrist.
    for (let i = rig.boneCount - 1; i >= 0; i--) {
      const angle = angles[i] * weights[start + i];
      if (Math.abs(angle) < .00001) continue;
      const axis = axes[i];
      offset.copy(point).sub(pivots[i]);
      cross.crossVectors(axis, offset);
      const cos = Math.cos(angle), sin = Math.sin(angle), projection = offset.dot(axis);
      point.copy(pivots[i]).addScaledVector(offset, cos).addScaledVector(cross, sin).addScaledVector(axis, projection * (1 - cos));
    }
    return point;
  }

  function update(time: number, reduced = false, articulation = 1, entrance = 1) {
    const t = reduced ? 0 : time;
    // Adam's hand hangs from a relaxed wrist; the other hand points toward it.
    // Curl the remaining fingers independently, rather than rotating a flat fan.
    rest.forEach((angle, i) => {
      const movement = i === 0 ? .012 : i >= 4 && i <= 6 ? .008 : .018;
      const finger = Math.max(0, Math.floor((i - 1) / 3));
      // Arrive with a softly bent wrist and curled fingers. The index reaches
      // first, then the other fingers relax in a small cascade. Entrance also
      // runs backward on return, so the gesture retraces without a pose jump.
      const delay = i === 0 ? 0 : Math.max(0, finger - 1) * .06;
      const curl = side === 0 && !reduced
        ? enteringCurl[i] * (1 - THREE.MathUtils.smootherstep(entrance, .08 + delay, .82 + delay)) : 0;
      angles[i] = -(angle + curl + (reduced ? 0 : Math.sin(t * .45 + Math.floor((i - 1) / 3) * .34 + side) * movement)) * articulation;
    });
  }

  return { update, deform, angles };
}
