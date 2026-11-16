import { Canvas, useFrame, useThree, type ThreeEvent } from "@react-three/fiber";
import {
  ContactShadows, Environment, GizmoHelper, GizmoViewport, Grid, Html, Lightformer, Line, OrbitControls, TransformControls,
} from "@react-three/drei";
import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import type { OrbitControls as OrbitImpl } from "three-stdlib";
import { api } from "../lib/api";
import { runOp, useStore } from "../lib/store";
import ViewportToolbar from "./ViewportToolbar";

const loader = new GLTFLoader();
const sanitize = (n: string) => THREE.PropertyBinding.sanitizeNodeName(n);

interface Loaded { root: THREE.Group; rev: number; projectId: string }

function easeOutBack(t: number) {
  const c1 = 1.70158, c3 = c1 + 1;
  return 1 + c3 * Math.pow(t - 1, 3) + c1 * Math.pow(t - 1, 2);
}

function useModel(): Loaded | null {
  const url = useStore((s) => s.snap?.model_url);
  const mode = useStore((s) => s.project?.mode);
  const pid = useStore((s) => s.project?.id);
  const rev = useStore((s) => s.snap?.rev ?? 0);
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  useEffect(() => {
    let cancelled = false;
    if (!url || !pid) { setLoaded(null); return; }
    api.arrayBuffer(url).then((buf) => new Promise<THREE.Group>((res, rej) => loader.parse(buf, "", (g) => res(g.scene), rej)))
      .then((scene) => {
        if (cancelled) return;
        const root = new THREE.Group();
        if (mode === "cad") { scene.rotation.x = -Math.PI / 2; scene.scale.setScalar(0.001); }
        root.add(scene);
        scene.traverse((o) => {
          const m = o as THREE.Mesh;
          if (m.isMesh) {
            m.castShadow = true; m.receiveShadow = true;
            m.userData.orig = m.material;
            const mat = m.material as THREE.MeshStandardMaterial;
            if (mat.transparent) { mat.depthWrite = false; m.castShadow = false; }
            if (mode === "cad") mat.side = THREE.DoubleSide;
          }
        });
        root.userData.cadRoot = mode === "cad" ? scene : null;
        root.updateMatrixWorld(true);
        root.userData.bbox = new THREE.Box3().setFromObject(root); // measured before spawn animations shrink parts
        (window as unknown as { __ai3dRoot?: THREE.Group }).__ai3dRoot = root; // debug/test hook
        setLoaded({ root, rev, projectId: pid });
      })
      .catch((e) => { if (!cancelled) useStore.getState().notify(`Viewport: ${e.message || e}`, "err"); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url, pid, mode]);
  return url ? loaded : null;
}

const wireCache = new WeakMap<THREE.Material, THREE.Material>();
const solidCache = new WeakMap<THREE.Material, THREE.Material>();
function applyShading(root: THREE.Object3D, shading: string) {
  root.traverse((o) => {
    const m = o as THREE.Mesh;
    if (!m.isMesh) return;
    const orig = m.userData.orig as THREE.MeshStandardMaterial;
    if (shading === "wireframe") {
      let w = wireCache.get(orig);
      if (!w) { w = new THREE.MeshBasicMaterial({ color: orig.map ? 0x9fb4d8 : orig.color, wireframe: true, transparent: true, opacity: 0.7 }); wireCache.set(orig, w); }
      m.material = w;
    } else if (shading === "solid") {
      let s = solidCache.get(orig);
      if (!s) {
        const c = orig.map ? new THREE.Color(0xc4c8ce) : orig.color.clone();
        s = new THREE.MeshStandardMaterial({ color: c, roughness: 0.62, metalness: 0.0, transparent: orig.transparent, opacity: orig.transparent ? 0.4 : 1, side: orig.side });
        solidCache.set(orig, s);
      }
      m.material = s;
    } else {
      m.material = orig;
    }
  });
}

function findNode(root: THREE.Object3D, name: string): THREE.Object3D | null {
  let hit: THREE.Object3D | null = null;
  const sn = sanitize(name);
  root.traverse((o) => { if (!hit && (o.name === sn || o.name === name)) hit = o; });
  return hit;
}

function Model({ loaded, measuring, onMeasurePoint }: { loaded: Loaded; measuring: boolean; onMeasurePoint: (p: THREE.Vector3) => void }) {
  const shading = useStore((s) => s.shading);
  const objects = useStore((s) => s.snap?.objects);
  const newNames = useStore((s) => s.newNames);
  const spawn = useRef<{ node: THREE.Object3D; scale: THREE.Vector3; t0: number }[]>([]);

  const nameMap = useMemo(() => {
    const m = new Map<string, string>();
    objects?.forEach((o) => m.set(sanitize(o.name), o.name));
    return m;
  }, [objects]);

  useEffect(() => { applyShading(loaded.root, shading); }, [loaded, shading]);

  useEffect(() => {
    spawn.current = [];
    if (!newNames.size) return;
    const now = performance.now();
    let i = 0;
    newNames.forEach((n) => {
      const obj = objects?.find((o) => o.name === n);
      if (obj?.parent && newNames.has(obj.parent)) return; // animate only the root of a new composite
      const node = findNode(loaded.root, n);
      if (node) { spawn.current.push({ node, scale: node.scale.clone(), t0: now + i * 40 }); node.scale.multiplyScalar(0.001); i++; }
    });
  }, [loaded, newNames, objects]);

  useFrame(() => {
    if (!spawn.current.length) return;
    const now = performance.now();
    spawn.current = spawn.current.filter(({ node, scale, t0 }) => {
      const t = Math.min(1, Math.max(0, (now - t0) / 520));
      node.scale.copy(scale).multiplyScalar(Math.max(0.001, easeOutBack(t)));
      return t < 1;
    });
  });

  const onClick = (e: ThreeEvent<MouseEvent>) => {
    e.stopPropagation();
    if (measuring) { onMeasurePoint(e.point.clone()); return; }
    if (e.delta > 4) return;
    let o: THREE.Object3D | null = e.object;
    let picked: string | null = null;
    const objs = useStore.getState().snap?.objects || [];
    // climb to the top-most known object (a whole wheel / character), Alt = deepest part
    while (o) {
      const real = nameMap.get(o.name);
      if (real) { picked = real; if (e.nativeEvent.altKey) break; }
      o = o.parent;
    }
    if (picked) {
      const info = objs.find((x) => x.name === picked);
      if (info?.locked) { useStore.getState().notify(`„${picked}” jest zablokowany`, "info"); return; }
      useStore.getState().select(picked);
    }
  };
  return <primitive object={loaded.root} onClick={onClick} />;
}

function Selection({ loaded }: { loaded: Loaded }) {
  const selected = useStore((s) => s.selected);
  const gizmo = useStore((s) => s.gizmo);
  const mode = useStore((s) => s.project?.mode);
  const objects = useStore((s) => s.snap?.objects);
  const node = useMemo(() => (selected ? findNode(loaded.root, selected) : null), [loaded, selected]);
  const helper = useMemo(() => {
    if (!node) return null;
    const h = new THREE.BoxHelper(node, 0x5b8cff);
    (h.material as THREE.LineBasicMaterial).depthTest = false;
    (h.material as THREE.LineBasicMaterial).transparent = true;
    h.renderOrder = 999;
    return h;
  }, [node]);
  useFrame(() => helper?.update());
  const start = useRef<{ p: THREE.Vector3; r: THREE.Euler; s: THREE.Vector3 } | null>(null);
  const info = objects?.find((o) => o.name === selected);
  const effectiveGizmo = mode === "cad" && gizmo !== "translate" ? "none" : gizmo;

  const commit = async () => {
    if (!node || !selected) return;
    if (mode === "cad") {
      const p = node.position.clone();
      node.position.set(0, 0, 0);
      if (p.lengthSq() < 1e-6) return;
      await runOp("transform", { target: selected, translate: [round(p.x), round(p.y), round(p.z)] }, `Przesunięcie ${selected}`).catch(() => undefined);
    } else {
      const e = new THREE.Euler().setFromQuaternion(node.quaternion, "ZYX");
      const deg = (r: number) => round((r * 180) / Math.PI);
      await runOp("transform", {
        target: selected, position: node.position.toArray().map(round), rotation: [deg(e.x), deg(e.y), deg(e.z)],
        scale: node.scale.toArray().map(round),
      }, `Transformacja ${selected}`).catch(() => undefined);
    }
  };
  return (
    <>
      {helper && <primitive object={helper} />}
      {node && effectiveGizmo !== "none" && !info?.locked && (
        <TransformControls object={node} mode={effectiveGizmo} size={0.8}
          onMouseDown={() => { start.current = { p: node.position.clone(), r: node.rotation.clone(), s: node.scale.clone() }; }}
          onMouseUp={commit} />
      )}
    </>
  );
}
const round = (v: number) => Math.round(v * 10000) / 10000;

function CameraRig({ loaded }: { loaded: Loaded | null }) {
  const { camera, controls } = useThree() as unknown as { camera: THREE.PerspectiveCamera; controls: OrbitImpl | null };
  const viewCmd = useStore((s) => s.viewCmd);
  const fittedFor = useRef<string>("");
  const fit = (view: string): boolean => {
    if (!loaded || !controls) return false;
    const box = (loaded.root.userData.bbox as THREE.Box3 | undefined)?.clone() || new THREE.Box3().setFromObject(loaded.root);
    if (box.isEmpty()) return false;
    const c = box.getCenter(new THREE.Vector3());
    const r = box.getSize(new THREE.Vector3()).length() / 2;
    const dist = (r / Math.sin((camera.fov * Math.PI) / 360)) * 1.05;
    const dirs: Record<string, [number, number, number]> = {
      iso: [1, 0.75, 1.15], front: [0, 0, 1], back: [0, 0, -1], left: [-1, 0, 0], right: [1, 0, 0], top: [0, 1, 0.0001], bottom: [0, -1, 0.0001],
    };
    const d = new THREE.Vector3(...(dirs[view] || dirs.iso)).normalize();
    camera.position.copy(c).addScaledVector(d, dist);
    camera.near = Math.max(0.001, dist / 500); camera.far = dist * 50; camera.updateProjectionMatrix();
    controls.target.copy(c);
    controls.update();
    return true;
  };
  useEffect(() => {
    if (loaded && fittedFor.current !== loaded.projectId && fit("iso")) fittedFor.current = loaded.projectId;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loaded, controls]);
  useEffect(() => { if (viewCmd) fit(viewCmd.view); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [viewCmd]);
  return null;
}

function Lighting({ shading, hdri }: { shading: string; hdri: string }) {
  const render = shading === "render";
  return (
    <>
      <hemisphereLight args={["#dfe8ff", "#2a2622", shading === "solid" ? 1.1 : 0.45]} />
      <directionalLight position={[5, 9, 6]} intensity={shading === "solid" ? 2.0 : 1.8} castShadow={render}
        shadow-mapSize={[2048, 2048]} shadow-bias={-0.0004} shadow-camera-left={-8} shadow-camera-right={8}
        shadow-camera-top={8} shadow-camera-bottom={-8} />
      <directionalLight position={[-6, 4, -5]} intensity={0.7} />
      {shading !== "solid" && shading !== "wireframe" && (hdri === "studio" ? (
        <Environment resolution={256} background={false}>
          <Lightformer form="rect" intensity={3} position={[0, 6, 0]} rotation-x={Math.PI / 2} scale={[10, 10, 1]} />
          <Lightformer form="rect" intensity={1.6} position={[-6, 2, 3]} rotation-y={Math.PI / 2} scale={[6, 3, 1]} />
          <Lightformer form="rect" intensity={1.2} position={[6, 2, -3]} rotation-y={-Math.PI / 2} scale={[6, 3, 1]} />
          <Lightformer form="ring" intensity={2} position={[3, 3, 6]} scale={2} color="#cfe0ff" />
          <Lightformer form="rect" intensity={0.6} position={[0, -2, 0]} rotation-x={-Math.PI / 2} scale={[10, 10, 1]} color="#444" />
        </Environment>
      ) : (
        <Environment preset={hdri as "city"} background={render} backgroundBlurriness={0.7} backgroundIntensity={0.35} />
      ))}
    </>
  );
}

function MeasureLayer({ pts, units }: { pts: THREE.Vector3[]; units: string }) {
  if (!pts.length) return null;
  const f = units === "mm" ? 1000 : 1;
  return (
    <group>
      {pts.map((p, i) => (
        <mesh key={i} position={p}><sphereGeometry args={[0.004 * (units === "mm" ? 1 : 10), 12, 12]} /><meshBasicMaterial color="#ffd166" depthTest={false} /></mesh>
      ))}
      {pts.length === 2 && (
        <>
          <Line points={[pts[0], pts[1]]} color="#ffd166" lineWidth={2} depthTest={false} />
          <Html position={pts[0].clone().lerp(pts[1], 0.5)} center>
            <div className="px-2 py-1 rounded bg-black/80 text-[#ffd166] font-mono text-xs whitespace-nowrap border border-[#ffd16655]">
              {(pts[0].distanceTo(pts[1]) * f).toFixed(units === "mm" ? 2 : 3)} {units}
            </div>
          </Html>
        </>
      )}
    </group>
  );
}

export default function Viewport() {
  const loaded = useModel();
  const shading = useStore((s) => s.shading);
  const hdri = useStore((s) => s.hdri);
  const mode = useStore((s) => s.project?.mode);
  const snap = useStore((s) => s.snap);
  const job = useStore((s) => s.job);
  const [measuring, setMeasuring] = useState(false);
  const [pts, setPts] = useState<THREE.Vector3[]>([]);
  const [showGrid, setShowGrid] = useState(true);
  const tris = snap?.objects.reduce((a, o) => a + (o.triangles || 0), 0) || 0;
  const isCad = mode === "cad";
  const lastStep = [...useStore((s) => s.live)].reverse().find((e) => e.type === "step_start");

  return (
    <div className="relative w-full h-full bg-[radial-gradient(ellipse_at_50%_35%,#1d2127_0%,#121418_60%,#0e1013_100%)]">
      <Canvas shadows dpr={[1, 2]} gl={{ antialias: true, preserveDrawingBuffer: true, toneMapping: THREE.ACESFilmicToneMapping, toneMappingExposure: shading === "render" ? 1.05 : 1 }}
        camera={{ position: [4, 3, 5], fov: 38, near: 0.01, far: 500 }} onPointerMissed={(e) => { if (e.type === "click" && !measuring) useStore.getState().select(null); }}>
        <Suspense fallback={null}>
          <Lighting shading={shading} hdri={hdri} />
        </Suspense>
        {loaded && <Model loaded={loaded} measuring={measuring} onMeasurePoint={(p) => setPts((a) => (a.length >= 2 ? [p] : [...a, p]))} />}
        {loaded && <Selection loaded={loaded} />}
        {showGrid && (
          <Grid position={[0, -0.0005, 0]} infiniteGrid fadeDistance={isCad ? 3 : 60} fadeStrength={3}
            cellSize={isCad ? 0.01 : 0.5} sectionSize={isCad ? 0.1 : 5} cellColor="#262b33" sectionColor="#39414c" cellThickness={0.6} sectionThickness={1} />
        )}
        {shading === "render" && <ContactShadows position={[0, 0.0005, 0]} opacity={0.65} scale={isCad ? 1 : 14} blur={2.4} far={isCad ? 0.3 : 4} resolution={1024} />}
        <MeasureLayer pts={pts} units={snap?.units || "m"} />
        <OrbitControls makeDefault enableDamping dampingFactor={0.12} />
        <CameraRig loaded={loaded} />
        <GizmoHelper alignment="bottom-right" margin={[64, 64]}>
          <GizmoViewport axisColors={["#e5534b", "#3fb67a", "#5b8cff"]} labelColor="#0d0f12" />
        </GizmoHelper>
      </Canvas>

      <ViewportToolbar measuring={measuring} toggleMeasure={() => { setMeasuring((m) => !m); setPts([]); }} showGrid={showGrid} toggleGrid={() => setShowGrid((g) => !g)} />

      <div className="pointer-events-none absolute left-3 bottom-3 flex flex-col gap-1 font-mono text-xxs text-mute">
        <span>{isCad ? "CAD · B-Rep (OpenCascade) · mm · Z-up" : "GAME · PBR mesh · m · Y-up"}</span>
        <span>{snap?.objects.length || 0} obiektów · {tris.toLocaleString("pl-PL")} trójkątów · rev {snap?.rev ?? 0}</span>
        {measuring && <span className="text-[#ffd166]">Pomiar: kliknij dwa punkty na modelu</span>}
      </div>

      {!snap?.objects.length && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
          <div className="text-center max-w-sm">
            <div className="text-dim text-sm">Pusta scena</div>
            <div className="text-mute mt-1">Opisz model w panelu AI po prawej — zobaczysz, jak powstaje krok po kroku.</div>
          </div>
        </div>
      )}
      {job?.status === "running" && (
        <div className="absolute left-1/2 -translate-x-1/2 top-12 px-3 py-1.5 rounded-full bg-black/60 border border-line2 backdrop-blur text-xs flex items-center gap-2 slide-in">
          <span className="w-1.5 h-1.5 rounded-full bg-accent pulse-dot" />
          <span className="text-dim">{job.stage}</span>
          {lastStep?.label && <span className="text-fg">· {lastStep.label}</span>}
        </div>
      )}
    </div>
  );
}
