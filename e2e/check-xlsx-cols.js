// 用 node 读字节校验导出 xlsx 的列头与埋点值，绕开 Read/Select-String 对中文的幻影问题
const fs = require('fs');
const path = require('path');

const dir = process.argv[2];
const keys = ['Agent 回复版本', 'Agent 回复', 'VERIFY119'];

const files = fs.readdirSync(dir).filter((f) => f.endsWith('.sheet1.xml')).sort();
if (files.length === 0) {
  console.log('NO_SHEET_XML in ' + dir);
  process.exit(1);
}

for (const f of files) {
  const s = fs.readFileSync(path.join(dir, f), 'utf8');
  console.log('=== ' + f + ' (chars=' + s.length + ') ===');

  for (const k of keys) {
    const idx = s.indexOf(k);
    const count = idx < 0 ? 0 : s.split(k).length - 1;
    console.log('  [' + k + '] idx=' + idx + ' count=' + count);
  }

  const rowEnd = s.indexOf('</row>');
  if (rowEnd > 0) {
    const row1 = s.slice(0, rowEnd);
    const hdrs = [];
    const re = /<t[^>]*>([^<]*)<\/t>/g;
    let m;
    while ((m = re.exec(row1)) !== null) hdrs.push(m[1]);
    console.log('  headers(' + hdrs.length + ')=' + JSON.stringify(hdrs, null, 0));
  } else {
    console.log('  headers=<row1 not found>');
  }

  // 打印 VERIFY119 附近上下文，确认它落在哪一列
  const vi = s.indexOf('VERIFY119');
  if (vi >= 0) {
    console.log('  ctx=' + JSON.stringify(s.slice(Math.max(0, vi - 200), vi + 120)));
  }
}
