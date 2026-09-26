/* ============================================================
   check-dom-ids.mjs —— DOM id 交叉核对
   ------------------------------------------------------------
   为什么需要它：前端 `document.getElementById('xxx')` 拼错 id 时
   **不报错**，只是拿到 null，然后在下一行 `.value` / `.hidden` 上抛
   TypeError —— 或者更糟：被 try/catch 吞掉，表现为「点了没反应」。
   这类问题只有点到那个页面才暴露，往往在演示时才发现。

   这个脚本做三件事：
     1. 收集 index.html 里的静态 id
     2. 收集 JS 模板字符串里动态创建的 id
     3. 收集 JS 里所有 getElementById / querySelector('#...') 引用
   然后报出「引用了但既不在 HTML 里、也没有任何地方创建」的 id。

   用法：
     node frontend/tools/check-dom-ids.mjs
   ============================================================ */
import { readFileSync, readdirSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

import { blankComments } from './js-lex.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, '..');

/** 递归收集 js 文件 */
function walk(dir, out = []) {
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    if (['tools', 'node_modules'].includes(e.name)) continue;
    const p = join(dir, e.name);
    if (e.isDirectory()) walk(p, out);
    else if (e.name.endsWith('.js')) out.push(p);
  }
  return out;
}

/* ---------------- 1. HTML 里的静态 id ---------------- */
const html = readFileSync(join(ROOT, 'index.html'), 'utf8');
const staticIds = new Set(
  [...html.matchAll(/\sid="([^"]+)"/g)].map((m) => m[1]));
/* 模板里出现的 id（例如 <template>），一并算作静态 */
for (const m of html.matchAll(/id='([^']+)'/g)) staticIds.add(m[1]);

/* ---------------- 2 & 3. 扫 JS ---------------- */
/** 从 JS 源码里抠出「创建了哪些 id」 */
function createdIds(src) {
  const out = new Set();
  /* 形如 id="foo" 或 id='foo'（在模板字符串/拼接串里） */
  for (const m of src.matchAll(/\bid=\\?["']([a-zA-Z][\w-]*)\\?["']/g)) {
    out.add(m[1]);
  }
  return out;
}

/** 从 JS 源码里抠出「引用了哪些 id」，带行号 */
function referencedIds(src) {
  const out = [];
  const push = (kind, id, index) => {
    const line = src.slice(0, index).split('\n').length;
    out.push({ kind, id, line });
  };
  /* document.getElementById('x') / $('#x') */
  for (const m of src.matchAll(/getElementById\(\s*['"]([^'"]+)['"]/g)) {
    push('getElementById', m[1], m.index);
  }
  /* querySelector('#x') / querySelectorAll('#x') */
  for (const m of src.matchAll(/querySelector(?:All)?\(\s*['"]#([\w-]+)['"]/g)) {
    push('querySelector', m[1], m.index);
  }
  /* 变量式的 getElementById 无法静态判断，单独统计出来提示 */
  return out;
}

const files = walk(ROOT);
const jsCreated = new Set();
const refs = [];

for (const file of files) {
  /* ★ 先剥注释再提取。行号必须保持对应，所以 stripComments 只做
     等量替换（把注释内容换成空格）而不是删除 —— 否则报出的行号会错位。 */
  const raw = readFileSync(file, 'utf8');
  const src = blankComments(raw);
  const rel = file.replace(ROOT, 'frontend').replace(/\\/g, '/');
  for (const id of createdIds(src)) jsCreated.add(id);
  for (const r of referencedIds(src)) refs.push({ ...r, file: rel });
}

/* ---------------- 汇总 ---------------- */
const problems = [];
const dynamicOnly = [];
const seen = new Set();

for (const r of refs) {
  const key = `${r.file}:${r.line}:${r.id}`;
  if (seen.has(key)) continue;
  seen.add(key);
  if (staticIds.has(r.id) || jsCreated.has(r.id)) {
    if (!staticIds.has(r.id)) dynamicOnly.push(r);
    continue;
  }
  problems.push(r);
}

console.log(`静态 id ${staticIds.size} 个（index.html）`);
console.log(`JS 动态创建 id ${jsCreated.size} 个`);
console.log(`DOM 引用 ${refs.length} 处（去重后 ${seen.size} 处）`);

/* 动态创建的 id 清单：这些靠「先渲染再取」才成立，值得让作者知道 */
if (process.argv.includes('-v') && dynamicOnly.length) {
  console.log(`\n以下 ${dynamicOnly.length} 处引用的是「JS 运行时才创建」的 id`
    + '（正常，但要求渲染在前）：');
  for (const r of dynamicOnly) {
    console.log(`  ${r.file}:${r.line}  ${r.kind}('#${r.id}')`);
  }
}

if (!problems.length) {
  console.log('\n全部匹配：没有引用了不存在的 id');
  process.exit(0);
}

console.log(`\n发现 ${problems.length} 处引用了不存在的 id：\n`);
for (const r of problems) {
  console.log(`  ${r.file}:${r.line}  ${r.kind}('#${r.id}')`);
  const near = [...staticIds, ...jsCreated].filter(
    (x) => x.toLowerCase().includes(r.id.toLowerCase())
      || r.id.toLowerCase().includes(x.toLowerCase()));
  if (near.length) console.log(`      相近的已有 id：${near.slice(0, 4).join(', ')}`);
  console.log('');
}
process.exit(1);
