import test from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { Text } from '../src/components/Chat';

test('answers render Markdown headings, lists, emphasis, links, and tables', () => {
  const html = renderToStaticMarkup(createElement(Text, { text: '## Overview\n\nA **clear** answer with a [source](https://example.org).\n\n- First point\n- Second point\n\n| Topic | Detail |\n| --- | --- |\n| Evidence | Source |' }));
  assert.match(html, /<h2>Overview<\/h2>/);
  assert.match(html, /<strong>clear<\/strong>/);
  assert.match(html, /<ul>\s*<li>First point<\/li>\s*<li>Second point<\/li>\s*<\/ul>/);
  assert.match(html, /href="https:\/\/example.org"/);
  assert.match(html, /class="answer-table"><table>/);
});

test('answer content cannot execute embedded HTML or unsafe Markdown links', () => {
  const html = renderToStaticMarkup(createElement(Text, { text: '<script>alert(1)</script>\n\n[unsafe](javascript:alert%281%29)\n\n![untrusted image](https://example.org/tracker)' }));
  assert.doesNotMatch(html, /<script|javascript:|<img/);
  assert.match(html, /untrusted image/);
});
