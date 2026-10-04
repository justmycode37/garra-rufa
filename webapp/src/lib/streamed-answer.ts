// Decode only the top-level answer string from the structured response. Keep
// JSON escapes and split surrogate pairs buffered until they can be displayed.
export function createAnswerDecoder() {
  let depth = 0, inString = false, escaped = false, unicode: string | null = null;
  let expectingKey = false, key = '', token = '', pending = '', finished = false;
  let kind: 'key' | 'answer' | 'skip' = 'skip';
  const append = (value: string) => {
    if (kind === 'key') token += value;
    if (kind === 'answer') pending += value;
  };
  return (chunk: string) => {
    for (const char of chunk) {
      if (finished) break;
      if (inString) {
        if (unicode !== null) {
          unicode += char;
          if (unicode.length === 4) {
            if (!/^[\da-f]{4}$/i.test(unicode)) throw new Error('Invalid response text.');
            append(String.fromCharCode(parseInt(unicode, 16)));
            unicode = null;
          }
        } else if (escaped) {
          escaped = false;
          if (char === 'u') unicode = '';
          else {
            const value = ({ '"': '"', '\\': '\\', '/': '/', b: '\b', f: '\f', n: '\n', r: '\r', t: '\t' } as Record<string, string>)[char];
            if (value === undefined) throw new Error('Invalid response text.');
            append(value);
          }
        } else if (char === '\\') escaped = true;
        else if (char === '"') {
          inString = false;
          if (kind === 'key') key = token;
          if (kind === 'answer') finished = true;
        } else append(char);
      } else if (char === '"') {
        inString = true;
        token = '';
        kind = depth === 1 ? expectingKey ? 'key' : key === 'answer' ? 'answer' : 'skip' : 'skip';
      } else if (char === '{' || char === '[') {
        depth++;
        if (depth === 1) expectingKey = true;
      } else if (char === '}' || char === ']') depth--;
      else if (depth === 1 && char === ',') { expectingKey = true; key = ''; }
      else if (depth === 1 && char === ':') expectingKey = false;
    }
    const hold = !finished && /[\uD800-\uDBFF]$/.test(pending) ? pending.slice(-1) : '';
    const delta = hold ? pending.slice(0, -1) : pending;
    pending = hold;
    return delta;
  };
}
