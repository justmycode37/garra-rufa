import type { Root, Element, Text } from 'hast';

// Stable word spans animate only as they enter the DOM. Existing paragraphs
// retain their opacity while Markdown continues to update around new text.
export function answerWordReveal() {
  return (tree: Root) => {
    const visit = (node: Root | Element) => {
      if (node.type === 'element' && ['pre', 'code'].includes(node.tagName)) return;
      const children: Root['children'] = [];
      for (const child of node.children) {
        if (child.type === 'element') { visit(child); children.push(child); continue; }
        if (child.type !== 'text') { children.push(child); continue; }
        children.push(...(child.value.match(/\S+\s*|\s+/g) || []).map(value => ({
          type: 'element', tagName: 'span', properties: { className: ['answer-word'] }, children: [{ type: 'text', value } as Text],
        } as Element)));
      }
      node.children = children as typeof node.children;
    };
    visit(tree);
  };
}
