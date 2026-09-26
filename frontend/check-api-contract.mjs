/* ============================================================
   check-api-contract.mjs —— 前端调用路径 vs 后端契约 一致性检查
   ------------------------------------------------------------
   为什么需要它：前端拼错一个接口路径（`/pesticide/usage` 写成
   `/pesticide/usages`），这里没有 TypeScript 兜底，只有运行到那一步
   才会 404 —— 而往往是在演示的时候才发现。

   这个脚本把前端代码里所有 `api.get/post/patch/del/req('...')` 的
   路径抠出来，与后端 /openapi.json 的 paths 做两项比对：
     1. 路径是否存在
     2. 该路径是否支持这个 HTTP 方法

   用法（后端需在跑）：
     node frontend/check-api-contract.mjs
     node frontend/check-api-contract.mjs http://127.0.0.1:8001
   ============================================================ */
import { readFileSync, readdirSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

import { blankComments } from './tools/js-lex.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const ORIGIN = process.argv[2] || 'http://127.0.0.1:8001';

/** 递归收集 js 文件（排除本脚本自身） */
function walk(dir, out = []) {
  for (const name of readdirSync(dir, { withFileTypes: true })) {
    if (name.name === 'tools' || name.name === 'node_modules') continue;
    const p = join(dir, name.name);
    if (name.isDirectory()) out.push(...walk(p));
    else if (name.name.endsWith('.js')) out.push(p);
  }
  return out;
}

const isQuote = (c) => c === "'" || c === '"' || c === '`';

/**
 * 从 src[start]（紧跟在左括号之后）开始，切出这次调用的实参列表。
 * 需要真的做括号/引号配对，不能靠正则 —— 实参里常出现嵌套的
 * 对象字面量与函数调用，正则一定会把 `,` 切错位置。
 */
function splitArgs(src, start) {
  const args = [];
  let cur = '';
  let depth = 0;
  let quote = null;
  let i = start;
  while (i < src.length) {
    const c = src[i];
    if (quote) {
      cur += c;
      if (c === '\\') { cur += src[i + 1] ?? ''; i += 2; continue; }
      if (c === quote) quote = null;
      i++;
      continue;
    }
    if (isQuote(c)) { quote = c; cur += c; i++; continue; }
    if (c === '(' || c === '[' || c === '{') { depth++; cur += c; i++; continue; }
    if (c === ')' || c === ']' || c === '}') {
      if (depth === 0) { args.push(cur); return args; }   // 整个调用的右括号
      depth--; cur += c; i++; continue;
    }
    if (c === ',' && depth === 0) { args.push(cur); cur = ''; i++; continue; }
    cur += c;
    i++;
  }
  return null;
}

/** 按顶层 `+` 拆分表达式 */
function splitPlus(expr) {
  const parts = [];
  let cur = '';
  let depth = 0;
  let quote = null;
  let i = 0;
  while (i < expr.length) {
    const c = expr[i];
    if (quote) {
      cur += c;
      if (c === '\\') { cur += expr[i + 1] ?? ''; i += 2; continue; }
      if (c === quote) quote = null;
      i++;
      continue;
    }
    if (isQuote(c)) { quote = c; cur += c; i++; continue; }
    if (c === '(' || c === '[' || c === '{') depth++;
    if (c === ')' || c === ']' || c === '}') depth--;
    if (c === '+' && depth === 0) { parts.push(cur); cur = ''; i++; continue; }
    cur += c;
    i++;
  }
  parts.push(cur);
  return parts.map((s) => s.trim()).filter((s) => s.length);
}

/**
 * 把「第一个实参表达式」还原成路径模式（下面用 <p> 表示路径参数占位）：
 *   '/farm/farms/' + id            → /farm/farms/<p>
 *   '/tasks/' + id + '/done'       → /tasks/<p>/done
 *   '/tasks/' + id + '/done' + api.qs({...})  → /tasks/<p>/done（query 丢掉）
 *   `/diseases/${id}`              → /diseases/<p>
 * 首段不是以 / 开头的字符串字面量 → 返回 null（说明不是绝对路径，跳过）
 *
 * ★ 上面几行原本写成真实通配符的样子，结果块注释被里面的星号加斜杠
 *   提前闭合、脚本当场语法报错 —— 这已经是我第三次栽在这个符号组合上。
 *   所以这里用 <p> 代替，注释里一律不出现那两个字符。
 */
function exprToPath(expr) {
  const parts = splitPlus(expr);
  if (!parts.length) return null;

  // 首段必须是字符串/模板字面量，否则这个实参是变量，无法静态判断
  const head = parts[0];
  if (!isQuote(head[0])) return null;

  let path = '';
  for (const p of parts) {
    const q = p[0];
    if (q === "'" || q === '"') {
      // 普通字符串字面量：去掉首尾引号，做最小反转义
      const body = p.slice(1, -1);
      path += body.replace(/\\(['"\\`])/g, '$1');
    } else if (q === '`') {
      // 模板串：${...} 一律当作路径参数
      const body = p.slice(1, -1);
      path += body.replace(/\$\{[^}]*\}/g, '*');
    } else if (/^api\.qs\s*\(/.test(p)) {
      // api.qs(...) 产出的是查询串，不属于路径
      continue;
    } else {
      // 变量 / 表达式 → 路径参数
      path += '*';
    }
  }
  if (!path.startsWith('/')) return null;
  return path;
}

/** 归一化：去查询串、{xxx}→*、折叠重复 *、去尾斜杠 */
function normalize(p) {
  return p
    .replace(/\?.*$/, '')
    .replace(/\{[^}]*\}/g, '*')
    .replace(/\*{2,}/g, '*')
    .replace(/\/+$/, '');
}

/** Levenshtein 距离，用来在报错时推荐「最接近的后端路径」 */
function editDistance(a, b) {
  const m = a.length;
  const n = b.length;
  if (!m) return n;
  if (!n) return m;
  let prev = Array.from({ length: n + 1 }, (_, j) => j);
  for (let i = 1; i <= m; i++) {
    const cur = [i];
    for (let j = 1; j <= n; j++) {
      cur[j] = Math.min(
        prev[j] + 1,
        cur[j - 1] + 1,
        prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
    }
    prev = cur;
  }
  return prev[n];
}

const METHOD_OF = { get: 'GET', post: 'POST', patch: 'PATCH', del: 'DELETE' };
const CALL_RE = /\bapi\.(get|post|patch|del|req)\s*\(/g;

/* ---------------- 拉后端契约 ---------------- */
const spec = await fetch(ORIGIN + '/openapi.json').then((r) => r.json());
const paths = Object.keys(spec.paths || {});
if (!paths.length) {
  console.error('后端没有返回任何路径，确认服务是否正常');
  process.exit(1);
}

/* openapi 的路径形如 /api/v1/farm/farms/{farm_id}；前端写的是 /farm/farms/{id}
   比对时统一剥掉 /api/v1 前缀，并把 {xxx} 归一成 *  */
const stripPrefix = (p) => p.replace(/^\/api\/v1/, '');
const known = new Map(); // 归一化路径 -> { raw, methods:Set }
for (const p of paths) {
  const key = normalize(stripPrefix(p));
  const methods = new Set(
    Object.keys(spec.paths[p]).map((m) => m.toUpperCase()));
  const prev = known.get(key);
  if (prev) methods.forEach((m) => prev.methods.add(m));
  else known.set(key, { raw: stripPrefix(p), methods });
}

/* ---------------- 扫描前端 ---------------- */
const problems = [];
let checked = 0;

for (const file of walk(HERE)) {
  /* ★ 先剥注释：注释里写的 api.get('/x') 示例不该被当成真实调用。
     blankComments 是等长替换，所以下面的行号/偏移都还成立。 */
  const src = blankComments(readFileSync(file, 'utf8'));
  const lines = src.split('\n');
  const lineAt = (idx) => src.slice(0, idx).split('\n').length;

  const re = new RegExp(CALL_RE.source, 'g');
  let m;
  while ((m = re.exec(src))) {
    const fn = m[1];
    const args = splitArgs(src, m.index + m[0].length);
    if (!args) continue;

    let method;
    let expr;
    if (fn === 'req') {
      const mArg = (args[0] || '').trim().match(/^['"](\w+)['"]$/);
      if (!mArg) continue;                 // 方法不是字面量，跳过
      method = mArg[1].toUpperCase();
      expr = args[1] || '';
    } else {
      method = METHOD_OF[fn];
      expr = args[0] || '';
    }

    const rawPath = exprToPath(expr);
    if (!rawPath) continue;
    const path = normalize(rawPath);
    if (!path.startsWith('/')) continue;

    checked++;
    const hit = known.get(path);
    const line = lineAt(m.index);
    if (hit) {
      if (!hit.methods.has(method)) {
        problems.push({
          kind: 'method',
          file: file.replace(HERE, 'frontend'),
          line,
          method,
          path,
          detail: `后端这条路径只支持 ${[...hit.methods].join(' / ')}`,
          source: lines[line - 1].trim()
        });
      }
      continue;
    }

    // 路径不存在：找出最接近的一条，方便直接改。
    // ★ 打分规则踩过坑：最初把「通配段」也算命中，阈值放到 段数-1，
    //   结果 `/pesticide/usages` 会建议出 `/diagnosis/{diagnosis_id}`
    //   （只因为后者第二段是通配）—— 这种建议比不给还糟。
    //   现在要求首段（资源根，如 /pesticide）必须一致，再按编辑距离取最近。
    const seg = path.split('/').filter(Boolean);
    let best = null;
    let bestDist = Infinity;
    for (const k of known.keys()) {
      const ks = k.split('/').filter(Boolean);
      if (!ks.length || ks[0] !== seg[0]) continue;
      const d = editDistance(k, path);
      if (d < bestDist) { bestDist = d; best = k; }
    }
    const closeEnough = best && bestDist <= Math.max(3, Math.ceil(path.length * 0.34));
    problems.push({
      kind: 'path',
      file: file.replace(HERE, 'frontend'),
      line,
      method,
      path,
      detail: closeEnough
        ? `后端最接近的是 ${known.get(best).raw}`
        : '后端没有相近的路径',
      source: lines[line - 1].trim()
    });
  }
}

console.log(`后端路径 ${paths.length} 条，前端调用点 ${checked} 个`);

/* ★ 扫到 0 个调用点必须当成失败。
   否则「目录传错 / 正则失效 / 前端换了请求封装」都会输出
   「全部匹配」，给出虚假的安全感 —— 一个永远通过的检查器等于没有检查器。 */
if (checked === 0) {
  console.error('\n扫描到 0 个调用点，无法判断契约是否一致。');
  console.error('请确认：目录是否正确、前端是否仍用 api.get/post/patch/del/req 封装。');
  process.exit(2);
}

if (!problems.length) {
  console.log('全部匹配：路径存在，且 HTTP 方法一致');
  process.exit(0);
}

const pathIssues = problems.filter((p) => p.kind === 'path');
const methodIssues = problems.filter((p) => p.kind === 'method');
console.log(`\n发现 ${problems.length} 处问题`
  + `（路径不存在 ${pathIssues.length}，方法不匹配 ${methodIssues.length}）：\n`);
for (const p of problems) {
  const tag = p.kind === 'path' ? '[路径]' : '[方法]';
  console.log(`  ${tag} ${p.file}:${p.line}  ${p.method} ${p.path}`);
  console.log(`      ${p.source}`);
  console.log(`      ${p.detail}`);
  console.log('');
}
process.exit(1);
