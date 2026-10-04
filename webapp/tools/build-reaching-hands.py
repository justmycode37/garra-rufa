"""Extract an articulated 3D hand graph from MakeHuman's CC0 hm08 mesh.

Usage: python3 tools/build-reaching-hands.py /path/to/base.obj
Requires NumPy and SciPy. Source and license: public/models/README.md.
The right hand is canonical: wrist at zero, fingers +X, thumb +Y, palm +Z.
"""
import json
import sys
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree, Delaunay

rng = np.random.default_rng(28173)
vertices, faces, helpers = [], [], {}
group = ''
for line in Path(sys.argv[1]).read_text().splitlines():
    if line.startswith('v '):
        vertices.append(list(map(float, line.split()[1:4])))
    elif line.startswith('g '):
        group = line[2:]
    elif line.startswith('f '):
        q = [int(x.split('/')[0]) - 1 for x in line.split()[1:]]
        if group == 'body':
            faces.extend([[q[0], q[i], q[i + 1]] for i in range(1, len(q) - 1)])
        elif group.startswith('joint-r-'):
            helpers.setdefault(group, set()).update(q)

source = np.array(vertices)
faces = np.array(faces)
body_vertex_ids = np.unique(faces)
body_asset = json.loads((Path(__file__).resolve().parents[1] / 'public/models/human-exploration.json').read_text())
body_positions = np.array(body_asset['positions']).reshape(-1, 3)
body_weights = np.array(body_asset['rig']['surfaceWeights']).reshape(-1, 15)
source_joints = {name: source[list(ids)].mean(axis=0) for name, ids in helpers.items()}
wrist = source_joints['joint-r-hand']
forward = source_joints['joint-r-finger-3-4'] - wrist
scale = 2 / np.linalg.norm(forward)
forward /= np.linalg.norm(forward)
radial = source_joints['joint-r-finger-2-1'] - source_joints['joint-r-finger-5-1']
radial -= np.dot(radial, forward) * forward
radial /= np.linalg.norm(radial)
palmar = np.cross(forward, radial)
basis = np.column_stack((forward, radial, palmar))

def canonical(points):
    # Element-wise formulation avoids platform BLAS edge cases on helper data.
    return np.sum((points - wrist)[..., :, None] * basis, axis=-2) * scale

all_positions = canonical(source)
shoulder = source_joints['joint-r-shoulder']
elbow = source_joints['joint-r-elbow']
upper_axis = elbow - shoulder
upper_t = np.clip(np.sum((source - shoulder) * upper_axis, axis=1) / np.dot(upper_axis, upper_axis), 0, 1)
upper_distance = np.linalg.norm(source - shoulder - upper_t[:, None] * upper_axis, axis=1)
# Include the humerus and shoulder cap, not a wrist-length cutout. The entire
# arm keeps its source correspondence even when the shoulder is offscreen.
keep_vertex = (source[:, 0] < -1.42) & (source[:, 1] > .9) & ((source[:, 0] < -2.7) | (upper_distance < .78))
faces = faces[np.all(keep_vertex[faces], axis=1)]
used, inverse = np.unique(faces, return_inverse=True)
v = all_positions[used]
tri = inverse.reshape(-1, 3)
body_ids = np.searchsorted(body_vertex_ids, used)
body_v = body_positions[body_ids]
body_w = body_weights[body_ids]
body_node_ids = cKDTree(body_positions).query(np.array(body_asset['nodes']).reshape(-1, 3))[1]
body_node_mask = np.isin(body_node_ids, body_ids)
body_cross = np.cross(body_v[tri[:, 1]] - body_v[tri[:, 0]], body_v[tri[:, 2]] - body_v[tri[:, 0]])
body_normals = np.zeros_like(body_v)
for corner in range(3):
    np.add.at(body_normals, tri[:, corner], body_cross)
body_normals /= np.maximum(np.linalg.norm(body_normals, axis=1, keepdims=True), 1e-9)

names = ['thumb', 'index', 'middle', 'ring', 'pinky']
finger_joints = np.array([
    [canonical(source_joints[f'joint-r-finger-{i}-{j}']) for j in range(1, 5)]
    for i in range(1, 6)
])
joint_names = ['wrist']
joints = [np.zeros(3)]
curl_axes = [np.array([0., -1., 0.])]
for name, finger in zip(names, finger_joints):
    for j, suffix in enumerate(('MCP', 'PIP', 'DIP')):
        joint_names.append(name + suffix)
        joints.append(finger[j])
        axis = np.cross(finger[j + 1] - finger[j], palmar @ basis)
        axis /= np.linalg.norm(axis)
        curl_axes.append(axis)
for name, finger in zip(names, finger_joints):
    joint_names.append(name + 'Tip')
    joints.append(finger[3])
bone_count = len(curl_axes)

def smooth(x):
    x = np.clip(x, 0, 1)
    return x * x * (3 - 2 * x)

def segment_distance(points, start, end):
    direction = end - start
    along = np.clip(np.sum((points - start) * direction, axis=1) / np.dot(direction, direction), 0, 1)
    return np.linalg.norm(points - start - along[:, None] * direction, axis=1)

# Finger ownership uses each anatomical chain, so adjacent fingers never drag
# one another. Every phalanx has its own blend to retain knuckle volume.
finger_distances = np.array([
    np.min([segment_distance(v, finger[k], finger[k + 1]) for k in range(3)], axis=0)
    for finger in finger_joints
])
ownership = finger_distances.argmin(axis=0)
weights = np.zeros((len(v), bone_count))
weights[:, 0] = smooth((v[:, 0] + .22) / .5)
for i, finger in enumerate(finger_joints):
    membership = (ownership == i).astype(float)
    for k in (0, 1, 2):
        axis = finger[k + 1] - finger[k]
        axis /= np.linalg.norm(axis)
        along = np.sum((v - finger[k]) * axis, axis=1)
        weight = smooth((along + .025) / .125) * membership
        if k > 0:
            weight *= weights[:, i * 3 + k]
        weights[:, i * 3 + k + 1] = weight

cross = np.cross(v[tri[:, 1]] - v[tri[:, 0]], v[tri[:, 2]] - v[tri[:, 0]])
area = np.linalg.norm(cross, axis=1)
normals = np.zeros_like(v)
for corner in range(3):
    np.add.at(normals, tri[:, corner], cross)
normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-9)

# Map the existing body graph onto the very same source triangles. This lets
# its original nodes articulate into the mission pose before any refinement.
original_node_ids = np.flatnonzero(body_node_mask)
original_p = np.array(body_asset['nodes']).reshape(-1, 3)[original_node_ids]
nearby_faces = cKDTree(body_v[tri].mean(axis=1)).query(original_p, k=24)[1]
candidate_triangles = body_v[tri[nearby_faces]]
a = candidate_triangles[:, :, 0]
ab = candidate_triangles[:, :, 1] - a
ac = candidate_triangles[:, :, 2] - a
ap = original_p[:, None, :] - a
dot = lambda x, y: np.sum(x * y, axis=-1)
d00, d01, d11 = dot(ab, ab), dot(ab, ac), dot(ac, ac)
d20, d21 = dot(ap, ab), dot(ap, ac)
denominator = np.maximum(d00 * d11 - d01 * d01, 1e-14)
vb = (d11 * d20 - d01 * d21) / denominator
wb = (d00 * d21 - d01 * d20) / denominator
candidate_bary = np.stack((1 - vb - wb, vb, wb), axis=-1)
projected = np.sum(candidate_triangles * candidate_bary[:, :, :, None], axis=2)
score = dot(projected - original_p[:, None, :], projected - original_p[:, None, :]) + np.sum(np.minimum(candidate_bary, 0) ** 2, axis=-1)
best = score.argmin(axis=1)
original_faces = nearby_faces[np.arange(len(original_p)), best]
original_bary = candidate_bary[np.arange(len(original_p)), best]
original_nodes = np.sum(v[tri[original_faces]] * original_bary[:, :, None], axis=1)
original_weights = np.sum(weights[tri[original_faces]] * original_bary[:, :, None], axis=1)
original_normals = np.sum(normals[tri[original_faces]] * original_bary[:, :, None], axis=1)
original_normals /= np.maximum(np.linalg.norm(original_normals, axis=1, keepdims=True), 1e-9)

# The palm, webbing and fingers receive finer sampling than the forearm.
face_ids = rng.choice(len(tri), 80000, p=area / area.sum())
bary = rng.random((len(face_ids), 2))
bary[bary.sum(axis=1) > 1] = 1 - bary[bary.sum(axis=1) > 1]
bary = np.column_stack((bary, 1 - bary.sum(axis=1)))
p = (v[tri[face_ids]] * bary[:, :, None]).sum(axis=1)
n = (normals[tri[face_ids]] * bary[:, :, None]).sum(axis=1)
n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-9)
sample_weights = (weights[tri[face_ids]] * bary[:, :, None]).sum(axis=1)
body_p = (body_v[tri[face_ids]] * bary[:, :, None]).sum(axis=1)
body_sample_weights = (body_w[tri[face_ids]] * bary[:, :, None]).sum(axis=1)
body_sample_normals = (body_normals[tri[face_ids]] * bary[:, :, None]).sum(axis=1)
body_sample_normals /= np.maximum(np.linalg.norm(body_sample_normals, axis=1, keepdims=True), 1e-9)
# One visual density from forearm through palm, with just enough extra
# fingertip detail to describe the pads without forming dark tangles.
spacing = .095 - .030 * smooth((p[:, 0] + .2) / 1.2)
tree = cKDTree(p)
available = np.ones(len(p), dtype=bool)
selected = []
for i in rng.permutation(len(p)):
    if not available[i]:
        continue
    selected.append(i)
    nearby = np.array(tree.query_ball_point(p[i], .11))
    close = np.linalg.norm(p[nearby] - p[i], axis=1) < (spacing[i] + spacing[nearby]) * .5
    available[nearby[close]] = False
p, n, spacing, sample_weights = p[selected], n[selected], spacing[selected], sample_weights[selected]
body_p, body_sample_weights = body_p[selected], body_sample_weights[selected]
body_sample_normals = body_sample_normals[selected]
tree = cKDTree(p)
edges = set()
for i in range(len(p)):
    candidates = []
    for j in tree.query_ball_point(p[i], .21):
        if i == j:
            continue
        delta = p[j] - p[i]
        length = np.linalg.norm(delta)
        relative = length / ((spacing[i] + spacing[j]) * .5)
        if relative > 1.85 or np.dot(n[i], n[j]) < .15:
            continue
        if max(abs(np.dot(delta / length, n[i])), abs(np.dot(delta / length, n[j]))) > .74:
            continue
        candidates.append(j)
    if len(candidates) < 3:
        continue
    tangent = np.cross(n[i], [0, 0, 1] if abs(n[i, 2]) < .9 else [0, 1, 0])
    tangent /= np.linalg.norm(tangent)
    bitangent = np.cross(n[i], tangent)
    ids = [i] + candidates
    delta = p[ids] - p[i]
    local = np.column_stack((delta @ tangent, delta @ bitangent))
    # Local surface triangulation avoids the crossed chords produced by
    # connecting an arbitrary number of nearest neighbours.
    for face in Delaunay(local).simplices:
        if 0 in face:
            for corner in face:
                if corner:
                    edges.add(tuple(sorted((i, ids[corner]))))

connections = np.array(sorted(edges))
connected = np.unique(connections)
remap = np.full(len(p), -1, dtype=int)
remap[connected] = np.arange(len(connected))
p, n, spacing, sample_weights = p[connected], n[connected], spacing[connected], sample_weights[connected]
body_p, body_sample_weights = body_p[connected], body_sample_weights[connected]
body_sample_normals = body_sample_normals[connected]
connections = remap[connections]
lengths = np.linalg.norm(p[connections[:, 0]] - p[connections[:, 1]], axis=1)
assert np.isfinite(v).all() and np.isfinite(p).all() and np.isfinite(n).all()
assert lengths.max() < .21 and lengths.min() > .035
assert weights.shape == (len(v), bone_count) and sample_weights.shape == (len(p), bone_count)
assert weights.min() >= 0 and weights.max() <= 1
assert np.max(np.sum(weights[:, 1::3] > .01, axis=1)) == 1
asset = {
    'schemaVersion': 2,
    'positions': np.round(v, 5).flatten().tolist(),
    'triangles': tri.flatten().tolist(),
    'nodes': np.round(p, 5).flatten().tolist(),
    'normals': np.round(n, 5).flatten().tolist(),
    'surfaceNormals': np.round(normals, 5).flatten().tolist(),
    'spacing': np.round(spacing, 4).tolist(),
    'edges': connections.flatten().tolist(),
    'body': {
        'vertexIds': body_ids.tolist(),
        'nodeMask': body_node_mask.tolist(),
        'originalNodeIds': original_node_ids.tolist(),
        'growthNodeIds': original_node_ids[cKDTree(original_nodes).query(p)[1]].tolist(),
        'originalNodes': np.round(original_nodes, 5).flatten().tolist(),
        'originalNodeWeights': np.round(original_weights, 4).flatten().tolist(),
        'originalNodeNormals': np.round(original_normals, 5).flatten().tolist(),
        'positions': np.round(body_v, 5).flatten().tolist(),
        'nodes': np.round(body_p, 5).flatten().tolist(),
        'surfaceNormals': np.round(body_normals, 5).flatten().tolist(),
        'nodeNormals': np.round(body_sample_normals, 5).flatten().tolist(),
        'surfaceWeights': np.round(body_w, 4).flatten().tolist(),
        'nodeWeights': np.round(body_sample_weights, 4).flatten().tolist(),
    },
    'rig': {
        'boneCount': bone_count,
        'jointNames': joint_names,
        'joints': np.round(joints, 5).flatten().tolist(),
        'curlAxes': np.round(curl_axes, 6).flatten().tolist(),
        'surfaceWeights': np.round(weights, 4).flatten().tolist(),
        'nodeWeights': np.round(sample_weights, 4).flatten().tolist(),
    },
}
output = Path(__file__).resolve().parents[1] / 'public/models/reaching-hands.json'
temporary = output.with_suffix('.json.tmp')
temporary.write_text(json.dumps(asset, separators=(',', ':')))
temporary.replace(output)
print(json.dumps({
    'vertices': len(v), 'triangles': len(tri), 'nodes': len(p), 'edges': len(connections),
    'bounds': [v.min(axis=0).round(3).tolist(), v.max(axis=0).round(3).tolist()],
    'edgeRange': [round(float(lengths.min()), 3), round(float(lengths.max()), 3)],
    'sizeKB': output.stat().st_size // 1024,
}))
