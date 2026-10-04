# Human graph asset

`human-exploration.json` is a decorative surface graph derived from the MakeHuman
community's **hm08** base mesh, released under CC0 in September 2020.

- Source: https://github.com/makehumancommunity/makehuman/blob/master/makehuman/data/3dobjs/base.obj
- License: `MakeHuman-CC0.txt`
- Original authors: Data Collection AB, Joel Palmius, Jonas Hauquier.

Only the `body` group is rendered. Joint-helper centers provide anatomical
landmarks for the shoulders, elbows, wrists, neck, fingers, hips, knees and
ankles; helper geometry
and clothing proxies are excluded from the visible mesh.
The mesh is normalized, posed with relaxed arms, and sampled into an
asymmetric graph. Surface and graph weights share the same articulated rig.
The 15 rotation influences, in order, are left shoulder, left elbow, left
forearm twist, left wrist, right shoulder, right elbow, right forearm twist,
right wrist, neck, left hip, left knee, left ankle, right hip, right knee,
and right ankle. Forearm twist increases gradually from zero at the elbow
to full influence at the hand; wrist flexion has its own short blend region.
`rig.jointNames` identifies the separate anatomical landmark positions.
`rig.boneCount` is the stride of both vertex and graph-node weight arrays.

The earlier `human-graph.json` is retained during the renderer migration.
The new asset preserves exactly the same mesh, graph nodes and connections.
Rebuild with `python3 tools/build-human-graph.py /path/to/base.obj` (NumPy/SciPy).
The graph is illustrative; it does not depict medical records or patient data.

## Mission hands

`reaching-hands.json` extracts the anatomical right hand and lower forearm from
the same CC0 source in its original rest pose. It includes the solid surface
mesh for depth occlusion and 1,029 irregularly sampled nodes joined by 2,848
short, straight edges. The renderer can reflect the hand to create its partner.
There is no photographic texture or flat hand silhouette in this asset.

The wrist is the origin. Fingers point along +X, the thumb toward +Y, and the
palm faces +Z. Wrist to middle fingertip distance is two world units; the
forearm extends approximately 3.3 units behind the wrist. The open forearm
end is intended to extend past the scene boundary.

The 16 rig influences and first 16 `jointNames` are wrist followed by MCP, PIP
and DIP for the thumb, index, middle, ring and pinky, in that order. Each finger
also has a named `Tip` landmark. `curlAxes`
contains one axis triplet per influence; positive rotations flex toward the
palm. Both weight arrays use `boneCount` (16) as their stride. Apply finger DIP
before PIP and MCP, then wrist, using angle-weighted rotations to preserve
volume. The thumb's MCP/PIP/DIP names refer to its three source chain joints
(anatomically CMC/MCP/IP).

Forearm-to-fingertip spacing varies by less than 1.5×. Local tangent-plane
triangulation keeps the network consistent without crossed nearest-neighbour
chords. The renderer uses an orthographic camera to keep anatomical proportions
consistent, with a descending left hand and an ascending right index finger
inspired by the near-touching gesture in Michelangelo's *Creation of Adam*.
Small, coordinated joint changes preserve the pose and the gap throughout the
animation; reduced motion uses a fixed pose.

`ConnectionExperience` keeps one renderer mounted in the root layout. The
original body hand stays unchanged until About is clicked. The six-second
transition starts zooming immediately and lifts the viewer-left arm (anatomical
right). The original graph nodes articulate into the reaching pose. Extra detail
grows over 1.4 seconds only once the rest of the body is outside the viewport.
The asset's `body` mapping retains the original mesh vertices and maps original
graph nodes through their source triangles, keeping the forearm connected.
The figure and camera never rotate. The partner hand enters from above-left
and retains gentle translation and finger motion. Route content fades independently
of the scene. Reduced motion uses immediate navigation. Static SVG fallbacks
can be rebuilt with `node --import tsx tools/build-connection-fallbacks.ts`.

Rebuild with
`python3 tools/build-reaching-hands.py /path/to/base.obj` (NumPy/SciPy).
The generator replaces the JSON atomically. The renderer validates schema
version 2 before using it, so an older or incomplete asset cannot reach an
animation frame during development.
