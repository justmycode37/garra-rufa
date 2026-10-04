import type { Disease, Region, Source } from './types';

// Curated discovery records. Associations are condition-level, never a diagnosis
// or a claim that the same intervention works across related conditions.
export const diseases: Disease[] = [
  { id:'cmt', name:'Charcot-Marie-Tooth disease', shortName:'Charcot-Marie-Tooth', category:'Peripheral nerves', region:'hands', regions:['hands','legs'], genes:['PMP22','MPZ','MFN2','GJB1'], summary:'A group of inherited peripheral nerve disorders that can affect muscle strength and sensation in the feet, legs, and hands.', symptoms:['hand weakness','foot drop','high arches','sensory loss','nerve pain'], mechanism:'Different subtypes affect the myelin sheath or the nerve axon. Gene and variant context matter.', source:'https://medlineplus.gov/genetics/condition/charcot-marie-tooth-disease/', color:'sea', organization:{name:'Charcot-Marie-Tooth Association',url:'https://www.cmtausa.org/'}, updated:'2026-10-03' },
  { id:'eds', name:'Ehlers-Danlos syndromes', shortName:'Ehlers-Danlos', category:'Connective tissue', region:'hands', regions:['hands','legs','body'], genes:['COL5A1','COL5A2','COL3A1'], summary:'A group of conditions affecting connective tissue, with features that can include joint hypermobility and changes in skin strength or elasticity.', symptoms:['joint hypermobility','joint pain','skin fragility','easy bruising'], mechanism:'Many subtypes involve collagen or related tissue processes. The common hypermobile subtype does not have a single established genetic cause.', source:'https://medlineplus.gov/genetics/condition/ehlers-danlos-syndrome/', color:'lilac', organization:{name:'The Ehlers-Danlos Society',url:'https://www.ehlers-danlos.com/'}, updated:'2026-10-03' },
  { id:'fabry', name:'Fabry disease', shortName:'Fabry', category:'Lysosomal storage', region:'hands', regions:['hands','heart','body'], genes:['GLA'], summary:'An inherited condition in which deficient alpha-galactosidase A activity leads to accumulation of certain lipids in cells.', symptoms:['burning hands and feet','pain episodes','reduced sweating','kidney involvement','heart involvement'], mechanism:'Reduced activity of a lysosomal enzyme causes accumulation of globotriaosylceramide and related substances.', source:'https://medlineplus.gov/genetics/condition/fabry-disease/', color:'peach', organization:{name:'National Fabry Disease Foundation',url:'https://www.fabrydisease.org/'}, updated:'2026-10-03' },
  { id:'marfan', name:'Marfan syndrome', shortName:'Marfan', category:'Connective tissue', region:'heart', regions:['heart','hands','body'], genes:['FBN1'], summary:'A connective tissue condition that can affect the skeleton, eyes, and cardiovascular system, including the aorta.', symptoms:['long fingers','tall stature','lens displacement','aortic enlargement'], mechanism:'Changes in fibrillin-1 affect the structure and function of connective tissue.', source:'https://medlineplus.gov/genetics/condition/marfan-syndrome/', color:'butter', organization:{name:'The Marfan Foundation',url:'https://marfan.org/'}, updated:'2026-10-03' },
  { id:'huntington', name:'Huntington disease', shortName:'Huntington', category:'Neurodegeneration', region:'brain', regions:['brain'], genes:['HTT'], summary:'An inherited brain disorder involving progressive changes in movement, cognition, and emotional function.', symptoms:['involuntary movements','cognitive changes','coordination difficulties'], mechanism:'An expanded CAG repeat in HTT alters the huntingtin protein. Repeat length and clinical context require specialist interpretation.', source:'https://medlineplus.gov/genetics/condition/huntingtons-disease/', color:'lilac', organization:{name:'Huntington’s Disease Society of America',url:'https://hdsa.org/'}, updated:'2026-10-03' },
  { id:'sma', name:'Spinal muscular atrophy', shortName:'Spinal muscular atrophy', category:'Motor neurons', region:'muscles', regions:['muscles','legs'], genes:['SMN1','SMN2'], summary:'A group of conditions involving loss of motor neurons and resulting muscle weakness. The common 5q form is associated with SMN1.', symptoms:['muscle weakness','low muscle tone','motor difficulties','breathing difficulties'], mechanism:'In 5q SMA, insufficient survival motor neuron protein impairs motor neuron maintenance. SMN2 copy number can modify severity.', source:'https://medlineplus.gov/genetics/condition/spinal-muscular-atrophy/', color:'sea', organization:{name:'Cure SMA',url:'https://www.curesma.org/'}, updated:'2026-10-03' },
  { id:'pompe', name:'Pompe disease', shortName:'Pompe', category:'Lysosomal storage', region:'muscles', regions:['muscles','heart','legs'], genes:['GAA'], summary:'An inherited metabolic condition in which glycogen accumulates in cells, particularly affecting muscle function.', symptoms:['muscle weakness','respiratory weakness','cardiac involvement'], mechanism:'Deficient acid alpha-glucosidase activity disrupts breakdown of glycogen in lysosomes.', source:'https://medlineplus.gov/genetics/condition/pompe-disease/', color:'peach', organization:{name:'International Pompe Association',url:'https://worldpompe.org/'}, updated:'2026-10-03' },
  { id:'rett', name:'Rett syndrome', shortName:'Rett', category:'Neurodevelopment', region:'brain', regions:['brain','hands'], genes:['MECP2'], summary:'A neurodevelopmental condition that can involve loss of acquired skills, repetitive hand movements, and difficulties with communication and movement.', symptoms:['loss of acquired skills','repetitive hand movements','communication difficulties','seizures'], mechanism:'Most cases involve pathogenic MECP2 variants affecting regulation of gene activity in the nervous system.', source:'https://medlineplus.gov/genetics/condition/rett-syndrome/', color:'butter', organization:{name:'International Rett Syndrome Foundation',url:'https://www.rettsyndrome.org/'}, updated:'2026-10-03' },
];

export const regions: {id:Region;label:string;description:string}[] = [
  {id:'body',label:'Whole body',description:'Explore the connections'},
  {id:'brain',label:'Brain & nerves',description:'Neurological connections'},
  {id:'heart',label:'Heart',description:'Cardiovascular connections'},
  {id:'hands',label:'Hands & joints',description:'Peripheral and connective tissue'},
  {id:'muscles',label:'Muscles',description:'Neuromuscular connections'},
  {id:'legs',label:'Legs & feet',description:'Movement and peripheral nerves'},
];

export function inferRegion(query:string):Region {
  const q=query.toLowerCase();
  if (/\b(hand|hands|finger|fingers|wrist|wrists|joint|joints)\b/.test(q)) return 'hands';
  if (/\b(brain|neuro|memory|cognitive|seizure|seizures|rett|huntington)\b/.test(q)) return 'brain';
  if (/\b(heart|cardiac|aorta|aortic|marfan|chest)\b/.test(q)) return 'heart';
  if (/\b(leg|legs|foot|feet|walking|ankle)\b/.test(q)) return 'legs';
  if (/\b(muscle|muscles|muscular|pompe|sma)\b/.test(q)) return 'muscles';
  return diseases.find(d=>q.includes(d.id)||q.includes(d.name.toLowerCase()))?.region || 'body';
}

export function searchDiseases(query:string, region?:Region):Disease[] {
  const tokens=query.toLowerCase().replace(/deseases/g,'diseases').split(/[^a-z0-9]+/).filter(t=>t.length>2&&!['the','and','have','this','with','what','about','show','find','disease','diseases','condition','conditions','rare','research','papers','studies','explain','for','can'].includes(t));
  const area=region||inferRegion(query);
  return diseases.map(d=>{
    const name=`${d.name} ${d.shortName} ${d.id} ${d.genes.join(' ')}`.toLowerCase();
    const haystack=`${name} ${d.summary} ${d.symptoms.join(' ')} ${d.category} ${d.mechanism}`.toLowerCase();
    const score=tokens.reduce((s,t)=>s+(name.includes(t)?8:haystack.includes(t)?2:0),0)+(area!=='body'&&d.regions.includes(area)?4:0);
    return {d,score};
  }).filter(x=>x.score>0||(!tokens.length&&area==='body')).sort((a,b)=>b.score-a.score).map(x=>x.d).slice(0,6);
}

export function diseaseSource(d:Disease):Source {
  return {id:d.id,title:`${d.name} · MedlinePlus Genetics`,url:d.source,kind:'reference',excerpt:`${d.summary} ${d.mechanism}`};
}

export function reasoningEffort(prompt:string,hasFile=false):'low'|'medium'|'high'|'max' {
  if(hasFile || /review|critique|systematic|methodology|paper|manuscript|meta.analysis|experimental design|causal|publish|hypothesis/i.test(prompt)||prompt.length>1500) return 'max';
  if(/compare|mechanism|pathway|treatment|trial|evidence|analy[sz]|research plan/i.test(prompt)||prompt.length>500) return 'high';
  if(prompt.length>120||/why|how|explain|relationship/i.test(prompt)) return 'medium';
  return 'low';
}
