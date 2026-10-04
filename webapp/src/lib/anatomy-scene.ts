import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { diseases } from './knowledge';
import { placeAtlasLabels, SILHOUETTE_STEP, type LabelCandidate, type PlacedLabel, type SilhouetteRow } from './atlas-labels';
import { AtlasLabelMotion, type AnimatedAtlasLabel } from './atlas-label-motion';
import { ATLAS_GRAPH_ZOOM, atlasRegions } from './atlas-graph';

export type AnatomyGraph = { positions: number[]; triangles: number[]; nodes: number[]; normals: number[]; spacing: number[]; edges: number[] };
type OrganGraph = { nodes: number[]; normals: number[]; edges: number[]; spacing: number };
type OrganNetwork = { id: string; category: string; spacing: number; lines: THREE.LineSegments<THREE.BufferGeometry, THREE.LineBasicMaterial>; dots: THREE.Points<THREE.BufferGeometry, THREE.PointsMaterial> };
type Point3 = [number, number, number];
type Organ = { id: string; name: string; category: string; anchor: Point3; center: Point3; bounds: [Point3, Point3]; structures: string[] };
export type AnatomyManifest = { organs: Organ[] };
export type AnatomyFrame = { zoom: number; labels: AnimatedAtlasLabel[]; detailReady: boolean; detailError: boolean; region: string; rotated: boolean; regionId?: string; regionAnchor?: { x: number; y: number }; viewport?: { width: number; height: number } };
type Target = { id: string; title: string; point: THREE.Vector3; zoom: number; conditions: string[] };

export class AnatomyScene {
  private renderer: THREE.WebGLRenderer;
  private scene = new THREE.Scene();
  private camera = new THREE.OrthographicCamera(-5, 5, 4, -4, .1, 100);
  private controls: OrbitControls;
  private surface: THREE.Mesh;
  private organNetworks: OrganNetwork[] = [];
  private pickingMaterial: THREE.MeshBasicMaterial;
  private lineMaterial: THREE.LineBasicMaterial;
  private dotMaterial: THREE.PointsMaterial;
  private surfaceDetail = { value: 0 };
  private organMeshes: THREE.Mesh[] = [];
  private disposed = false;
  private detailReady = false;
  private detailError = false;
  private width = 1;
  private height = 1;
  private frame = 0;
  private observer: ResizeObserver;
  private pointerStart = { x: 0, y: 0 };
  private pointerMoved = false;
  private pointers = new Set<number>();
  private tween: { started: number; fromTarget: THREE.Vector3; toTarget: THREE.Vector3; fromZoom: number; toZoom: number; reset: boolean; fromPosition: THREE.Vector3 } | null = null;
  private targets: Target[];
  private nodePoints: THREE.Vector3[];
  private anchorPoints = new Map<string, THREE.Vector3>();
  private projectVector = new THREE.Vector3();
  private lastRegion = '';
  private focusedPart: string | null = null;
  private labelMotion = new AtlasLabelMotion();
  private labelPlacements: PlacedLabel[] = [];
  private labelPoints = new Map<string, THREE.Vector3>();
  private detailLabels = false;
  private labelRegion: Target | undefined;
  private graphRegion = '';
  private focusPoint: THREE.Vector3 | null = null;
  private regionalPoints = new Map<string, THREE.Vector3[]>();

  constructor(private canvas: HTMLCanvasElement, private graph: AnatomyGraph, private manifest: AnatomyManifest, private onFrame: (frame: AnatomyFrame) => void, private onContextLost: () => void, private onSelection: (id: string, title: string) => void = () => {}) {
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false, powerPreference: 'low-power' });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setClearColor(0xffffff);
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.camera.position.set(0, 0, 12);
    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = .11;
    this.controls.rotateSpeed = .65;
    this.controls.zoomSpeed = .85;
    this.controls.zoomToCursor = true;
    this.controls.minZoom = 1;
    this.controls.maxZoom = 14;
    this.controls.minPolarAngle = .08;
    this.controls.maxPolarAngle = Math.PI - .08;
    this.controls.addEventListener('change', this.requestDraw);
    this.controls.addEventListener('start', this.cancelTween);
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(graph.positions, 3));
    geometry.setIndex(graph.triangles);
    geometry.computeVertexNormals();
    // Solid geometry is only a hit target. It must never hide the graph's
    // rear connections or internal anatomy, at any zoom or viewing angle.
    this.pickingMaterial = new THREE.MeshBasicMaterial({ visible: false, colorWrite: false, depthWrite: false, side: THREE.DoubleSide });
    this.surface = new THREE.Mesh(geometry, this.pickingMaterial);
    this.surface.visible = false;
    this.scene.add(this.surface);
    this.nodePoints = Array.from({ length: graph.nodes.length / 3 }, (_, i) => new THREE.Vector3().fromArray(graph.nodes, i * 3));
    const lifted = this.nodePoints.map((point, i) => point.clone().addScaledVector(new THREE.Vector3().fromArray(graph.normals, i * 3), .008));
    // Dense facial, hand, and foot samples should have the same visual weight
    // as the torso in the overview. Line coverage scales with spacing, while
    // point coverage scales with its square. Keep every connection intact.
    const overviewSpacing = Math.max(...graph.spacing);
    const density = graph.spacing.map(spacing => Math.min(1, spacing / overviewSpacing));
    const lineGeometry = new THREE.BufferGeometry().setFromPoints(graph.edges.map(index => lifted[index]));
    lineGeometry.setAttribute('color', new THREE.Float32BufferAttribute(graph.edges.flatMap(index => [1, 1, 1, density[index]]), 4));
    this.lineMaterial = new THREE.LineBasicMaterial({ color: 0x000000, vertexColors: true, transparent: true, opacity: .55, depthWrite: false, depthTest: false });
    const lines = new THREE.LineSegments(lineGeometry, this.lineMaterial);
    lines.renderOrder = 3;
    this.scene.add(lines);
    const dotGeometry = new THREE.BufferGeometry().setFromPoints(lifted);
    dotGeometry.setAttribute('color', new THREE.Float32BufferAttribute(density.flatMap(weight => [1, 1, 1, weight * weight]), 4));
    this.dotMaterial = new THREE.PointsMaterial({ color: 0x000000, vertexColors: true, size: 2, sizeAttenuation: false, transparent: true, depthWrite: false, depthTest: false });
    // A shared zoom uniform restores full detail without rebuilding or
    // uploading the graph on every orbit/zoom frame.
    for (const material of [this.lineMaterial, this.dotMaterial]) {
      material.onBeforeCompile = shader => {
        shader.uniforms.surfaceDetail = this.surfaceDetail;
        shader.vertexShader = `uniform float surfaceDetail;\n${shader.vertexShader}`
          .replace('#include <color_vertex>', '#include <color_vertex>\nvColor.a = mix(vColor.a, 1.0, surfaceDetail);');
      };
    }
    const dots = new THREE.Points(dotGeometry, this.dotMaterial);
    dots.renderOrder = 4;
    this.scene.add(dots);

    const organPoint = (id: string, fallback: Point3) => new THREE.Vector3().fromArray(manifest.organs.find(o => o.id === id)?.center ?? fallback);
    this.targets = [
      { id: 'brain', title: 'Brain & nerves', point: organPoint('brain', [0, 3.2, 0]), zoom: 4.2, conditions: ['huntington', 'rett'] },
      { id: 'eyes', title: 'Eyes', point: organPoint('eyes', [0, 3, .3]), zoom: 10, conditions: ['marfan'] },
      { id: 'heart', title: 'Heart', point: organPoint('heart', [.16, 1.8, .2]), zoom: 4.4, conditions: ['fabry', 'marfan', 'pompe'] },
      { id: 'skeleton', title: 'Skeleton', point: organPoint('pelvis', [0, .2, 0]), zoom: 1.12, conditions: [] },
      { id: 'liver', title: 'Abdomen', point: organPoint('liver', [-.15, 1.1, .2]), zoom: 3.8, conditions: [] },
      { id: 'hands', title: 'Hands & joints', point: this.nearestSkinPoint([-1.17, -.4, .35]), zoom: 4.4, conditions: ['cmt', 'eds', 'fabry', 'marfan', 'rett'] },
      { id: 'muscles', title: 'Muscles', point: this.nearestSkinPoint([.4, -1.0, .23]), zoom: 3.1, conditions: ['sma', 'pompe'] },
      { id: 'legs', title: 'Legs & feet', point: this.nearestSkinPoint([-.31, -2.78, .35]), zoom: 3.5, conditions: ['cmt', 'eds', 'sma', 'pompe'] },
    ];
    // Every condition gets a separate, stable anatomical attachment. Anchors
    // represent the region association in our curated records, not a lesion.
    this.targets.forEach(target => {
      target.conditions.forEach((id, i) => {
        const offset = new THREE.Vector3((i % 2 ? 1 : -1) * (.07 + i * .035), (i - (target.conditions.length - 1) / 2) * .1, .18 + i * .016);
        const desired = target.point.clone().add(offset);
        const anchor = ['hands', 'muscles', 'legs'].includes(target.id) ? this.nearestSkinPoint(desired.toArray() as Point3, new Set([...this.anchorPoints.values()].map(p => p.toArray().join(',')))) : desired;
        this.anchorPoints.set(`${target.id}-${id}`, anchor);
      });
    });
    this.observer = new ResizeObserver(this.resize);
    this.observer.observe(canvas.parentElement!);
    canvas.addEventListener('pointerdown', this.pointerDown);
    canvas.addEventListener('pointermove', this.pointerMove);
    canvas.addEventListener('pointerup', this.pointerUp);
    canvas.addEventListener('pointercancel', this.pointerCancel);
    canvas.addEventListener('webglcontextlost', this.contextLost);
    this.resize();
    this.loadOrgans();
  }

  private nearestSkinPoint(point: Point3, used = new Set<string>()) {
    const target = new THREE.Vector3().fromArray(point);
    let best = this.nodePoints[0], distance = Infinity;
    for (const candidate of this.nodePoints) {
      const d = candidate.distanceToSquared(target);
      if (d < distance && !used.has(candidate.toArray().join(','))) { best = candidate; distance = d; }
    }
    return best.clone();
  }

  private async loadOrgans() {
    try {
      const [modelResult, graphResult] = await Promise.allSettled([
        new GLTFLoader().loadAsync('/models/anatomy/organs.glb'),
        fetch('/models/anatomy/organs-graph.json').then(async response => {
          if (!response.ok) throw new Error('Organ graphs unavailable');
          return response.json() as Promise<Record<string, OrganGraph>>;
        }),
      ]);
      if (modelResult.status === 'rejected' || graphResult.status === 'rejected') {
        if (modelResult.status === 'fulfilled') this.disposeObject(modelResult.value.scene);
        throw new Error('Anatomical detail unavailable');
      }
      const result = modelResult.value, graphs = graphResult.value;
      if (this.disposed) { this.disposeObject(result.scene); return; }
      result.scene.traverse(object => {
        if (!(object instanceof THREE.Mesh)) return;
        const original = object.material;
        (Array.isArray(original) ? original : [original]).forEach(material => material.dispose());
        const category = object.userData.category ?? this.manifest.organs.find(o => o.id === object.name)?.category ?? 'organ';
        const graph = graphs[object.name];
        object.userData.category = category;
        object.material = new THREE.MeshBasicMaterial({ visible: false, colorWrite: false, depthWrite: false, side: THREE.DoubleSide });
        object.visible = false;
        this.organMeshes.push(object);
        if (!graph) return;
        const points = Array.from({ length: graph.nodes.length / 3 }, (_, i) => new THREE.Vector3().fromArray(graph.nodes, i * 3).addScaledVector(new THREE.Vector3().fromArray(graph.normals, i * 3), graph.spacing * .22));
        // A small, evenly sampled spatial index handles paired organs and limbs,
        // whose shared centre may be nowhere near the part being viewed.
        this.regionalPoints.set(object.name, points.filter((_, i) => i % Math.max(1, Math.ceil(points.length / 180)) === 0));
        const lines = new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(graph.edges.map(index => points[index])), new THREE.LineBasicMaterial({ color: 0x000000, transparent: true, opacity: 0, depthWrite: false, depthTest: false }));
        const dots = new THREE.Points(new THREE.BufferGeometry().setFromPoints(points), new THREE.PointsMaterial({ color: 0x000000, size: 1.8, sizeAttenuation: false, transparent: true, opacity: 0, depthWrite: false, depthTest: false }));
        lines.renderOrder = 2; dots.renderOrder = 3;
        dots.name = object.name;
        this.scene.add(lines, dots);
        this.organNetworks.push({ id: object.name, category, spacing: graph.spacing, lines, dots });
        // Attach to real, distinct graph nodes, fixed in model space through
        // every rotation and pan. A condition is never attached to a hub.
        const target = this.targets.find(item => item.id === object.name);
        const used = new Set<number>();
        target?.conditions.forEach(id => {
          const key = `${target.id}-${id}`;
          const desired = this.anchorPoints.get(key)!;
          let best = -1, distance = Infinity;
          points.forEach((point, i) => {
            const d = point.distanceToSquared(desired);
            if (!used.has(i) && d < distance) { best = i; distance = d; }
          });
          if (best >= 0) { used.add(best); this.anchorPoints.set(key, points[best]); }
        });
      });
      this.scene.add(result.scene);
      this.detailReady = true;
      this.requestDraw();
    } catch {
      if (!this.disposed) { this.detailError = true; this.requestDraw(); }
    }
  }

  private resize = () => {
    this.width = this.canvas.parentElement!.clientWidth;
    this.height = this.canvas.parentElement!.clientHeight;
    if (!this.width || !this.height) return;
    this.renderer.setSize(this.width, this.height, false);
    const unit = Math.max(1, Math.min((this.height - 100) / 7.1, this.width / 5.7));
    this.camera.left = -this.width / 2 / unit;
    this.camera.right = this.width / 2 / unit;
    this.camera.top = this.height / 2 / unit;
    this.camera.bottom = -this.height / 2 / unit;
    this.camera.updateProjectionMatrix();
    this.requestDraw();
  };

  private requestDraw = () => { if (!this.disposed && !this.frame) this.frame = requestAnimationFrame(this.draw); };
  private cancelTween = () => { this.tween = null; };
  private contextLost = (event: Event) => { event.preventDefault(); this.onContextLost(); };
  private pointerDown = (event: PointerEvent) => { this.pointers.add(event.pointerId); this.pointerStart = { x: event.clientX, y: event.clientY }; this.pointerMoved = event.button !== 0 || this.pointers.size > 1; this.canvas.focus({ preventScroll: true }); };
  private pointerCancel = (event: PointerEvent) => { this.pointers.delete(event.pointerId); this.pointerMoved = true; };
  private pointerMove = (event: PointerEvent) => { if (Math.hypot(event.clientX - this.pointerStart.x, event.clientY - this.pointerStart.y) > 5) this.pointerMoved = true; };
  private pointerUp = (event: PointerEvent) => {
    if (!this.pointers.has(event.pointerId)) return;
    this.pointers.delete(event.pointerId);
    if (this.pointerMoved || event.button !== 0 || this.pointers.size > 0) return;
    const bounds = this.canvas.getBoundingClientRect();
    const ray = new THREE.Raycaster();
    ray.setFromCamera(new THREE.Vector2((event.clientX - bounds.left) / bounds.width * 2 - 1, -(event.clientY - bounds.top) / bounds.height * 2 + 1), this.camera);
    const skeletal = this.isSkeletalFocus();
    // Pick the visible graph nodes, so transparent bones do not prevent
    // selecting an organ through the spaces between their connections.
    if (this.detailReady && (this.camera.zoom > 1.7 || skeletal)) {
      ray.params.Points.threshold = (this.camera.top - this.camera.bottom) / this.camera.zoom / this.height * 7;
      const nodes = this.organNetworks.filter(network => network.dots.visible && network.dots.material.opacity > .2).map(network => network.dots);
      const node = ray.intersectObjects(nodes, false).sort((a, b) => (a.distanceToRay ?? 0) - (b.distanceToRay ?? 0))[0];
      if (node) { this.focusOrgan(node.object.name, node.point); return; }
    }
    const organs = this.detailReady ? this.organMeshes.filter(mesh => skeletal ? ['bone', 'dental'].includes(mesh.userData.category) : !['bone', 'dental', 'muscle', 'cornea'].includes(mesh.userData.category)) : [];
    const hit = ray.intersectObjects(this.camera.zoom > 1.7 || skeletal ? organs : [this.surface], false)[0];
    if (!hit) return;
    const organ = this.manifest.organs.find(o => o.id === hit.object.name);
    if (organ) this.focusOrgan(organ.id, hit.point);
    else {
      const nearest = [...this.targets].sort((a, b) => a.point.distanceToSquared(hit.point) - b.point.distanceToSquared(hit.point))[0];
      this.focus(nearest.id);
    }
  };

  private project(point: THREE.Vector3) {
    const p = this.projectVector.copy(point).project(this.camera);
    return { x: (p.x + 1) * this.width / 2, y: (1 - p.y) * this.height / 2 };
  }

  private silhouette() {
    const rows: SilhouetteRow[] = Array.from({ length: Math.ceil(this.height / SILHOUETTE_STEP) + 1 }, () => ({ left: Infinity, right: -Infinity }));
    // Use the full sampled surface, including its back, so transparent anatomy
    // still reserves its physical silhouette for the model rather than labels.
    this.nodePoints.forEach(point => {
      const p = this.project(point);
      const row = Math.floor(p.y / SILHOUETTE_STEP);
      for (let i = Math.max(0, row - 2); i <= Math.min(rows.length - 1, row + 2); i++) {
        rows[i].left = Math.min(rows[i].left, p.x);
        rows[i].right = Math.max(rows[i].right, p.x);
      }
    });
    return rows;
  }

  private draw = (now: number) => {
    this.frame = 0;
    if (this.disposed) return;
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (this.tween) {
      const t = Math.min(1, (now - this.tween.started) / (reducedMotion ? 1 : 550));
      const ease = 1 - Math.pow(1 - t, 3);
      const offset = this.camera.position.clone().sub(this.controls.target);
      this.controls.target.lerpVectors(this.tween.fromTarget, this.tween.toTarget, ease);
      this.camera.position.copy(this.controls.target).add(offset);
      if (this.tween.reset) this.camera.position.lerpVectors(this.tween.fromPosition, new THREE.Vector3(0, 0, 12), ease);
      this.camera.zoom = THREE.MathUtils.lerp(this.tween.fromZoom, this.tween.toZoom, ease);
      this.camera.updateProjectionMatrix();
      if (t === 1) this.tween = null;
      else this.requestDraw();
    }
    this.controls.update();
    this.camera.updateMatrixWorld();
    const zoom = this.camera.zoom;
    this.surfaceDetail.value = THREE.MathUtils.smoothstep(zoom, 1.35, 2.6);
    const reveal = this.detailReady ? THREE.MathUtils.smoothstep(zoom, 1.35, 2.6) : 0;
    const skeletal = this.isSkeletalFocus();
    this.lineMaterial.opacity = skeletal ? .06 : .58 * (1 - reveal) + .055 * reveal;
    this.dotMaterial.opacity = skeletal ? .08 : (1 - reveal) * .9 + .08 * reveal;
    this.dotMaterial.size = Math.min(2.4, 1.8 + zoom * .18);
    const secondary = THREE.MathUtils.smoothstep(zoom, 2.8, 5);
    this.organNetworks.forEach(({ id, category, spacing, lines, dots }) => {
      const bone = ['bone', 'dental'].includes(category);
      let opacity = bone ? reveal * .68 : category === 'muscle' ? secondary * .18 : category === 'cornea' ? reveal * .13 : reveal;
      if (skeletal) opacity = bone ? (this.focusedPart === 'skeleton' || this.focusedPart === id ? 1 : .4) : .008;
      else if (id === this.focusedPart) opacity = this.detailReady ? 1 : 0;
      const pixelSpacing = spacing * this.height / (this.camera.top - this.camera.bottom) * zoom;
      if (bone) opacity *= THREE.MathUtils.clamp(pixelSpacing / 5, .22, 1);
      lines.material.opacity = opacity * .54;
      dots.material.opacity = opacity * .85;
      dots.material.size = bone ? THREE.MathUtils.clamp(pixelSpacing * .25, .6, 2.3) : category === 'iris' ? 1.5 : Math.min(2.7, 1.5 + zoom * .13);
      lines.visible = dots.visible = opacity > .01;
    });
    this.renderer.render(this.scene, this.camera);

    const center = { x: this.width / 2, y: this.height / 2 };
    const distance = (target: Target) => { const p = this.project(target.point); return Math.hypot(p.x - center.x, p.y - center.y); };
    const closest = this.targets.filter(target => target.id !== 'skeleton').sort((a, b) => distance(a) - distance(b))[0];
    // Deadbands keep small movements around a boundary from alternating
    // whole sets of labels on consecutive frames.
    if (!this.labelRegion || distance(closest) + 40 < distance(this.labelRegion)) this.labelRegion = closest;
    const nearest = this.labelRegion;
    const regionCandidates = [
      ...this.targets.filter(target => target.id !== 'skeleton').map(target => ({ id: target.id, point: target.point })),
      ...this.manifest.organs.filter(organ => atlasRegions[organ.id]).map(organ => {
        const points = this.regionalPoints.get(organ.id) ?? [new THREE.Vector3().fromArray(organ.center)];
        const point = points.reduce((best, point) => point.distanceToSquared(this.controls.target) < best.distanceToSquared(this.controls.target) ? point : best);
        return { id: organ.id, point };
      }),
    ].filter(candidate => !skeletal || ['bone', 'dental'].includes(this.manifest.organs.find(organ => organ.id === candidate.id)?.category ?? ''));
    const regionDistance = (candidate: { id: string; point: THREE.Vector3 }) => candidate.point.distanceTo(this.controls.target) - (candidate.id === this.graphRegion ? .08 : 0);
    regionCandidates.sort((a, b) => regionDistance(a) - regionDistance(b));
    const focusStillHere = this.focusPoint && this.focusPoint.distanceTo(this.controls.target) < .12;
    const activeRegion = (focusStillHere || this.tween) && this.focusedPart && atlasRegions[this.focusedPart]
      ? { id: this.focusedPart, point: this.focusPoint ?? this.controls.target }
      : regionCandidates[0];
    this.graphRegion = zoom >= ATLAS_GRAPH_ZOOM ? activeRegion?.id ?? '' : '';
    this.detailLabels = this.detailLabels ? zoom > 1.58 : zoom >= 1.72;
    const candidates: LabelCandidate[] = [];
    const addLabel = (label: Omit<LabelCandidate, 'anchor'>, point: THREE.Vector3) => {
      this.labelPoints.set(label.id, point);
      candidates.push({ ...label, anchor: this.project(point) });
    };
    if (!this.detailLabels && !skeletal) {
      this.targets.filter(target => target.id !== 'muscles').forEach(target => addLabel({ id: target.id, title: target.title, kind: 'region', target: target.id }, target.point));
    } else {
      if (!skeletal && zoom < ATLAS_GRAPH_ZOOM) nearest.conditions.forEach(id => {
        const disease = diseases.find(d => d.id === id)!;
        const anchor = this.anchorPoints.get(`${nearest.id}-${id}`)!;
        addLabel({ id: `disease-${nearest.id}-${id}`, title: disease.name, kind: 'disease', diseaseId: id }, anchor);
      });
      const previousIds = new Set(this.labelPlacements.map(label => label.id));
      const organDistance = (screen: { x: number; y: number }, id: string) => Math.hypot(screen.x - center.x, screen.y - center.y) - (previousIds.has(`organ-${id}`) ? 32 : 0);
      this.manifest.organs.filter(organ => skeletal ? ['bone', 'dental'].includes(organ.category) : (['organ', 'eye', 'bone'].includes(organ.category) || organ.id === 'diaphragm') && organ.id !== 'lenses').map(organ => ({ organ, screen: this.project(new THREE.Vector3().fromArray(organ.anchor)) }))
        .filter(({ screen }) => screen.x >= 0 && screen.x <= this.width && screen.y >= 0 && screen.y <= this.height)
        .sort((a, b) => organDistance(a.screen, a.organ.id) - organDistance(b.screen, b.organ.id)).slice(0, skeletal ? 10 : 6)
        .forEach(({ organ }) => addLabel({ id: `organ-${organ.id}`, title: organ.name, kind: 'organ', target: organ.id }, new THREE.Vector3().fromArray(organ.anchor)));
    }
    this.lastRegion = this.graphRegion ? atlasRegions[this.graphRegion].label : this.focusedPart === 'skeleton' ? 'Skeleton' : !this.detailLabels ? '' : this.manifest.organs.find(organ => organ.id === this.focusedPart)?.name ?? nearest.title;
    this.labelPlacements = placeAtlasLabels(candidates, this.silhouette(), this.width, this.height, this.labelPlacements);
    const motion = this.labelMotion.update(this.labelPlacements, now, reducedMotion);
    // Even exiting labels stay attached to their anatomical point as the
    // camera moves. Text and leader lines share the same animated placement.
    const labels = motion.labels.map(label => ({ ...label, anchor: this.project(this.labelPoints.get(label.id)!) }));
    if (motion.animating) this.requestDraw();
    this.onFrame({ zoom, labels, detailReady: this.detailReady, detailError: this.detailError, region: this.lastRegion,
      regionId: this.graphRegion, regionAnchor: activeRegion ? this.project(activeRegion.point) : undefined, viewport: { width: this.width, height: this.height },
      rotated: Math.abs(this.controls.getAzimuthalAngle()) > .03 || Math.abs(this.controls.getPolarAngle() - Math.PI / 2) > .03 });
  };

  focus(id: string, notify = true) {
    if (id === 'body') { this.reset(notify); return; }
    this.focusedPart = id;
    if (id === 'skeleton') { this.animateTo(new THREE.Vector3(), 1.12); if (notify) this.onSelection(id, 'Skeleton'); return; }
    const target = this.targets.find(t => t.id === id);
    if (!target) { this.focusOrgan(id, undefined, notify); return; }
    this.animateTo(target.point, target.zoom);
    if (notify) this.onSelection(id, target.title);
  }

  private isSkeletalFocus() {
    return this.focusedPart === 'skeleton' || ['bone', 'dental'].includes(this.manifest.organs.find(organ => organ.id === this.focusedPart)?.category ?? '');
  }

  private focusOrgan(id: string, point?: THREE.Vector3, notify = true) {
    const organ = this.manifest.organs.find(item => item.id === id);
    if (!organ) return;
    this.focusedPart = id;
    const target = this.targets.find(item => item.id === id);
    const extent = Math.max(organ.bounds[1][0] - organ.bounds[0][0], organ.bounds[1][1] - organ.bounds[0][1]);
    const zoom = target?.zoom ?? Math.max(3.2, Math.min(12, 4 / extent));
    const pairedBones = ['hand-bones', 'foot-bones', 'arm-bones', 'leg-bones'].includes(id);
    this.animateTo(point ?? new THREE.Vector3().fromArray(pairedBones ? organ.anchor : organ.center), pairedBones ? 5 : zoom);
    if (notify) this.onSelection(id, organ.name);
  }

  private animateTo(target: THREE.Vector3, zoom: number, reset = false) {
    this.focusPoint = reset ? null : target.clone();
    this.tween = { started: performance.now(), fromTarget: this.controls.target.clone(), toTarget: target.clone(), fromZoom: this.camera.zoom, toZoom: zoom, reset, fromPosition: this.camera.position.clone() };
    this.requestDraw();
  }

  reset(notify = true) { this.focusedPart = null; this.animateTo(new THREE.Vector3(), 1, true); if (notify) this.onSelection('body', 'Whole body'); }
  zoomBy(factor: number) { this.cancelTween(); this.camera.zoom = THREE.MathUtils.clamp(this.camera.zoom * factor, 1, 14); this.camera.updateProjectionMatrix(); this.requestDraw(); }
  setPanMode(pan: boolean) { this.controls.mouseButtons.LEFT = pan ? THREE.MOUSE.PAN : THREE.MOUSE.ROTATE; this.controls.touches.ONE = pan ? THREE.TOUCH.PAN : THREE.TOUCH.ROTATE; }
  rotateBy(horizontal: number, vertical: number) {
    this.cancelTween();
    const offset = this.camera.position.clone().sub(this.controls.target);
    const spherical = new THREE.Spherical().setFromVector3(offset);
    spherical.theta += horizontal; spherical.phi = THREE.MathUtils.clamp(spherical.phi + vertical, .08, Math.PI - .08);
    this.camera.position.copy(this.controls.target).add(new THREE.Vector3().setFromSpherical(spherical));
    this.controls.update(); this.requestDraw();
  }

  private disposeObject(object: THREE.Object3D) {
    object.traverse(item => {
      if (item instanceof THREE.Mesh || item instanceof THREE.LineSegments || item instanceof THREE.Points) {
        item.geometry.dispose();
        (Array.isArray(item.material) ? item.material : [item.material]).forEach(material => material.dispose());
      }
    });
  }

  dispose() {
    this.disposed = true; cancelAnimationFrame(this.frame); this.observer.disconnect(); this.controls.dispose();
    this.canvas.removeEventListener('pointerdown', this.pointerDown);
    this.canvas.removeEventListener('pointermove', this.pointerMove);
    this.canvas.removeEventListener('pointerup', this.pointerUp);
    this.canvas.removeEventListener('pointercancel', this.pointerCancel);
    this.canvas.removeEventListener('webglcontextlost', this.contextLost);
    this.disposeObject(this.scene); this.pickingMaterial.dispose(); this.renderer.dispose();
  }
}
