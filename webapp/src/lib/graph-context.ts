import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { getGraphBuild, graphBuildMarkdown } from './graph-builds';
import { isBuildId, type ExampleEntry } from './graph-cache';

// The graph chat answers from the Markdown context the pipeline wrote for the open graph
// (src/query-test/chat_context.py: the whole graph ranked by importance, fitted to about
// 240k characters): built graphs come from the research service, the bundled examples
// from public/graph-data.

export type GraphView = 'present' | 'evidence';
export type GraphContext = { label: string; view: GraphView; markdown: string; truncated: boolean };

/** A safety cap a little above chat_context.py's budget (about 65k tokens). */
export const GRAPH_CONTEXT_CHARS = 260000;
const EXAMPLES = path.join(process.cwd(), 'public', 'graph-data');
const EXAMPLE_ID = /^[a-z0-9-]{1,60}$/;

async function example(id: string, view: GraphView) {
  const index = JSON.parse(await readFile(path.join(EXAMPLES, 'index.json'), 'utf8')) as (ExampleEntry & { presentMd?: string; evidenceMd?: string })[];
  const entry = index.find(e => e.id === id);
  const file = entry?.[view === 'present' ? 'presentMd' : 'evidenceMd'];
  if (!entry || !file || path.basename(file) !== file) return null;
  return { label: entry.label, markdown: await readFile(path.join(EXAMPLES, file), 'utf8') };
}

async function built(id: string, view: GraphView) {
  const build = await getGraphBuild(id);
  const markdown = build ? await graphBuildMarkdown(id, view) : null;
  return build && markdown ? { label: build.label, markdown } : null;
}

export async function graphContext(id: string, view: GraphView): Promise<GraphContext | null> {
  if (!EXAMPLE_ID.test(id)) return null;
  const found = await (isBuildId(id) ? built(id, view) : example(id, view).catch(() => null));
  if (!found) return null;
  const truncated = found.markdown.length > GRAPH_CONTEXT_CHARS;
  return { ...found, view, truncated, markdown: truncated ? found.markdown.slice(0, GRAPH_CONTEXT_CHARS) : found.markdown };
}
