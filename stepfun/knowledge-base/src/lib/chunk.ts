/** 文本分块：按字符切分，保留重叠，避免截断代理对。 */
export function chunkText(text: string, size = 400, overlap = 50): string[] {
  const clean = text.replace(/\r\n/g, '\n').trim();
  if (!clean) return [];
  const chunks: string[] = [];
  let start = 0;
  while (start < clean.length) {
    let end = Math.min(start + size, clean.length);
    // 避免在 UTF-16 代理对中间截断
    if (end < clean.length && /[\uD800-\uDBFF]/.test(clean[end - 1])) end -= 1;
    const piece = clean.slice(start, end).trim();
    if (piece) chunks.push(piece);
    if (end >= clean.length) break;
    start = Math.max(end - overlap, start + 1);
  }
  return chunks;
}

/** 把检索结果里命中查询词/字的部分用 <mark> 包起来。 */
export function highlight(text: string, query: string): Array<{ text: string; hit: boolean }> {
  const terms = [...new Set(
    query
      .split(/[\s,，。;；、]+/)
      .map((t) => t.trim())
      .filter((t) => t.length >= 2),
  )];
  if (terms.length === 0) return [{ text, hit: false }];
  const pattern = new RegExp(`(${terms.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})`, 'g');
  const parts: Array<{ text: string; hit: boolean }> = [];
  let last = 0;
  for (const match of text.matchAll(pattern)) {
    const index = match.index ?? 0;
    if (index > last) parts.push({ text: text.slice(last, index), hit: false });
    parts.push({ text: match[0], hit: true });
    last = index + match[0].length;
  }
  if (last < text.length) parts.push({ text: text.slice(last), hit: false });
  return parts;
}
