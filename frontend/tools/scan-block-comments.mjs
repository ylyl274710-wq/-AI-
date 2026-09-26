/* ============================================================
   scan-block-comments.mjs —— 块注释配平检查
   ------------------------------------------------------------
   为什么需要它：块注释里只要出现「星号 + 斜杠」两个字符，
   注释就会被提前闭合。典型触发场景是注释里写了一个 glob 路径
   或正则（例如描述「把 /x/ 通配 /y 拼起来」这种语义），
   剩下的文字就变成了代码，报错位置往往在几十行之后，
   而且报的是「Unexpected token」这种和真实原因毫不相干的错。

   这个脚本统计每个 js 文件里「块注释开符」与「块注释闭符」的出现次数，
   不相等就报出来。比等 node --check 报错要快得多，
   而且能定位到「注释写坏了」这个真实原因。

   ★ 讽刺的是：我写这个文件的时候，在同一段说明里又把这两个符号
     原样写了一遍，于是注释当场被闭合、脚本自己语法报错。
     所以下面说明里一律用文字描述，不再写出那两个字符。
     这个坑的复发率就是这么高 —— 别在注释里秀符号。

   ★★ 第一版实现是「把字符串抹掉后直接数符号」，结果报了一个假问题：
     某行**行注释**里写了 `pages/` 后面跟通配符的路径，
     其中的「斜杠 + 星号」被当成了块注释开符 —— 但行注释里的它
     根本不开注释，代码是好的。
     假问题会让人去改本来正确的代码，比不报还糟。
     现在改为复用 js-lex.mjs 的状态机（它认字符串 / 正则 / 行注释 /
     块注释），不再自己数符号。

   用法：
     node frontend/tools/scan-block-comments.mjs
     node frontend/tools/scan-block-comments.mjs <目录>
   ============================================================ */
import { readFileSync, readdirSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { blockCommentStats } from './js-lex.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = process.argv[2] || join(HERE, '..', '..');

function walk(dir, out = []) {
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    if (['node_modules', '.git', '.venv', '__pycache__'].includes(e.name)) continue;
    const p = join(dir, e.name);
    if (e.isDirectory()) walk(p, out);
    else if (/\.(js|mjs|cjs)$/.test(e.name)) out.push(p);
  }
  return out;
}

/* 统计走 js-lex 的状态机，不再自己抹字符串数符号（见文件头说明） */

let bad = 0;
let total = 0;
const files = walk(ROOT);

for (const f of files) {
  const { opens, closes } = blockCommentStats(readFileSync(f, 'utf8'));
  total++;
  if (opens !== closes) {
    bad++;
    console.log(`[不配平] ${f}`);
    console.log(`         开 ${opens} 个，闭 ${closes} 个，差 ${opens - closes}`);
  }
}

console.log(`\n扫描 ${total} 个 js/mjs/cjs 文件，块注释不配平的 ${bad} 个`);
process.exit(bad ? 1 : 0);
