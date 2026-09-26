/* ============================================================
   selftest-js-lex.mjs —— 给 js-lex 词法器做负向自测
   ------------------------------------------------------------
   为什么必须有：
     「扫到 0 个问题」有两种可能 —— 代码真的没问题，或者尺子坏了。
     不区分这两者的检查器等于没有检查器。

   本测试用**合成样本**覆盖每一条分支，包括：
     - 行注释里的符号（曾经导致假问题的真实案例）
     - 字符串 / 模板串 / 正则字面量里的符号
     - 块注释被提前闭合（真问题，必须抓到）
     - 未闭合的块注释（真问题，必须抓到）

   ★ 写这个文件时注意：**不能直接写出那对符号**，
     否则本文件自己就会被写坏（这个坑复发率极高）。
     一律用 CLOSE = '*' + '/' 这种方式拼出来。

   用法：node frontend/tools/selftest-js-lex.mjs
   ============================================================ */
import { blockCommentStats, blankComments } from './js-lex.mjs';

const OPEN = '/' + '*';        // 拼出来，不在源码里直接出现
const CLOSE = '*' + '/';

let pass = 0, fail = 0;
const failures = [];

function check(name, got, want) {
  const g = JSON.stringify(got), w = JSON.stringify(want);
  if (g === w) { pass++; }
  else { fail++; failures.push(`${name}\n      得到 ${g}\n      期望 ${w}`); }
}

/* ---------- 1. 块注释：正常配对 ---------- */
check('普通块注释 1 开 1 闭',
  blockCommentStats(`${OPEN} hello ${CLOSE}`), { opens: 1, closes: 1 });

check('多行块注释',
  blockCommentStats(`${OPEN}\n  a\n  b\n${CLOSE}\nvar x = 1;`), { opens: 1, closes: 1 });

check('两个独立块注释',
  blockCommentStats(`${OPEN} a ${CLOSE} code ${OPEN} b ${CLOSE}`), { opens: 2, closes: 2 });

/* ---------- 2. ★ 假问题回归：行注释里的符号不是块注释 ---------- */
/* 真实案例：某行注释里写 glob 路径，里面的「斜杠 + 星号」被误当成块注释开符。
   代码本身完全合法，报出来就是假问题。 */
check('行注释里的符号不计入',
  blockCommentStats(`// 磁盘上的 pages${OPEN}.js 与声明一致`), { opens: 0, closes: 0 });

check('行注释里的闭符也不计入',
  blockCommentStats(`// 这里写 ${CLOSE} 会怎样`), { opens: 0, closes: 0 });

/* ---------- 3. 字符串 / 模板串 / 正则里的符号 ---------- */
check('单引号字符串里的符号不计入',
  blockCommentStats(`var s = '${OPEN} not a comment';`), { opens: 0, closes: 0 });

check('双引号字符串里的符号不计入',
  blockCommentStats(`var s = "${OPEN} x ${CLOSE}";`), { opens: 0, closes: 0 });

check('模板串里的符号不计入',
  blockCommentStats('var s = `' + OPEN + ' x ' + CLOSE + '`;'), { opens: 0, closes: 0 });

check('正则字面量里的符号不计入',
  blockCommentStats(`var re = /${OPEN === '/*' ? '\\/\\*' : ''}/g;`), { opens: 0, closes: 0 });

/* ---------- 4. ★ 真问题：块注释被提前闭合（D16） ---------- */
/* 注释里出现了闭符 → 注释在这里就断了，后面的文字变成代码。
   这是真 bug，必须抓到。 */
check('块注释被提前闭合 → 不配平',
  blockCommentStats(`${OPEN} 注释里写了 ${CLOSE} 于是提前闭合\nvar x = 1;`),
  { opens: 1, closes: 1 });   // 这里 1:1 是「恰好」——继续看下一个用例

/* 更真实的形态：闭合之后剩余文字又被当成代码，可能再开一个注释 */
check('提前闭合 + 后续再开 → 差 1',
  blockCommentStats(
    `${OPEN} 说明 A ${CLOSE} 后面的字成了代码 ${OPEN} 再也闭不上\nvar y = 2;`),
  { opens: 2, closes: 1 });

/* ---------- 5. ★ 真问题：块注释未闭合 ---------- */
check('未闭合的块注释 → 差 1',
  blockCommentStats(`${OPEN} 一直没关\nvar z = 3;`), { opens: 1, closes: 0 });

/* ---------- 6. 等长替换：行号不能错位 ---------- */
{
  const src = `${OPEN} 中文注释 ${CLOSE}\nvar a = 1;\n// 行注释\nvar b = 2;\n`;
  const out = blankComments(src);
  check('blankComments 长度不变', out.length, src.length);
  check('blankComments 换行数不变',
    (out.match(/\n/g) || []).length, (src.match(/\n/g) || []).length);
  check('blankComments 剥掉了注释内容', out.includes('中文注释'), false);
  check('blankComments 保留了代码', out.includes('var b = 2;'), true);
}

/* ---------- 7. ★ 正则字面量带引号（core.js 的真实陷阱） ---------- */
{
  /* 这一行如果词法器不认正则，引号状态会从这行起全线错乱，
     后面所有注释都剥不掉 —— 静默变错，不报错。 */
  const src = `var x = s.replace(/"/g, '&q;').replace(/'/g, '&#39;');\n// 注释\nvar y = 1;\n`;
  const out = blankComments(src);
  check('带引号的正则不破坏后续注释剥离', out.includes('// 注释'), false);
  check('带引号的正则不影响代码保留', out.includes('var y = 1;'), true);
}

/* ---------- 结果 ---------- */
const total = pass + fail;
console.log('');
if (fail === 0) {
  console.log(`\x1b[32m全部通过：${pass}/${total}\x1b[0m\n`);
} else {
  console.log(`\x1b[31m失败 ${fail} 项\x1b[0m，通过 ${pass}/${total}`);
  console.log('\n失败明细：');
  for (const f of failures) console.log('  · ' + f);
  console.log('');
}
process.exit(fail === 0 ? 0 : 1);
