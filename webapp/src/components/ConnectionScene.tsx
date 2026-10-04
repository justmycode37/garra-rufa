'use client';

import { useEffect, useRef, useState, type RefObject } from 'react';
import * as THREE from 'three';
import { createBodyMotion, type HumanRig } from '@/lib/body-motion';
import { createReachingHandMotion, isReachingHandGraph, type ReachingHandGraph } from '@/lib/reaching-hand-motion';
import { createConnectionGraph, type ConnectionGraph } from '@/lib/connection-graph';
import { createHandAttachment, createHandPairLayout, FIGURE_ORIENTATION, HAND_SCALE } from '@/lib/hand-layout';
import { connectionTiming, advanceConnection, refinementFromZoom, CONNECTION_DURATION as DURATION } from '@/lib/connection-timing';
import { createAnimationLoop } from '@/lib/animation-loop';
import type { ConnectionDestination, SceneControl } from './ConnectionExperience';

type HumanGraph = ConnectionGraph & { rig: HumanRig };
type Props = {
  control: RefObject<SceneControl | null>;
  route: RefObject<string>;
  onReady: (ready: boolean) => void;
  onUnavailable: () => void;
  onNavigate: (destination: ConnectionDestination) => void;
  onArrive: (destination: ConnectionDestination) => void;
  onComplete: (destination: ConnectionDestination) => void;
};
const smooth = THREE.MathUtils.smoothstep;
const mix = THREE.MathUtils.lerp;

let assets: Promise<[HumanGraph, ReachingHandGraph]> | undefined;
function loadAssets(): Promise<[HumanGraph, ReachingHandGraph]> {
  return assets ??= Promise.all([
    fetch('/models/human-exploration.json?v=shoulder-socket-2', { cache: 'no-store' }).then(r => { if (!r.ok) throw new Error('Body unavailable'); return r.json(); }),
    fetch('/models/reaching-hands.json?v=connected-upper-arm-5', { cache: 'no-store' }).then(r => {
      if (!r.ok) throw new Error('Hands unavailable');
      return r.json();
    }).then(value => {
      if (!isReachingHandGraph(value)) throw new Error('Hand geometry version mismatch');
      return value;
    }),
  ]).catch(error => { assets = undefined; throw error; });
}

export default function ConnectionScene(props: Props) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const callbacks = useRef(props);
  callbacks.current = props;
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let disposed = false, cleanup: (() => void) | undefined;
    loadAssets().then(([human, hand]) => {
      if (disposed || !canvas.current) return;
      const element = canvas.current;
      let renderer: THREE.WebGLRenderer;
      try { renderer = new THREE.WebGLRenderer({ canvas: element, alpha: true, antialias: true, powerPreference: 'low-power' }); }
      catch { setFailed(true); callbacks.current.onUnavailable(); return; }
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
      renderer.setClearColor(0, 0);
      renderer.localClippingEnabled = true;
      const scene = new THREE.Scene(), figure = new THREE.Group();
      const camera = new THREE.OrthographicCamera(-6, 6, 4, -4, .01, 100);
      camera.position.z = 20;
      scene.add(figure);
      const { rig } = human;
      const handAttachment = createHandAttachment(rig);
      const restAttachment = handAttachment.matrix;
      figure.rotation.copy(FIGURE_ORIENTATION);
      // Partition the original graph without changing any landing geometry.
      // The detailed mission hand only blends in after About is clicked.
      const armVertices = new Set(hand.body.vertexIds);
      const hiddenVertices = human.positions.filter((_, i) => i % 3 === 0).map((_, i) => armVertices.has(i));
      const hiddenNodes = hand.body.nodeMask;
      const vertexMap = new Int32Array(human.positions.length / 3).fill(-1);
      const nodeMap = new Int32Array(human.nodes.length / 3).fill(-1);
      hand.body.vertexIds.forEach((id, i) => { vertexMap[id] = i; });
      hand.body.originalNodeIds.forEach((id, i) => { nodeMap[id] = i; });
      const restBodyNodes = human.spacing.map((_, i) => i).filter(i => rig.nodeWeights[i * rig.boneCount + 4] < .1);
      const body = createConnectionGraph({ ...human,
        normalTriangles: human.triangles,
        triangles: human.triangles.filter((_, i) => {
          const first = i - i % 3;
          return !(hiddenVertices[human.triangles[first]] && hiddenVertices[human.triangles[first + 1]] && hiddenVertices[human.triangles[first + 2]]);
        }),
        nodeVisibility: hiddenNodes.map(hidden => !hidden),
        edges: human.edges.filter((_, i) => !hiddenNodes[human.edges[i - i % 2]] && !hiddenNodes[human.edges[i - i % 2 + 1]]),
      });
      const originalHand = createConnectionGraph({ ...human,
        normalTriangles: human.triangles,
        triangles: human.triangles.filter((_, i) => {
          const first = i - i % 3;
          return hiddenVertices[human.triangles[first]] && hiddenVertices[human.triangles[first + 1]] && hiddenVertices[human.triangles[first + 2]];
        }),
        nodeVisibility: hiddenNodes,
        edges: human.edges.filter((_, i) => hiddenNodes[human.edges[i - i % 2]] || hiddenNodes[human.edges[i - i % 2 + 1]]),
      });
      const right = createConnectionGraph(hand, true, HAND_SCALE), left = createConnectionGraph(hand, true);
      figure.add(body.group, originalHand.group, right.group); scene.add(left.group);
      left.group.matrixAutoUpdate = false;
      const bodyMotion = createBodyMotion(rig);
      const rightMotion = createReachingHandMotion(hand.rig, 1);
      const leftMotion = createReachingHandMotion(hand.rig, 0);
      const rigidWeights = Array.from({ length: rig.boneCount }, (_, i) => i >= 4 && i < 8 ? 1 : 0);
      const attachment = new THREE.Matrix4(), carrierWorld = new THREE.Matrix4();
      const origin = new THREE.Vector3(), bx = new THREE.Vector3(), by = new THREE.Vector3(), bz = new THREE.Vector3();
      const sourcePoint = new THREE.Vector3(), sourceNormal = new THREE.Vector3();
      const projected = new THREE.Vector3(), originalTarget = new THREE.Vector3(), originalNormal = new THREE.Vector3();
      const handFocus = new THREE.Vector3();
      const pair = createHandPairLayout(hand.rig);
      const media = window.matchMedia('(prefers-reduced-motion: reduce)');
      let time = 0, frozenTime = 0, handTime = 0;
      let progress = callbacks.current.route.current === '/about' ? 1 : 0;
      let active = false, direction: 1 | -1 = 1, navigated = false, arrived = false, previousRoute = callbacks.current.route.current;
      let returnHandTime = 0, framed = false;
      let width = 0, height = 0, startHalfHeight = 4, startCameraY = 0;
      let contextLost = false;

      function aboutRect() {
        const anchor = document.querySelector<HTMLElement>('[data-connection-anchor="about"]');
        if (anchor) return anchor.getBoundingClientRect();
        const mobile = width <= 720, artWidth = mobile ? width * 1.6 : Math.min(1280, width);
        const artHeight = mobile ? 230 : Math.max(285, Math.min(420, width * .31));
        return { width: artWidth, height: artHeight, top: mobile ? 84 : 105, left: (width - artWidth) / 2 };
      }

      function render(elapsedMs: number) {
        if (disposed || contextLost || document.hidden) return false;
        const path = callbacks.current.route.current;
        const anchor = document.querySelector<HTMLElement>(`[data-connection-anchor="${path === '/about' ? 'about' : 'body'}"]`);
        if (!anchor && !active) { element.style.visibility = 'hidden'; return false; }
        element.style.visibility = 'visible';
        const delta = elapsedMs / 1000;
        const nextWidth = window.innerWidth, nextHeight = window.innerHeight;
        if (width !== nextWidth || height !== nextHeight) {
          width = nextWidth; height = nextHeight; renderer.setSize(width, height, false);
          framed = false;
        }
        const destination: ConnectionDestination = direction === 1 ? 'about' : 'body';
        const destinationReady = direction === 1 ? path === '/about' : path === '/';
        if (path !== previousRoute) {
          const expectedRoute = direction === 1 ? '/about' : '/';
          if (!active || path !== expectedRoute) {
            progress = path === '/about' ? 1 : 0; active = false;
          }
          previousRoute = path;
        }
        if (active) {
          progress = advanceConnection(progress, delta * 1000, direction, destinationReady);
          if (media.matches) progress = direction === 1 ? 1 : 0;
          if ((direction === 1 ? progress >= .52 : progress <= .50) && !navigated) {
            navigated = true; callbacks.current.onNavigate(destination);
          }
          if ((direction === 1 ? progress >= .82 : progress <= .16) && destinationReady && !arrived) {
            arrived = true; callbacks.current.onArrive(destination);
          }
          // The partner's idle motion eases back onto the shared timeline.
          handTime = direction === 1 ? progress * DURATION / 1000
            : mix(progress * DURATION / 1000, returnHandTime, smooth(progress, .82, 1));
          if ((direction === 1 ? progress === 1 : progress === 0) && destinationReady) {
            active = false;
            if (progress === 0) time = frozenTime;
            callbacks.current.onComplete(destination);
          }
        } else if (!media.matches) {
          if (progress === 0) time += delta;
          else handTime += delta;
        }
        const { lift, zoom, focus, shape: poseShape, entrance } = connectionTiming(progress);
        bodyMotion.update(progress > 0 ? frozenTime : time, media.matches, lift);
        const bodyAnchor = document.querySelector<HTMLElement>('[data-connection-anchor="body"]');
        if ((progress === 0 && bodyAnchor) || !framed) {
          // A direct About visit still has a valid reverse destination.
          const mobile = width <= 720;
          const rect = bodyAnchor?.getBoundingClientRect() ?? {
            width: width + (mobile ? 60 : 0), height: Math.max(height, mobile ? 520 : 540) - (mobile ? 225 : 66),
            top: mobile ? 185 : 38,
          };
          const half = Math.max(3.67, 1.8 / (rect.width / rect.height));
          startHalfHeight = half * height / rect.height;
          startCameraY = ((rect.top + rect.height / 2) / height - .5) * startHalfHeight * 2;
          figure.position.set(width <= 720 ? 0 : half * rect.width / rect.height * .28, Math.sin(time * .32) * .065, 0);
          framed = true;
        }
        rightMotion.update(0, true);
        leftMotion.update(handTime, media.matches, 1, entrance);
        const attached = (p: THREE.Vector3) => bodyMotion.deform(p.applyMatrix4(restAttachment), rigidWeights, 0);
        attached(origin.set(0, 0, 0));
        attached(bx.set(1, 0, 0)).sub(origin); attached(by.set(0, 1, 0)).sub(origin); attached(bz.set(0, 0, 1)).sub(origin);
        attachment.makeBasis(bx, by, bz).setPosition(origin);
        // The body keeps its orientation and position throughout the shot.
        // Only its anatomical right shoulder lifts; the camera pans and zooms.
        figure.updateMatrixWorld(true);
        carrierWorld.multiplyMatrices(figure.matrixWorld, attachment);
        pair.update(carrierWorld, rightMotion, leftMotion, entrance, media.matches ? 0 : handTime);
        const art = aboutRect();
        const endHalf = 4.35 * HAND_SCALE * height / art.width;
        const halfHeight = startHalfHeight * Math.pow(endHalf / startHalfHeight, zoom);
        const framingY = ((art.top + art.height / 2) / height - .5) * halfHeight * 2;
        camera.left = -halfHeight * width / height; camera.right = -camera.left;
        camera.top = halfHeight; camera.bottom = -halfHeight;
        // Establish the original hand first, before the shoulder starts its
        // main lift. Widen the focus to the fingertip gap as its partner enters.
        handFocus.copy(origin).applyMatrix4(figure.matrixWorld).lerp(pair.carrierTip, .65).lerp(pair.center, entrance);
        camera.position.x = mix(0, handFocus.x, focus);
        camera.position.y = mix(startCameraY, handFocus.y, focus) + framingY * focus;
        camera.updateProjectionMatrix(); camera.updateMatrixWorld();

        // Detail grows from the original nodes during the fast opening zoom.
        // Waiting for the entire torso to leave delayed detail until the lift.
        const refinement = refinementFromZoom(zoom);
        let bodyVisible = false;
        for (const i of restBodyNodes) {
          projected.fromArray(human.nodes, i * 3);
          bodyMotion.deform(projected, rig.nodeWeights, i * rig.boneCount);
          projected.applyMatrix4(figure.matrixWorld);
          projected.project(camera);
          if (Math.abs(projected.x) < 1.04 && Math.abs(projected.y) < 1.04) { bodyVisible = true; break; }
        }
        // The new sampling uses the original arm's exact source vertices.
        // Its root stays attached while the wrist settles into the reaching pose.
        body.setMarkScale(mix(1, .30, zoom));
        body.update((point, i, surface) => bodyMotion.deform(point, surface ? rig.surfaceWeights : rig.nodeWeights, i * rig.boneCount));
        originalHand.group.visible = refinement < 1;
        originalHand.setSurfaceVisible(refinement < .55);
        originalHand.setOpacity(1 - smooth(refinement, .72, 1));
        originalHand.setMarkScale(mix(1, .30, zoom));
        if (originalHand.group.visible) originalHand.update((point, i, surface) => {
          const index = (surface ? vertexMap : nodeMap)[i];
          if (index >= 0 && poseShape > 0) {
            originalTarget.fromArray(surface ? hand.positions : hand.body.originalNodes, index * 3);
            const shape = poseShape * smooth(originalTarget.x, -2.9, -.45);
            originalNormal.fromArray(surface ? hand.surfaceNormals : hand.body.originalNodeNormals, index * 3);
            originalTarget.addScaledVector(originalNormal, surface ? -.045 : .012);
            rightMotion.deform(originalTarget, surface ? hand.rig.surfaceWeights : hand.body.originalNodeWeights, index * hand.rig.boneCount);
            originalTarget.applyMatrix4(restAttachment);
            point.lerp(originalTarget, shape);
          }
          bodyMotion.deform(point, surface ? rig.surfaceWeights : rig.nodeWeights, i * rig.boneCount);
        });
        right.group.visible = refinement > 0;
        right.setSurfaceVisible(refinement >= .55);
        right.setOpacity(1);
        right.setGrowth(refinement);
        right.setMarkScale(HAND_SCALE);
        if (right.group.visible) right.update((point, i, surface) => {
          const shape = poseShape * smooth(point.x, -2.9, -.45);
          rightMotion.deform(point, surface ? hand.rig.surfaceWeights : hand.rig.nodeWeights, i * hand.rig.boneCount);
          point.applyMatrix4(restAttachment);
          sourcePoint.fromArray(surface ? hand.body.positions : hand.body.nodes, i * 3);
          sourceNormal.fromArray(surface ? hand.body.surfaceNormals : hand.body.nodeNormals, i * 3);
          sourcePoint.addScaledVector(sourceNormal, surface ? -.004 : .004);
          point.lerp(sourcePoint, 1 - shape);
          bodyMotion.deform(point, surface ? hand.body.surfaceWeights : hand.body.nodeWeights, i * rig.boneCount);
        }, (point, i) => { point.copy(originalHand.nodePosition(hand.body.growthNodeIds[i])); });
        left.group.visible = entrance > 0;
        if (left.group.visible) {
          left.group.matrix.copy(pair.partnerMatrix);
          // Start beyond the current viewport even during the wider, slower
          // opening of the zoom. The second arm enters instead of appearing.
          const offscreenTravel = pair.carrierTip.x - camera.position.x + camera.right + .25;
          left.group.matrix.elements[12] -= (1 - entrance) * Math.max(0, offscreenTravel - 4.5 * HAND_SCALE);
          left.update((point, i, surface) => leftMotion.deform(point, surface ? hand.rig.surfaceWeights : hand.rig.nodeWeights, i * hand.rig.boneCount));
        }
        // The anchor positions the art without clipping it. Both complete arm
        // silhouettes scroll naturally across the full viewport canvas.
        renderer.clear();
        renderer.render(scene, camera);
        element.dataset.progress = progress.toFixed(3);
        element.dataset.refinement = refinement.toFixed(3);
        element.dataset.bodyVisible = String(bodyVisible);
        element.dataset.zoom = zoom.toFixed(3);
        element.dataset.direction = direction === 1 ? 'forward' : 'reverse';
        element.dataset.scene = active ? 'transition' : progress === 1 ? 'about' : 'body';
        element.dataset.motionTime = (progress > 0 ? handTime : time).toFixed(3);
        return !media.matches || active;
      }

      const loop = createAnimationLoop(render);
      const requestRender = () => loop.request();
      const visibilityChanged = () => { if (document.hidden) loop.pause(); else loop.resume(); };
      const cancelTransition = () => {
        if (!active) return;
        active = false; progress = callbacks.current.route.current === '/about' ? 1 : 0;
        callbacks.current.onComplete(progress === 1 ? 'about' : 'body'); requestRender();
      };
      const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') cancelTransition(); };
      callbacks.current.control.current = { enterAbout: () => {
        if (active) return true;
        if (progress !== 0 || !document.querySelector('[data-connection-anchor="body"]')) return false;
        active = true; direction = 1; navigated = false; arrived = false; frozenTime = time; handTime = 0;
        requestRender(); return true;
      }, enterExplore: () => {
        if (active) return true;
        if (progress !== 1) return false;
        active = true; direction = -1; navigated = false; arrived = false; returnHandTime = handTime;
        requestRender(); return true;
      } };
      const lost = (event: Event) => { event.preventDefault(); contextLost = true; active = false; loop.pause(); setFailed(true); callbacks.current.onUnavailable(); };
      const restored = () => { contextLost = false; setFailed(false); callbacks.current.onReady(true); visibilityChanged(); };
      const observer = new MutationObserver(requestRender);
      observer.observe(document.querySelector('.connection-experience')!, { childList: true, subtree: true });
      window.addEventListener('resize', requestRender); window.addEventListener('scroll', requestRender, { passive: true });
      document.addEventListener('visibilitychange', visibilityChanged); media.addEventListener('change', requestRender);
      window.addEventListener('popstate', cancelTransition); document.addEventListener('keydown', escape);
      element.addEventListener('webglcontextlost', lost); element.addEventListener('webglcontextrestored', restored);
      callbacks.current.onReady(true); visibilityChanged();
      cleanup = () => {
        loop.dispose(); observer.disconnect(); callbacks.current.control.current = null;
        window.removeEventListener('resize', requestRender); window.removeEventListener('scroll', requestRender);
        document.removeEventListener('visibilitychange', visibilityChanged); media.removeEventListener('change', requestRender);
        window.removeEventListener('popstate', cancelTransition); document.removeEventListener('keydown', escape);
        element.removeEventListener('webglcontextlost', lost); element.removeEventListener('webglcontextrestored', restored);
        body.dispose(); originalHand.dispose(); right.dispose(); left.dispose(); renderer.dispose();
      };
    }).catch(() => { if (!disposed) { setFailed(true); callbacks.current.onUnavailable(); } });
    return () => { disposed = true; cleanup?.(); };
  }, []);

  return <canvas ref={canvas} className="connection-canvas" aria-hidden="true" style={failed ? { display: 'none' } : undefined}/>;
}
