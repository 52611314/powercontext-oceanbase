const shots = {
  library: {
    src: 'assets/library.webp',
    url: 'citeseek / 文献库',
    alt: 'CiteSeek 文献库：3 篇示例资料、6 页、20 个文本块，列表包含标签、索引进度与阅读状态。',
    caption:
      '文献库：拖拽导入后自动解析、切块并建立索引；去重、标签、阅读状态和重建都在同一张表里。',
  },
  search: {
    src: 'assets/search.webp',
    url: 'citeseek / 混合检索',
    alt: 'CiteSeek 混合检索：B+ 树查询同时命中术语与语义段落，显示全文与向量两路排名、RRF 分数与命中高亮。',
    caption:
      '混合检索：一个 B+ 树问题同时命中术语与范围查询段落；每张卡片都标出两路排名、RRF 分数与命中位置。',
  },
  evidence: {
    src: 'assets/evidence.webp',
    url: 'citeseek / 检索证据',
    alt: 'seekdb 证据抽屉：显示检索模式、返回候选、全文与向量索引命中状态，以及实际执行的混合检索 SQL。',
    caption:
      'seekdb 证据：本次检索的真实 SQL、过滤条件与 EXPLAIN 都在界面里；全文和向量两个索引均显示命中。',
  },
  reader: {
    src: 'assets/reader.webp',
    url: 'citeseek / 原文定位',
    alt: '原文定位抽屉：展示数据库索引笔记第一页，引用句子被高亮并自动滚动到视野中央。',
    caption:
      '原文定位：从结果跳到对应页，自动滚动到引用句并高亮；页码、章节与原始 PDF 入口保持一致。',
  },
  ask: {
    src: 'assets/ask.webp',
    url: 'citeseek / 带引用问答',
    alt: '带引用问答：KV cache 显存估算问题的三条结论，每条都标注文献、页码与章节。',
    caption:
      '带引用问答：不调用大模型，每条结论直接来自原文句子，并标出文献、页码与章节，点击即可回看。',
  },
};

const tabs = [...document.querySelectorAll('[data-shot]')];
const image = document.getElementById('shot-image');
const caption = document.getElementById('shot-caption');
const shotUrl = document.getElementById('shot-url');
const shotOpen = document.getElementById('shot-open');
const panel = document.getElementById('shot-panel');

function select(name, focus = false) {
  const shot = shots[name];
  const tab = tabs.find((item) => item.dataset.shot === name);
  if (!shot || !tab) return;

  for (const item of tabs) {
    const active = item === tab;
    item.setAttribute('aria-selected', String(active));
    item.tabIndex = active ? 0 : -1;
  }
  if (focus) tab.focus();

  image.src = shot.src;
  image.alt = shot.alt;
  caption.textContent = shot.caption;
  shotOpen.href = shot.src;
  shotUrl.textContent = shot.url;
  panel.setAttribute('aria-labelledby', tab.id);
}

for (const tab of tabs) {
  tab.addEventListener('click', () => select(tab.dataset.shot));
  tab.addEventListener('keydown', (event) => {
    const index = tabs.indexOf(tab);
    let next = null;
    if (event.key === 'ArrowRight') next = tabs[(index + 1) % tabs.length];
    if (event.key === 'ArrowLeft') next = tabs[(index - 1 + tabs.length) % tabs.length];
    if (event.key === 'Home') next = tabs[0];
    if (event.key === 'End') next = tabs[tabs.length - 1];
    if (!next) return;
    event.preventDefault();
    select(next.dataset.shot, true);
  });
}

caption.textContent = shots.search.caption;

for (const tab of tabs) {
  tab.tabIndex = tab.getAttribute('aria-selected') === 'true' ? 0 : -1;
}

const preload = () => {
  for (const shot of Object.values(shots)) {
    const img = new Image();
    img.src = shot.src;
  }
};

if ('requestIdleCallback' in window) {
  window.requestIdleCallback(preload);
} else {
  window.addEventListener('load', preload);
}
