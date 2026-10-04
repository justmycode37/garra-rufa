"""Build the decorative graph from MakeHuman's CC0 hm08 body mesh.

Usage: python3 tools/build-human-graph.py /path/to/base.obj
Requires numpy and scipy. Source and license: public/models/README.md.
"""
import json
import sys
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

rng = np.random.default_rng(73519)
vertices, faces, joint_vertices = [], [], {}
body = False
group = ''
for line in Path(sys.argv[1]).read_text().splitlines():
    if line.startswith('v '):
        vertices.append(list(map(float, line.split()[1:4])))
    elif line.startswith('g '):
        group = line[2:]
        body = line == 'g body'
        if group.startswith('joint-'):
            joint_vertices.setdefault(group, set())
    elif line.startswith('f ') and body:
        q = [int(x.split('/')[0]) - 1 for x in line.split()[1:]]
        faces.extend([[q[0], q[i], q[i + 1]] for i in range(1, len(q) - 1)])
    elif line.startswith('f ') and group.startswith('joint-'):
        joint_vertices[group].update(int(x.split('/')[0]) - 1 for x in line.split()[1:])

used, inverse = np.unique(faces, return_inverse=True)
v = np.array(vertices)[used]
tri = inverse.reshape(-1, 3)

def smooth(x):
    x = np.clip(x, 0, 1)
    return x * x * (3 - 2 * x)

def turn(points, pivot, axis, angle, weight):
    """Blend a joint rotation, preserving volume through wrists and elbows."""
    direction = np.array(axis, dtype=float)
    direction /= np.linalg.norm(direction)
    delta = points - np.array(pivot)
    theta = angle * weight[:, None]
    projection = np.sum(delta * direction, axis=1)
    return np.array(pivot) + delta * np.cos(theta) + np.cross(direction, delta) * np.sin(theta) + np.outer(projection, direction) * (1 - np.cos(theta))

def relaxed_pose(rest):
    result = rest.copy()
    for side in (-1, 1):
        mask = (rest[:, 0] * side > 1.4) & (rest[:, 1] > 1)
        p = rest[mask].copy()
        arm = smooth((p[:, 0] * side - 1.4) / .95)
        wrist = smooth((3.7 - p[:, 1]) / 1.05) * arm
        p = turn(p, [side * 3.6, 2.9, 1.05], [side * .52, -.62, .59], side * .98, wrist)
        elbow = smooth((4.15 - rest[mask, 1]) / .8) * arm
        p = turn(p, [side * 3.05, 3.85, .38], [.72, side * .70, 0], .69, elbow)
        p = turn(p, [side * 1.75, 5.13, .12], [0, 0, 1], -side * (.39 if side == 1 else .43), arm)
        result[mask] = p
    for side in (-1, 1):
        mask = (rest[:, 0] * side > 0) & (rest[:, 1] < .7)
        blend = smooth((.7 - rest[mask, 1]) / 2.0)
        feet = smooth((-7.05 - rest[mask, 1]) / .65)
        p = turn(result[mask], [side * 2.15, -7.35, .25], [1, 0, 0], .24, feet)
        result[mask] = turn(p, [side * .9, -.25, 0], [0, 0, 1], -side * (.195 if side == 1 else .18), blend)
    return result

rest = v.copy()
v = relaxed_pose(rest)
center_y = (v[:, 1].min() + v[:, 1].max()) / 2
scale = 7 / np.ptp(v[:, 1])
def normalize(points):
    points = points.copy()
    points[:, 1] -= center_y
    points *= scale
    points[:, 2] -= .27
    points[:, 0] += .025 * np.sin(points[:, 1] * 1.3)
    points[:, 2] += .02 * np.sin(points[:, 0] * 2 + points[:, 1])
    return points
v = normalize(v)

joint_sources = {
    'lShoulder': 'joint-l-shoulder', 'lElbow': 'joint-l-elbow', 'lWrist': 'joint-l-hand',
    'rShoulder': 'joint-r-shoulder', 'rElbow': 'joint-r-elbow', 'rWrist': 'joint-r-hand',
    'neck': 'joint-neck', 'head': 'joint-head',
    'lMiddleBase': 'joint-l-finger-3-1', 'lMiddleTip': 'joint-l-finger-3-4',
    'rMiddleBase': 'joint-r-finger-3-1', 'rMiddleTip': 'joint-r-finger-3-4',
    'lHip': 'joint-l-upper-leg', 'lKnee': 'joint-l-knee', 'lAnkle': 'joint-l-ankle',
    'rHip': 'joint-r-upper-leg', 'rKnee': 'joint-r-knee', 'rAnkle': 'joint-r-ankle',
    'lIndexBase': 'joint-l-finger-2-1', 'lPinkyBase': 'joint-l-finger-5-1',
    'rIndexBase': 'joint-r-finger-2-1', 'rPinkyBase': 'joint-r-finger-5-1',
}
joint_names = list(joint_sources)
joint_rest = np.array([np.array(vertices)[list(joint_vertices[name])].mean(axis=0) for name in joint_sources.values()])
joints = normalize(relaxed_pose(joint_rest))
joint = dict(zip(joint_names, joints))
# The original pose identifies the limb; weights within it follow each
# relaxed bone's axis. Forearm rotation is distributed from elbow to hand;
# the separate wrist influence bends only the short joint and palm region.
arm_boundary = 1.3 + .85 * smooth((4.9 - rest[:, 1]) / 1.4)
weights = np.zeros((len(v), 15))
for prefix, side, offset in [('l', 1, 0), ('r', -1, 4)]:
    arm = smooth((rest[:, 0] * side - arm_boundary) / .65) * (rest[:, 1] > 1)
    lower_axis = joint[prefix + 'Wrist'] - joint[prefix + 'Elbow']
    lower_length = np.linalg.norm(lower_axis)
    lower_axis /= lower_length
    along_forearm = np.sum((v - joint[prefix + 'Elbow']) * lower_axis, axis=1)
    # Keep each upper-arm cross-section rigid. The old diagonal boundary ran
    # down the humerus, rotating its inner and outer vertices by different
    # angles. Only the shoulder socket blends into the chest.
    socket_boundary = 1.0 + smooth((4.8 - rest[:, 1]) / 1.6)
    weights[:, offset] = smooth((rest[:, 0] * side - socket_boundary) / .7) * (rest[:, 1] > 1) if prefix == 'r' else arm
    weights[:, offset + 1] = arm * smooth((along_forearm + .11) / .27)
    weights[:, offset + 2] = arm * smooth(along_forearm / lower_length)
    weights[:, offset + 3] = arm * smooth((along_forearm - lower_length + .015) / .16)
weights[:, 8] = smooth((rest[:, 1] - 5.78) / .72)
for prefix, side, offset in [('l', 1, 9), ('r', -1, 12)]:
    # Keep the pelvis and opposite leg still; soften the groin transition.
    leg = smooth((rest[:, 0] * side - .10) / .65) * smooth((.8 - rest[:, 1]) / 1.55)
    shin_axis = joint[prefix + 'Ankle'] - joint[prefix + 'Knee']
    shin_length = np.linalg.norm(shin_axis)
    shin_axis /= shin_length
    along_shin = np.sum((v - joint[prefix + 'Knee']) * shin_axis, axis=1)
    weights[:, offset] = leg
    weights[:, offset + 1] = leg * smooth((along_shin + .15) / .30)
    weights[:, offset + 2] = leg * smooth((along_shin - shin_length + .02) / .17)

cross = np.cross(v[tri[:, 1]] - v[tri[:, 0]], v[tri[:, 2]] - v[tri[:, 0]])
area = np.linalg.norm(cross, axis=1)
normals = np.zeros_like(v)
for corner in range(3):
    np.add.at(normals, tri[:, corner], cross)
normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-9)

# Area-weighted Poisson sampling gives small, similar-length connections,
# with finer detail around fingers, facial features and toes.
face_ids = rng.choice(len(tri), 95000, p=area / area.sum())
bary = rng.random((len(face_ids), 2))
bary[bary.sum(axis=1) > 1] = 1 - bary[bary.sum(axis=1) > 1]
bary = np.column_stack((bary, 1 - bary.sum(axis=1)))
p = (v[tri[face_ids]] * bary[:, :, None]).sum(axis=1)
sample_weights = (weights[tri[face_ids]] * bary[:, :, None]).sum(axis=1)
n = (normals[tri[face_ids]] * bary[:, :, None]).sum(axis=1)
n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-9)
# After posing, hands sit beside the pelvis instead of at shoulder width.
detail = ((np.abs(p[:, 0]) > .85) & (p[:, 1] < .75) & (p[:, 1] > -.9)) | (p[:, 1] > 2.65) | (p[:, 1] < -3.05)
spacing = np.where(detail, .048, .093)
tree = cKDTree(p)
available = np.ones(len(p), dtype=bool)
selected = []
for i in rng.permutation(len(p)):
    if not available[i]:
        continue
    selected.append(i)
    nearby = np.array(tree.query_ball_point(p[i], .093))
    close = np.linalg.norm(p[nearby] - p[i], axis=1) < (spacing[i] + spacing[nearby]) * .5
    available[nearby[close]] = False
p, n, spacing, sample_weights = p[selected], n[selected], spacing[selected], sample_weights[selected]
tree = cKDTree(p)
edges = set()
for i in range(len(p)):
    candidates = []
    for j in tree.query_ball_point(p[i], .19):
        if i == j:
            continue
        delta = p[j] - p[i]
        length = np.linalg.norm(delta)
        relative = length / ((spacing[i] + spacing[j]) * .5)
        # Do not bridge across fingers or through opposing body surfaces.
        if relative > 1.85 or np.dot(n[i], n[j]) < .15:
            continue
        if max(abs(np.dot(delta / length, n[i])), abs(np.dot(delta / length, n[j]))) > .72:
            continue
        candidates.append((abs(relative - 1.22), j))
    for _, j in sorted(candidates)[:4]:
        edges.add(tuple(sorted((i, j))))

connections = np.array(sorted(edges))
# Drop rare isolated samples at very narrow finger creases.
connected = np.unique(connections)
remap = np.full(len(p), -1, dtype=int)
remap[connected] = np.arange(len(connected))
p, n, spacing, sample_weights = p[connected], n[connected], spacing[connected], sample_weights[connected]
connections = remap[connections]
lengths = np.linalg.norm(p[connections[:, 0]] - p[connections[:, 1]], axis=1)
assert np.isfinite(p).all() and lengths.max() < .19
assert np.isfinite(weights).all() and np.isfinite(sample_weights).all()
assert weights.shape == (len(v), 15) and sample_weights.shape == (len(p), 15)
assert weights.min() >= 0 and weights.max() <= 1
assert np.all((weights[:, 0] == 0) | (weights[:, 4] == 0))
assert np.all((weights[:, 9] == 0) | (weights[:, 12] == 0))
asset = {
    'positions': np.round(v, 5).flatten().tolist(),
    'triangles': tri.flatten().tolist(),
    'nodes': np.round(p, 5).flatten().tolist(),
    'normals': np.round(n, 5).flatten().tolist(),
    'spacing': np.round(spacing, 3).tolist(),
    'edges': connections.flatten().tolist(),
    'rig': {
        'boneCount': 15,
        'jointNames': joint_names,
        'joints': np.round(joints, 5).flatten().tolist(),
        'surfaceWeights': np.round(weights, 4).flatten().tolist(),
        'nodeWeights': np.round(sample_weights, 4).flatten().tolist(),
    },
}
output = Path(__file__).resolve().parents[1] / 'public/models/human-exploration.json'
temporary = output.with_suffix('.json.tmp')
temporary.write_text(json.dumps(asset, separators=(',', ':')))
temporary.replace(output)
print(json.dumps({'nodes': len(p), 'edges': len(edges), 'triangles': len(tri), 'maxEdge': round(float(lengths.max()), 3), 'sizeKB': output.stat().st_size // 1024}))
