"""Deterministic surface preparation for the anatomical graph assets."""
import numpy as np
from scipy.spatial import cKDTree


def orient_faces(vertices, faces):
    """Repair inconsistent OBJ winding before calculating surface normals."""
    faces = faces.copy()
    edges, adjacent = {}, [[] for _ in faces]
    for i, face in enumerate(faces):
        for a, b in zip(face, np.roll(face, -1)):
            key = (min(a, b), max(a, b))
            if key in edges:
                j, c, d = edges[key]
                same = a == c and b == d
                adjacent[i].append((j, same))
                adjacent[j].append((i, same))
            else:
                edges[key] = (i, a, b)
    visited = np.zeros(len(faces), dtype=bool)
    flipped = np.zeros(len(faces), dtype=bool)
    for start in range(len(faces)):
        if visited[start]:
            continue
        stack, component = [start], []
        visited[start] = True
        while stack:
            i = stack.pop()
            component.append(i)
            for j, same in adjacent[i]:
                if not visited[j]:
                    visited[j] = True
                    flipped[j] = flipped[i] ^ same
                    stack.append(j)
        ids = np.asarray(component)
        f = faces[ids].copy()
        f[flipped[ids]] = f[flipped[ids]][:, [0, 2, 1]]
        v = vertices[f]
        # Translation to the component centroid also stabilizes open surfaces.
        v = v - v.reshape(-1, 3).mean(0)
        volume = np.einsum('ij,ij->i', v[:, 0], np.cross(v[:, 1], v[:, 2])).sum()
        if volume < 0:
            flipped[ids] = ~flipped[ids]
    faces[flipped] = faces[flipped][:, [0, 2, 1]]
    return faces


def normals(vertices, faces):
    cross = np.cross(vertices[faces[:, 1]] - vertices[faces[:, 0]], vertices[faces[:, 2]] - vertices[faces[:, 0]])
    result = np.zeros_like(vertices)
    for corner in range(3):
        np.add.at(result, faces[:, corner], cross)
    result /= np.maximum(np.linalg.norm(result, axis=1, keepdims=True), 1e-12)
    return result


def surface_graph(vertices, faces, spacing, seed=5718):
    n = normals(vertices, faces)
    area = np.linalg.norm(np.cross(vertices[faces[:, 1]] - vertices[faces[:, 0]], vertices[faces[:, 2]] - vertices[faces[:, 0]]), axis=1)
    count = min(180000, max(5000, int(area.sum() / spacing ** 2 * 12)))
    rng = np.random.default_rng(seed)
    ids = rng.choice(len(faces), count, p=area / area.sum())
    bary = rng.random((len(ids), 2))
    bary[bary.sum(1) > 1] = 1 - bary[bary.sum(1) > 1]
    bary = np.column_stack((bary, 1 - bary.sum(1)))
    points = (vertices[faces[ids]] * bary[:, :, None]).sum(1)
    pn = (n[faces[ids]] * bary[:, :, None]).sum(1)
    pn /= np.maximum(np.linalg.norm(pn, axis=1, keepdims=True), 1e-12)
    tree = cKDTree(points)
    available = np.ones(count, dtype=bool)
    selected = []
    for i in rng.permutation(count):
        if available[i]:
            selected.append(i)
            available[tree.query_ball_point(points[i], spacing)] = False
    points, pn = points[selected], pn[selected]
    tree = cKDTree(points)
    edges = set()
    for i in range(len(points)):
        options = []
        for j in tree.query_ball_point(points[i], spacing * 1.9):
            if i == j:
                continue
            delta = points[j] - points[i]
            length = np.linalg.norm(delta)
            if np.dot(pn[i], pn[j]) < -.05:
                continue
            if max(abs(np.dot(delta / length, pn[i])), abs(np.dot(delta / length, pn[j]))) > .78:
                continue
            options.append((abs(length / spacing - 1.2), j))
        for _, j in sorted(options)[:5]:
            edges.add(tuple(sorted((i, j))))
    return {'nodes': np.round(points, 5).ravel().tolist(), 'normals': np.round(pn, 5).ravel().tolist(),
            'spacing': spacing, 'edges': np.array(sorted(edges)).ravel().tolist()}
