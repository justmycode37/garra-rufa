# Anatomical model attribution

BodyParts3D, © The Database Center for Life Science licensed under
CC Attribution 4.0 International.

- Dataset: [BodyParts3D 4.0](https://dbarchive.biosciencedbc.jp/en/bodyparts3d/download.html)
- Author's current license statement (updated 27 February 2025):
  [License to use this database](https://dbarchive.biosciencedbc.jp/en/bodyparts3d/lic.html)
- License: [Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/)
- Dataset DOI: [10.18908/lsdba.nbdc00837-000](https://doi.org/10.18908/lsdba.nbdc00837-000)
- Reference: Mitsuhashi et al., *BodyParts3D: 3D structure database for anatomical
  concepts*, Nucleic Acids Research (2009), [doi:10.1093/nar/gkn613](https://doi.org/10.1093/nar/gkn613).

The historical OBJ headers mention the earlier CC BY-SA 2.1 Japan license.
The 4.0 structures follow the current CC BY 4.0 license published by the
original database owner at the link above.

The five lung-lobe envelopes are from BodyParts3D 3.0 through
[Kevin-Mattheus-Moerman's STL mirror](https://github.com/Kevin-Mattheus-Moerman/BodyParts3D).
Files: FMA7333, FMA7337, FMA7383, FMA7370, and FMA7371. These lung meshes and
their adapted geometry retain that distribution's
[CC Attribution-ShareAlike 2.1 Japan license](https://creativecommons.org/licenses/by-sa/2.1/jp/).
BodyParts3D, © The Database Center for Life Science licensed under
CC Attribution-ShareAlike 2.1 Japan. The adapted lung geometry is freely
redistributed as the `lungs` mesh in `organs.glb` and its sampled graph in
`organs-graph.json` under that same license.

Garra Rufa adaptations: selected anatomical structures, mesh simplification,
surface graph sampling, GLB conversion, and monochrome rendering. All structures
use one common source-to-world transform; no per-organ placement offsets are applied.
The reproducible conversion script is `tools/build-atlas-anatomy.py`.

The model is an illustrative reference anatomy. Condition markers indicate
associations with a region, not a precise lesion or patient-specific finding.
No endorsement by the original authors is implied.
