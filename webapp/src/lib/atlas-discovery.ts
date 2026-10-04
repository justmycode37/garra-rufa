import { diseases } from './knowledge';
import type { Disease } from './types';

// Local UI fixtures. Replace this adapter with the repository-backed graph later.
// Publication records link to real citations; this is not a live literature search.
export type AtlasContext = { target: string; label: string; diseaseIds: string[]; diseaseId?: string };
export type AtlasResource = { id: string; kind: 'paper' | 'gene' | 'reference'; title: string; caption: string; description: string; url: string; diseaseId: string };
export type AtlasReply = { text: string; context?: AtlasContext; resources: AtlasResource[]; prompts: string[] };

const areas: { id: string; label: string; terms: string[]; diseases: string[] }[] = [
  { id: 'brain', label: 'Brain & nerves', terms: ['brain', 'head', 'nerves', 'neurological', 'memory', 'seizures'], diseases: ['huntington', 'rett'] },
  { id: 'heart', label: 'Heart', terms: ['heart', 'cardiac', 'cardiovascular', 'chest'], diseases: ['fabry', 'marfan', 'pompe'] },
  { id: 'eyes', label: 'Eyes', terms: ['eyes', 'eye', 'vision', 'lens'], diseases: ['marfan'] },
  { id: 'hands', label: 'Hands & joints', terms: ['hands', 'hand', 'fingers', 'finger', 'wrist', 'wrists', 'joints', 'joint'], diseases: ['cmt', 'eds', 'fabry', 'marfan', 'rett'] },
  { id: 'muscles', label: 'Muscles', terms: ['muscle', 'muscles', 'muscular'], diseases: ['sma', 'pompe'] },
  { id: 'legs', label: 'Legs & feet', terms: ['legs', 'leg', 'feet', 'foot', 'ankle', 'walking'], diseases: ['cmt', 'eds', 'sma', 'pompe'] },
  { id: 'skeleton', label: 'Skeleton', terms: ['skeleton', 'bones', 'bone'], diseases: ['marfan', 'eds'] },
  { id: 'kidneys', label: 'Kidneys', terms: ['kidney', 'kidneys', 'renal'], diseases: ['fabry'] },
  { id: 'lungs', label: 'Lungs', terms: ['lungs', 'lung', 'breathing', 'respiratory'], diseases: ['pompe', 'sma'] },
  { id: 'liver', label: 'Liver', terms: ['liver', 'abdomen', 'abdominal'], diseases: [] },
];

const relatedAreas: Record<string, string> = {
  aorta: 'heart', coronary: 'heart', 'hand-bones': 'hands', 'foot-bones': 'legs', 'leg-bones': 'legs',
  'arm-bones': 'skeleton', skull: 'skeleton', spine: 'skeleton', pelvis: 'skeleton', 'rib-cage': 'skeleton',
  iris: 'eyes', cornea: 'eyes', lenses: 'eyes', 'optic-nerves': 'eyes', diaphragm: 'muscles', airways: 'lungs',
};

const organTerms: { id: string; label: string; terms: string[] }[] = [
  { id: 'aorta', label: 'Aorta', terms: ['aorta', 'aortic'] },
  { id: 'coronary', label: 'Coronary vessels', terms: ['coronary'] },
  { id: 'stomach', label: 'Stomach', terms: ['stomach'] },
  { id: 'intestines', label: 'Intestines', terms: ['intestines', 'intestine', 'bowel', 'gut'] },
  { id: 'pancreas', label: 'Pancreas', terms: ['pancreas', 'pancreatic'] },
  { id: 'spleen', label: 'Spleen', terms: ['spleen'] },
  { id: 'gallbladder', label: 'Gallbladder', terms: ['gallbladder'] },
  { id: 'bladder', label: 'Urinary bladder', terms: ['bladder'] },
  { id: 'ureters', label: 'Ureters', terms: ['ureters', 'ureter'] },
  { id: 'adrenals', label: 'Adrenal glands', terms: ['adrenal', 'adrenals'] },
  { id: 'pituitary', label: 'Pituitary gland', terms: ['pituitary'] },
  { id: 'esophagus', label: 'Esophagus', terms: ['esophagus', 'oesophagus'] },
  { id: 'salivary-glands', label: 'Salivary glands', terms: ['salivary'] },
  { id: 'rectum', label: 'Rectum', terms: ['rectum'] },
  { id: 'tongue', label: 'Tongue', terms: ['tongue'] },
  { id: 'diaphragm', label: 'Diaphragm', terms: ['diaphragm'] },
  { id: 'spine', label: 'Spine', terms: ['spine', 'back', 'vertebrae'] },
  { id: 'skull', label: 'Skull & jaw', terms: ['skull', 'jaw'] },
  { id: 'teeth', label: 'Teeth', terms: ['teeth', 'tooth', 'dental'] },
  { id: 'rib-cage', label: 'Rib cage', terms: ['ribs', 'rib cage', 'sternum'] },
  { id: 'pelvis', label: 'Pelvis', terms: ['pelvis', 'hip', 'hips'] },
  { id: 'arm-bones', label: 'Shoulders & arms', terms: ['arms', 'arm', 'shoulder', 'shoulders'] },
];

const publications: Record<string, { id: string; title: string; caption: string; description: string }[]> = {
  fabry: [{ id: '39273698', title: 'Establishing Treatment Effectiveness in Fabry Disease: Observation-Based Recommendations for Improvement', caption: 'Review · 2024', description: 'Explores why study design, patient differences, and follow-up duration make long-term treatment effects difficult to assess.' }],
  pompe: [
    { id: '36969713', title: 'Pompe Disease: a Clinical, Diagnostic, and Therapeutic Overview', caption: 'Review', description: 'An overview of clinical presentation, diagnosis, treatment research, and unmet needs in Pompe disease.' },
    { id: '37680303', title: 'Monitoring and Management of Respiratory Function in Pompe Disease: Current Perspectives', caption: 'Review · 2023', description: 'A starting point for exploring respiratory involvement and its monitoring in Pompe disease.' },
  ],
  cmt: [{ id: '26457477', title: 'Charcot-Marie-Tooth 1A: A narrative review with clinical and anatomical perspectives', caption: 'Review · 2016', description: 'Connects the anatomical features of CMT1A in the hands, feet, and legs with its clinical and genetic context.' }],
  eds: [{ id: '32732924', title: 'The Ehlers-Danlos syndromes', caption: 'Review · 2020', description: 'Introduces the varied connective tissue features and molecular background of the Ehlers-Danlos syndromes.' }],
  marfan: [{ id: '34475413', title: 'Marfan syndrome', caption: 'Disease primer · 2021', description: 'A disease primer to explore the clinical and genetic context of Marfan syndrome.' }],
  huntington: [{ id: '25432725', title: 'Huntington disease: pathogenesis and treatment', caption: 'Review · 2015', description: 'A historical review connecting disease mechanisms with the treatment research discussed at publication.' }],
  sma: [{ id: '26515624', title: 'Spinal Muscular Atrophy', caption: 'Review · 2015', description: 'A historical overview of motor neuron involvement and the roles of SMN1 and SMN2. Read in the context of its publication date.' }],
  rett: [{ id: '39511247', title: 'Rett syndrome', caption: 'Disease primer · 2024', description: 'Reviews the clinical features of Rett syndrome, MECP2 biology, and research models.' }],
};

export function atlasContextForTarget(target: string, label?: string): AtlasContext {
  const area = areas.find(area => area.id === (relatedAreas[target] ?? target));
  return { target, label: label ?? area?.label ?? target.replaceAll('-', ' '), diseaseIds: area?.diseases ?? [] };
}

export function atlasContextForDisease(disease: Disease, current?: AtlasContext): AtlasContext {
  const context = current?.diseaseIds.includes(disease.id) ? current : atlasContextForTarget(disease.region);
  return { ...context, diseaseId: disease.id };
}

export function atlasResources(diseaseId: string): AtlasResource[] {
  const disease = diseases.find(d => d.id === diseaseId);
  if (!disease) return [];
  return [
    ...(publications[diseaseId] ?? []).map(paper => ({ ...paper, id: `paper-${paper.id}`, kind: 'paper' as const, url: `https://pubmed.ncbi.nlm.nih.gov/${paper.id}/`, diseaseId })),
    ...disease.genes.map(gene => ({ id: `gene-${gene}`, kind: 'gene' as const, title: gene, caption: 'Associated gene', description: `${gene} is included in the atlas record for ${disease.name}. Gene associations depend on the condition and subtype.`, url: `https://www.ncbi.nlm.nih.gov/gene/?term=${encodeURIComponent(`${gene}[Gene Name] AND Homo sapiens[Organism]`)}`, diseaseId })),
    { id: `reference-${diseaseId}`, kind: 'reference', title: disease.name, caption: 'MedlinePlus Genetics', description: disease.summary, url: disease.source, diseaseId },
  ];
}

const normalize = (text: string) => text.toLowerCase().replace(/[’']/g, '').replace(/[^a-z0-9]+/g, ' ').trim();
const includesPhrase = (query: string, phrase: string) => ` ${query} `.includes(` ${normalize(phrase)} `);

export function discoverAtlas(query: string, current?: AtlasContext, selectedResource?: AtlasResource | null): AtlasReply {
  const normalized = normalize(query);
  const named = diseases.filter(d => [d.id, d.name, d.shortName, ...d.genes, ...(d.id === 'huntington' ? ['huntingtons'] : [])].some(term => includesPhrase(normalized, term)));
  // A condition name can itself contain an anatomical word (e.g. muscular or
  // tooth). Only interpret body words outside the recognized condition name.
  const bodyQuery = named.reduce((text, disease) => [disease.name, disease.shortName].reduce((value, name) => value.replace(normalize(name), ''), text), normalized);
  const area = [...organTerms, ...areas].find(area => area.terms.some(term => includesPhrase(bodyQuery, term)));
  const symptomMatches = diseases.filter(d => d.symptoms.some(symptom => includesPhrase(normalized, symptom)));
  const explicit = named.length ? named : symptomMatches;
  const followUpWords = new Set('a an the and or of for to in on with about me you please can could would will what which how why is are does do show find explain explore tell help understand compare more related linked connected connection connections it its this that these those them their disease diseases condition conditions paper papers research evidence gene genes genetics source sources mechanism mechanisms latest recent studies study treatment treatments trial trials'.split(' '));
  const followUp = normalized.split(' ').every(word => followUpWords.has(word));
  let context: AtlasContext | undefined;
  if (explicit.length) {
    const commonArea = explicit.length > 1 ? areas.find(area => explicit.every(d => area.diseases.includes(d.id))) : undefined;
    const base = area ? atlasContextForTarget(area.id, area.label) : explicit.length === 1 ? atlasContextForDisease(explicit[0], current) : commonArea ? atlasContextForTarget(commonArea.id) : { target: 'body', label: 'Whole body', diseaseIds: explicit.map(d => d.id) };
    const linked = explicit.filter(d => !area || base.diseaseIds.includes(d.id));
    context = { ...base, diseaseIds: linked.map(d => d.id), diseaseId: linked[0]?.id };
  } else if (area) context = atlasContextForTarget(area.id, area.label);
  else if (followUp && current) context = current;
  else if (/\b(whole body|all conditions|all diseases)\b/.test(normalized)) context = { target: 'body', label: 'Whole body', diseaseIds: diseases.map(d => d.id) };

  if (!context) return {
    text: 'That topic isn’t in this demo collection yet. Try a body part, a condition such as Fabry or Pompe, or a gene such as MECP2. You can also select a part of the body to start exploring.',
    resources: [], prompts: ['Explore Fabry disease', 'Show research linked to the heart', 'Tell me about Rett syndrome'],
  };
  const linked = context.diseaseIds.map(id => diseases.find(d => d.id === id)!).filter(Boolean);
  if (!linked.length) return { context, text: `I’ve focused on ${context.label.toLowerCase()}. There are no linked condition or paper records for this selection in the demo collection yet. This is a gap in this preview, rather than an absence of research.`, resources: [], prompts: ['Show research linked to the heart', 'Explore Pompe disease'] };
  const selectedDiseaseId = context.diseaseId;
  const active = linked.find(d => d.id === selectedDiseaseId) ?? linked[0];
  context = { ...context, diseaseId: active.id };
  const subjects = !explicit.length && !area && current?.diseaseId ? linked.filter(d => d.id === current.diseaseId) : linked;
  const resources = subjects.flatMap(d => atlasResources(d.id));
  let text = explicit.length === 1 ? `${active.summary}\n\nI’ve connected ${active.shortName} to ${context.label.toLowerCase()}, its associated genes, and a reading list below.` : `I’ve connected ${context.label.toLowerCase()} with ${linked.map(d => d.shortName).join(', ')}. Select a condition on the body or ask a follow-up to explore its papers, genes, and reference records.`;
  if (selectedResource && !area && !explicit.length && /\b(this paper|this source|this gene)\b/.test(normalized)) text = `${selectedResource.title}\n\n${selectedResource.description}\n\nOpen the original source for its full context. This preview provides a reading guide, rather than a full-paper analysis.`;
  else if (/\b(genes?|genetics|mechanism)\b/.test(normalized)) text = subjects.map(d => `${d.shortName} · ${d.genes.join(', ')}\n${d.mechanism}`).join('\n\n');
  else if (/\b(papers?|research|evidence|sources?)\b/.test(normalized)) text = `Here’s the sample reading list connected to ${explicit.length || subjects.length === 1 ? subjects.map(d => d.shortName).join(' and ') : context.label.toLowerCase()}. Open the sources below to read the original papers and condition references. These are selected examples, not a live or comprehensive search.`;
  return { context, text, resources, prompts: [`Show papers about ${active.shortName}`, `Explain the genes linked to ${active.shortName}`] };
}
