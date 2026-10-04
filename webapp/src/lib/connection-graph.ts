import * as THREE from 'three';

export type ConnectionGraph = {
  positions: number[]; triangles: number[]; nodes: number[];
  normals: number[]; spacing: number[]; edges: number[];
  nodeVisibility?: boolean[];
  normalTriangles?: number[];
};

const noise = (i: number) => { const n = Math.sin(i * 127.1 + 311.7) * 43758.5453; return n - Math.floor(n); };

/** One depth-masked graph, shared by the full figure and the close-up hands. */
export function createConnectionGraph(asset: ConnectionGraph, fine = false, markScale = 1) {
  let growth = 1;
  const group = new THREE.Group();
  const surfaceGeometry = new THREE.BufferGeometry();
  surfaceGeometry.setAttribute('position', new THREE.Float32BufferAttribute(asset.positions, 3));
  surfaceGeometry.setIndex(asset.normalTriangles ?? asset.triangles);
  surfaceGeometry.computeVertexNormals();
  surfaceGeometry.setIndex(asset.triangles);
  const vertices = surfaceGeometry.getAttribute('position') as THREE.BufferAttribute;
  const normals = surfaceGeometry.getAttribute('normal');
  const inset = fine ? .026 : .022;
  for (let i = 0; i < vertices.count; i++) {
    vertices.setXYZ(i, vertices.getX(i) - normals.getX(i) * inset, vertices.getY(i) - normals.getY(i) * inset, vertices.getZ(i) - normals.getZ(i) * inset);
  }
  vertices.setUsage(THREE.DynamicDrawUsage);
  const rest = new Float32Array(vertices.array);
  const mask = new THREE.MeshBasicMaterial({ colorWrite: false, side: THREE.DoubleSide });
  const surface = new THREE.Mesh(surfaceGeometry, mask);
  const dotGeometry = new THREE.SphereGeometry(1, 7, 5);
  const edgeGeometry = new THREE.CylinderGeometry(1, 1, 1, 5, 1);
  const ink = new THREE.MeshBasicMaterial({ color: 0x141314, depthWrite: false, transparent: true });
  const count = asset.nodes.length / 3, edgeCount = asset.edges.length / 2;
  const dots = new THREE.InstancedMesh(dotGeometry, ink, count);
  const lines = new THREE.InstancedMesh(edgeGeometry, ink, edgeCount);
  dots.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
  lines.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
  surface.frustumCulled = dots.frustumCulled = lines.frustumCulled = false;
  surface.renderOrder = 0; lines.renderOrder = 1; dots.renderOrder = 2;
  group.add(surface, lines, dots);
  const point = new THREE.Vector3(), direction = new THREE.Vector3(), origin = new THREE.Vector3();
  const points = Array.from({ length: count }, () => new THREE.Vector3());
  const normal = new THREE.Vector3(), axis = new THREE.Vector3(0, 1, 0);
  const instance = new THREE.Object3D();
  const radii = asset.spacing.map((spacing, i) => asset.nodeVisibility?.[i] === false ? 0 : (fine ? .0068 + noise(i) * .0012 : .010 + noise(i) * .008) * Math.min(1, spacing / .06));
  const strokes = Array.from({ length: edgeCount }, (_, i) => (fine ? .0021 + noise(i + 300) * .0003 : .0036 + noise(i + 300) * .0021) * Math.min(1, Math.min(asset.spacing[asset.edges[i * 2]], asset.spacing[asset.edges[i * 2 + 1]]) / .06));

  function update(deform: (p: THREE.Vector3, index: number, surface: boolean) => void, growthOrigin?: (p: THREE.Vector3, index: number) => void) {
    for (let i = 0; i < vertices.count; i++) {
      point.fromArray(rest, i * 3); deform(point, i, true);
      vertices.setXYZ(i, point.x, point.y, point.z);
    }
    vertices.needsUpdate = true;
    for (let i = 0; i < count; i++) {
      const p = points[i].fromArray(asset.nodes, i * 3);
      normal.fromArray(asset.normals, i * 3);
      p.addScaledVector(normal, .008);
      deform(p, i, false);
      // New samples branch from an existing, already posed body node. All
      // connecting edges stay attached while those samples spread apart.
      if (growth < 1 && growthOrigin) {
        growthOrigin(origin, i);
        p.lerp(origin, 1 - growth);
      }
      instance.position.copy(p); instance.quaternion.identity();
      instance.scale.setScalar(radii[i] * markScale * growth); instance.updateMatrix();
      dots.setMatrixAt(i, instance.matrix);
    }
    for (let i = 0; i < edgeCount; i++) {
      const a = points[asset.edges[i * 2]], b = points[asset.edges[i * 2 + 1]];
      direction.subVectors(b, a);
      const length = direction.length();
      instance.position.copy(a).addScaledVector(direction, .5);
      if (length > .00001) instance.quaternion.setFromUnitVectors(axis, direction.normalize());
      instance.scale.set(strokes[i] * markScale * growth, length, strokes[i] * markScale * growth); instance.updateMatrix();
      lines.setMatrixAt(i, instance.matrix);
    }
    dots.instanceMatrix.needsUpdate = lines.instanceMatrix.needsUpdate = true;
  }

  return { group, update, nodePosition(index: number) { return points[index]; }, setSurfaceVisible(visible: boolean) { surface.visible = visible; }, setGrowth(value: number) { growth = value; }, setMarkScale(value: number) { markScale = value; }, setOpacity(opacity: number) { ink.opacity = opacity; }, setClipPlane(plane: THREE.Plane) {
    mask.clippingPlanes = [plane]; ink.clippingPlanes = [plane];
  }, dispose() {
    surfaceGeometry.dispose(); dotGeometry.dispose(); edgeGeometry.dispose();
    mask.dispose(); ink.dispose(); dots.dispose(); lines.dispose();
  } };
}
