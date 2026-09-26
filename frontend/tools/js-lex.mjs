/* ============================================================
   js-lex.mjs —— 极小的 JS 注释剥离工具（零依赖）
   ------------------------------------------------------------
   给「静态扫描 JS 源码」的工具用：扫描前必须把注释内容抹掉，
   否则注释里举例的代码会被当成真实引用，报出假问题。

   为什么不用一行正则解决：
     源码里同时存在字符串、模板串、正则字面量、行注释、块注释，
     它们的引号/斜杠语义互相干扰，正则做不到。

   ★★ 最大的坑：必须识别「正则字面量」。
     本项目 core.js 里有一行：
         .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
     两个正则里各带一个引号。如果词法器不认正则字面量，
     就会把 /"/g 里的 " 当成字符串开始，**从这一行起引号状态
     全线错乱**，之后所有注释都剥不掉、所有扫描结果都不可信。
     这个错误极其隐蔽：不会报错，只是结果静默变错。

   区分「正则开始」还是「除号」用的是标准启发式：
     看前一个有效字符。若是运算符 / 分隔符 / 行首，则可能是正则。
   ============================================================ */

/** 前一个有效字符处于这些位置时，`/` 可能是正则的开始 */
const REGEX_PREV = '(,=:[!&|?{};+-*%^~<>';

/**
 * 把注释内容替换成等长空白（保留换行）。
 *
 * ★ 必须是「等长替换」而不是「删除」：删掉注释会让后面的行号整体前移，
 *   报出来的行号就指到别处去了。
 *
 * @param {string} src JS 源码
 * @param {{opens:number, closes:number}|undefined} stats
 *        传了就顺带统计「真正的」块注释开符/闭符个数（见 blockCommentStats）。
 * @returns {string} 同长度、同换行结构的源码，注释部分变成空格
 */
export function blankComments(src, stats) {
  const chars = src.split('');
  let quote = null;          // 当前所处的引号：' " `
  let inBlock = false;       // 是否在块注释里
  let prevMeaningful = '';   // 上一个有效（非空白、非注释）字符
  let i = 0;

  const blank = (from, to) => {
    for (let k = from; k < to && k < chars.length; k++) {
      if (chars[k] !== '\n') chars[k] = ' ';
    }
  };

  while (i < src.length) {
    const c = src[i];

    if (inBlock) {
      if (c === '*' && src[i + 1] === '/') {
        if (stats) stats.closes++;
        blank(i, i + 2);
        inBlock = false;
        i += 2;
        continue;
      }
      if (c !== '\n') chars[i] = ' ';
      i++;
      continue;
    }

    if (quote) {
      if (c === '\\') { i += 2; continue; }
      if (c === quote) { quote = null; prevMeaningful = quote; }
      i++;
      continue;
    }

    if (c === '\n' || c === ' ' || c === '\t' || c === '\r') { i++; continue; }

    if (c === "'" || c === '"' || c === '`') { quote = c; i++; continue; }

    if (c === '/' && src[i + 1] === '/') {
      let j = i;
      while (j < src.length && src[j] !== '\n') j++;
      blank(i, j);
      i = j;
      continue;
    }

    if (c === '/' && src[i + 1] === '*') {
      if (stats) stats.opens++;
      blank(i, i + 2);
      inBlock = true;
      i += 2;
      continue;
    }

    /* ★ 正则字面量：/.../flags。必须先判断，否则里面的引号会带偏状态。 */
    if (c === '/' && (prevMeaningful === '' || REGEX_PREV.includes(prevMeaningful))) {
      let j = i + 1;
      let inClass = false;     // 字符类 [...] 里的 / 不结束正则
      while (j < src.length) {
        const d = src[j];
        if (d === '\\') { j += 2; continue; }
        if (d === '\n') break;             // 正则不能跨行，说明判断错了
        if (d === '[') inClass = true;
        else if (d === ']') inClass = false;
        else if (d === '/' && !inClass) break;
        j++;
      }
      if (j < src.length && src[j] === '/') {
        /* 跳过后面的 flags */
        let k = j + 1;
        while (k < src.length && /[a-z]/i.test(src[k])) k++;
        prevMeaningful = src[j];
        i = k;
        continue;
      }
      /* 没找到收尾的 / —— 说明这不是正则，当普通字符处理 */
    }

    prevMeaningful = c;
    i++;
  }
  return chars.join('');
}

/**
 * 统计**真正的**块注释开符 / 闭符个数。
 *
 * ★ 为什么不能直接用正则数那两个符号：
 *   字符串、正则字面量、**行注释**里都可能出现它们，但都不是块注释。
 *   实测踩过：某行注释里写了 glob 路径（目录名后面跟通配符），
 *   其中的「斜杠 + 星号」被当成块注释开符 → 报出「不配平」的假问题。
 *   假问题比没问题更糟 —— 会让人去改本来正确的代码（见 D17 的教训）。
 *
 *   所以必须走同一个状态机。这里复用 blankComments，而不是另写一份 ——
 *   两份状态机一定会漂移，到时候两个工具给出互相矛盾的结论。
 *
 * ★★ 而且：写这段说明时我又踩了一次 D16 ——
 *   为了举例，我在这段注释里把「斜杠 + 星号 + 斜杠」原样写了出来，
 *   那对字符当场闭合了本注释，文件直接语法报错。
 *   **同一个坑第 4 次了**，而且这次是在专门解释它的注释里。
 *   所以这里一律用文字描述，绝不写出那对字符。
 *
 * @param {string} src JS 源码
 * @returns {{opens:number, closes:number}}
 */
export function blockCommentStats(src) {
  const stats = { opens: 0, closes: 0 };
  blankComments(src, stats);
  return stats;
}
