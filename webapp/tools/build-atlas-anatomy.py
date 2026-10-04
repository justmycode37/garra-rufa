"""Prepare the atlas from the official BodyParts3D 4.0 OBJ archive.

Usage: python3 tools/build-atlas-anatomy.py archive.zip [--skin-only] [--lung-dir directory]
Requires numpy, scipy and fast-simplification 0.2.0. No downloaded code runs.
Source, attribution and current license: public/models/anatomy/NOTICE.md.
"""
import json
import re
import struct
import sys
from pathlib import Path
from zipfile import ZipFile

import fast_simplification
import numpy as np
from atlas_mesh import normals, orient_faces, surface_graph
from scipy.spatial import cKDTree

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'public/models/anatomy'
OUT.mkdir(parents=True, exist_ok=True)
archive = ZipFile(sys.argv[1])
catalog = []
for path in archive.namelist():
    if not path.endswith('.obj'):
        continue
    header = archive.read(path)[:1600].decode(errors='replace')
    name = re.search(r'# English name : (.+)', header)
    concept = re.search(r'# Concept ID : (.+)', header)
    if name and concept:
        catalog.append(dict(path=path, name=name[1].strip(), fma=concept[1].strip()))

# The 4.0 archive contains the bronchial trees but omits lung-lobe envelopes.
# The 3.0 lobe meshes retain the same millimeter reference coordinates.
if '--lung-dir' in sys.argv:
    lung_dir = Path(sys.argv[sys.argv.index('--lung-dir') + 1])
    for fma, name in [('FMA7333', 'Upper lobe of right lung'), ('FMA7337', 'Lower lobe of right lung'), ('FMA7383', 'Middle lobe of right lung'), ('FMA7370', 'Upper lobe of left lung'), ('FMA7371', 'Lower lobe of left lung')]:
        catalog.append({'path': str(lung_dir / (fma + '.stl')), 'name': name, 'fma': fma, 'stl': True})


def read_mesh(item):
    if item.get('stl'):
        data = Path(item['path']).read_bytes()
        dtype = np.dtype([('normal', '<f4', (3,)), ('vertices', '<f4', (3, 3)), ('attribute', '<u2')])
        points = np.frombuffer(data[84:], dtype=dtype)['vertices'].reshape(-1, 3)
        vertices, inverse = np.unique(points, axis=0, return_inverse=True)
        return vertices.astype(np.float64), inverse.reshape(-1, 3).astype(np.int32)
    vertices, triangles = [], []
    for line in archive.read(item['path']).decode().splitlines():
        if line.startswith('v '):
            vertices.append([float(x) for x in line.split()[1:4]])
        elif line.startswith('f '):
            face = [int(x.split('/')[0]) - 1 for x in line.split()[1:]]
            triangles.extend([face[0], face[i], face[i + 1]] for i in range(1, len(face) - 1))
    return np.asarray(vertices, dtype=np.float64), np.asarray(triangles, dtype=np.int32)


skin_item = next(item for item in catalog if item['fma'] == 'FMA7163')
skin_v, skin_f = read_mesh(skin_item)
source_min, source_max = skin_v.min(0), skin_v.max(0)
scale = 7 / (source_max[2] - source_min[2])
mid = (source_min + source_max) / 2


def normalize(vertices):
    # One shared source-to-world transform preserves all anatomical registration.
    return np.column_stack(((vertices[:, 0] - mid[0]) * scale,
                            (vertices[:, 2] - mid[2]) * scale,
                            -(vertices[:, 1] - mid[1]) * scale))



if '--skin-only' in sys.argv or not (OUT / 'surface.json').exists():
    v = normalize(skin_v)
    v, faces = fast_simplification.simplify(v, skin_f, target_count=45000)
    faces = orient_faces(v, faces)
    n = normals(v, faces)
    area = np.linalg.norm(np.cross(v[faces[:, 1]] - v[faces[:, 0]], v[faces[:, 2]] - v[faces[:, 0]]), axis=1)
    rng = np.random.default_rng(5718)
    ids = rng.choice(len(faces), 110000, p=area / area.sum())
    bary = rng.random((len(ids), 2))
    bary[bary.sum(1) > 1] = 1 - bary[bary.sum(1) > 1]
    bary = np.column_stack((bary, 1 - bary.sum(1)))
    points = (v[faces[ids]] * bary[:, :, None]).sum(1)
    pn = (n[faces[ids]] * bary[:, :, None]).sum(1)
    pn /= np.maximum(np.linalg.norm(pn, axis=1, keepdims=True), 1e-12)
    detail = (points[:, 1] > 2.65) | (points[:, 1] < -2.95) | ((np.abs(points[:, 0]) > .88) & (points[:, 1] < .4))
    spacing = np.where(detail, .041, .085)
    tree = cKDTree(points)
    available = np.ones(len(points), dtype=bool)
    selected = []
    for i in rng.permutation(len(points)):
        if not available[i]:
            continue
        selected.append(i)
        near = np.array(tree.query_ball_point(points[i], .085))
        close = np.linalg.norm(points[near] - points[i], axis=1) < (spacing[i] + spacing[near]) * .5
        available[near[close]] = False
    points, pn, spacing = points[selected], pn[selected], spacing[selected]
    tree = cKDTree(points)
    edges = set()
    for i in range(len(points)):
        options = []
        for j in tree.query_ball_point(points[i], .17):
            if i == j:
                continue
            delta = points[j] - points[i]
            length = np.linalg.norm(delta)
            relative = length / ((spacing[i] + spacing[j]) * .5)
            if relative > 1.85 or np.dot(pn[i], pn[j]) < .2:
                continue
            if max(abs(np.dot(delta / length, pn[i])), abs(np.dot(delta / length, pn[j]))) > .7:
                continue
            options.append((abs(relative - 1.2), j))
        for _, j in sorted(options)[:4]:
            edges.add(tuple(sorted((i, j))))
    graph = {'positions': np.round(v, 5).ravel().tolist(), 'triangles': faces.ravel().tolist(),
             'nodes': np.round(points, 5).ravel().tolist(), 'normals': np.round(pn, 5).ravel().tolist(),
             'spacing': spacing.tolist(), 'edges': np.array(sorted(edges)).ravel().tolist()}
    (OUT / 'surface.json').write_text(json.dumps(graph, separators=(',', ':')))
    print('Surface:', len(v), 'vertices;', len(points), 'graph nodes', flush=True)
if '--skin-only' in sys.argv:
    sys.exit(0)

# Select actual mesh surfaces, not inferred parent lists (some historical
# part-of rows include vessels without their enclosing organ surface).
groups = [
    ('brain', 'Brain', r'gyrus|cerebellum|occipital lobe|parietal lobule|white matter of .* cerebral hemisphere|^pons$|^medulla oblongata$', 85000, 'organ'),
    ('heart', 'Heart', r'^wall of ((left|right) )?(atrium|ventricle)$|^myocardium|^interventricular septum', 35000, 'organ'),
    ('lungs', 'Lungs', r'bronchopulmonary segment|^((left|right) )?(upper|middle|lower) lobe of (left |right )?lung$', 50000, 'organ'),
    ('liver', 'Liver', r'^hepatovenous segment|^caudate lobe of liver$', 28000, 'organ'),
    ('stomach', 'Stomach', r'^stomach$', 15000, 'organ'),
    ('kidneys', 'Kidneys', r'^(left|right) kidney$', 22000, 'organ'),
    ('intestines', 'Intestines', r'(part of (ileum|jejunum)$)|^(ascending|descending|transverse|sigmoid) colon$|^duodenum$|^cecum$', 50000, 'organ'),
    ('pancreas', 'Pancreas', r'^parenchyma of pancreas$', 10000, 'organ'),
    ('spleen', 'Spleen', r'^spleen$', 8000, 'organ'),
    ('gallbladder', 'Gallbladder', r'^gallbladder$', 5000, 'organ'),
    ('bladder', 'Urinary bladder', r'^urinary bladder$', 9000, 'organ'),
    ('ureters', 'Ureters', r'^(left|right) ureter$', 9000, 'organ'),
    ('adrenals', 'Adrenal glands', r'^(left|right) adrenal gland$', 9000, 'organ'),
    ('pituitary', 'Pituitary gland', r'^pituitary gland$', 6000, 'organ'),
    ('esophagus', 'Esophagus', r'^esophagus$', 10000, 'organ'),
    ('salivary-glands', 'Submandibular glands', r'^(left|right) submandibular gland$', 8000, 'organ'),
    ('rectum', 'Rectum', r'^rectum$', 9000, 'organ'),
    ('tongue', 'Tongue', r'^tongue$', 12000, 'organ'),
    ('diaphragm', 'Diaphragm', r'^diaphragm$', 18000, 'muscle'),
    ('eyes', 'Eyes', r'^(left|right) sclera$', 22000, 'eye'),
    ('iris', 'Iris', r'^(left|right) iris$', 8000, 'iris'),
    ('cornea', 'Cornea', r'^(left|right) cornea$', 10000, 'cornea'),
    ('lenses', 'Lens', r'^(left|right) lens$', 6000, 'eye'),
    ('optic-nerves', 'Optic nerves', r'^(left|right) optic nerve$|^optic chiasm$', 8000, 'nerve'),
    ('airways', 'Airways', r'^trachea$|^(left|right) main bronchus$', 14000, 'vessel'),
    ('aorta', 'Aorta', r'^ascending aorta$|^arch of aorta$|^thoracic aorta$|^abdominal aorta$', 18000, 'vessel'),
    ('coronary', 'Coronary vessels', r'coronary artery$|^great cardiac vein$|^anterior interventricular.*artery$', 16000, 'vessel'),
    ('skull', 'Skull & jaw', r'^(left |right )?(ethmoid|frontal bone|lacrimal bone|nasal bone|palatine bone|parietal bone|temporal bone|zygomatic bone|mandible|occipital bone|sphenoid bone|vomer|maxilla|inferior nasal concha)$', 65000, 'bone'),
    ('teeth', 'Teeth', r'tooth$', 22000, 'dental'),
    ('rib-cage', 'Rib cage & sternum', r'^(left |right )\w+ rib$|^(body of sternum|manubrium|xiphoid process)$', 38000, 'bone'),
    ('spine', 'Spine', r'^\w+ (cervical|thoracic|lumbar) vertebra$|^(atlas|axis)$', 38000, 'bone'),
    ('pelvis', 'Pelvis & sacrum', r'^(left |right )hip bone$|^sacrum$', 22000, 'bone'),
    ('arm-bones', 'Shoulders & arms', r'^(left|right) (humerus|radius|ulna|scapula|clavicle)$', 22000, 'bone'),
    ('hand-bones', 'Hand bones', r'^(left|right) (\w+ metacarpal bone|capitate|hamate|lunate|pisiform|scaphoid|trapezium|trapezoid|triquetral)$|phalanx of .*(finger|thumb)$', 22000, 'bone'),
    ('leg-bones', 'Leg bones', r'^(left|right) (femur|tibia|fibula|patella)$', 24000, 'bone'),
    ('foot-bones', 'Foot bones', r'^(left|right) (talus|calcaneus|cuboid bone|\w+ cuneiform bone|\w+ metatarsal bone)$|^navicular bone of (left|right) foot$|phalanx of .*toe$', 24000, 'bone'),
    ('muscles', 'Muscles', r'^(left |right )?(deltoid|biceps brachii|triceps brachii|rectus femoris|vastus lateralis|vastus medialis|gastrocnemius|soleus|tibialis anterior|biceps femoris|pectoralis major|rectus abdominis|external oblique|gluteus maximus|brachioradialis)( muscle)?$', 70000, 'muscle'),
]

gltf = {'asset': {'version': '2.0', 'generator': 'Garra Rufa / BodyParts3D', 'copyright': 'BodyParts3D © DBCLS. 4.0 anatomy: CC BY 4.0; 3.0 lung lobes: CC BY-SA 2.1 Japan. See NOTICE.md.'},
        'scene': 0, 'scenes': [{'nodes': []}], 'nodes': [], 'meshes': [], 'accessors': [], 'bufferViews': [], 'buffers': []}
binary = bytearray()
graphs = {}
manifest = {'source': 'BodyParts3D 4.0 with 3.0 lung lobes', 'license': 'CC BY 4.0; lung lobes CC BY-SA 2.1 Japan', 'sourceToWorld': {'scale': scale, 'center': mid.tolist()}, 'organs': []}


def accessor(array, kind, component):
    while len(binary) % 4:
        binary.extend(b'\0')
    offset = len(binary)
    binary.extend(array.tobytes())
    view = len(gltf['bufferViews'])
    gltf['bufferViews'].append({'buffer': 0, 'byteOffset': offset, 'byteLength': array.nbytes})
    result = {'bufferView': view, 'componentType': component, 'count': len(array), 'type': kind}
    if kind == 'VEC3':
        result.update(min=array.min(0).tolist(), max=array.max(0).tolist())
    gltf['accessors'].append(result)
    return len(gltf['accessors']) - 1


for key, title, pattern, budget, category in groups:
    items = [item for item in catalog if re.search(pattern, item['name'], re.I)]
    if not items:
        print('Missing:', key, flush=True)
        continue
    meshes = [read_mesh(item) for item in items]
    original_faces = sum(len(f) for v, f in meshes)
    merged_v, merged_f, offset = [], [], 0
    for vertices, faces in meshes:
        vertices = normalize(vertices)
        target = max(100, int(budget * len(faces) / original_faces))
        if len(faces) > target:
            vertices, faces = fast_simplification.simplify(vertices, faces, target_count=target)
        faces = orient_faces(vertices, faces)
        merged_v.append(vertices)
        merged_f.append(faces + offset)
        offset += len(vertices)
    vertices = np.concatenate(merged_v)
    faces = np.concatenate(merged_f)
    spacing = {'brain': .015, 'heart': .018, 'lungs': .032, 'liver': .026, 'intestines': .029, 'eyes': .006, 'iris': .0028, 'cornea': .004, 'lenses': .0035, 'optic-nerves': .007, 'coronary': .009, 'skull': .019, 'teeth': .007, 'rib-cage': .028, 'spine': .022, 'pelvis': .026, 'arm-bones': .028, 'hand-bones': .013, 'leg-bones': .032, 'foot-bones': .016, 'pituitary': .004, 'adrenals': .012, 'ureters': .012, 'esophagus': .016, 'diaphragm': .032, 'muscles': .04}.get(key, .024)
    graphs[key] = surface_graph(vertices, faces, spacing)
    lo, hi = vertices.min(0), vertices.max(0)
    target = (lo + hi) / 2
    target[2] = hi[2]
    anchor = vertices[np.argmin(np.linalg.norm(vertices - target, axis=1))]
    position = accessor(vertices.astype('<f4'), 'VEC3', 5126)
    normal = accessor(normals(vertices, faces).astype('<f4'), 'VEC3', 5126)
    indices = accessor(faces.astype('<u4').ravel(), 'SCALAR', 5125)
    mesh_index = len(gltf['meshes'])
    gltf['meshes'].append({'name': key, 'primitives': [{'attributes': {'POSITION': position, 'NORMAL': normal}, 'indices': indices}]})
    gltf['scenes'][0]['nodes'].append(len(gltf['nodes']))
    gltf['nodes'].append({'mesh': mesh_index, 'name': key, 'extras': {'category': category, 'title': title}})
    manifest['organs'].append({'id': key, 'name': title, 'category': category, 'anchor': np.round(anchor, 5).tolist(), 'center': np.round((lo + hi) / 2, 5).tolist(), 'bounds': np.round([lo, hi], 5).tolist(), 'triangles': len(faces), 'structures': [item['name'] for item in items]})
    print(key, len(items), 'structures;', len(faces), 'triangles;', len(graphs[key]['nodes']) // 3, 'nodes', flush=True)

gltf['buffers'] = [{'byteLength': len(binary)}]
json_data = json.dumps(gltf, separators=(',', ':')).encode()
json_data += b' ' * ((-len(json_data)) % 4)
binary += b'\0' * ((-len(binary)) % 4)
length = 12 + 8 + len(json_data) + 8 + len(binary)
glb = struct.pack('<III', 0x46546C67, 2, length) + struct.pack('<II', len(json_data), 0x4E4F534A) + json_data + struct.pack('<II', len(binary), 0x004E4942) + binary
(OUT / 'organs.glb').write_bytes(glb)
(OUT / 'organs-graph.json').write_text(json.dumps(graphs, separators=(',', ':')))
(OUT / 'manifest.json').write_text(json.dumps(manifest, indent=2))
print('GLB:', round(len(glb) / 1024 / 1024, 2), 'MiB', flush=True)
