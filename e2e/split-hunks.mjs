// 按 hunk 序号把某个文件的 unstaged diff 切成子补丁并 git apply --cached。
// 全程用 Buffer，不让 Node/PowerShell 碰字符串编码（补丁里有中文注释）。
//   node split-hunks.mjs <repo> <file> <1-based hunk indices, comma sep>
import { execFileSync } from 'node:child_process'
import { writeFileSync, unlinkSync } from 'node:fs'

const GIT = 'D:\\software\\Git\\cmd\\git.exe'
const [repo, file, pick] = process.argv.slice(2)
const want = new Set(pick.split(',').map(s => parseInt(s.trim(), 10)))

const diff = execFileSync(GIT, ['-C', repo, '--no-pager', 'diff', '--', file], {
  maxBuffer: 1 << 28,
})
const lines = []
let start = 0
for (let i = 0; i < diff.length; i++) {
  if (diff[i] === 0x0a) { lines.push(diff.subarray(start, i)); start = i + 1 }
}
if (start < diff.length) lines.push(diff.subarray(start))

const isHunkStart = b => b.length >= 2 && b[0] === 0x40 && b[1] === 0x40 // "@@"
const headerEnd = lines.findIndex(isHunkStart)
if (headerEnd < 0) { console.log('NO_HUNKS'); process.exit(1) }

const header = lines.slice(0, headerEnd)
const hunks = []
for (let i = headerEnd; i < lines.length; i++) {
  if (isHunkStart(lines[i])) hunks.push([lines[i]])
  else if (hunks.length) hunks[hunks.length - 1].push(lines[i])
}

const kept = hunks.filter((_, idx) => want.has(idx + 1))
console.log(`TOTAL=${hunks.length} KEPT=${kept.length}`)
const out = Buffer.concat(
  [...header, ...kept.flat()].flatMap(b => [b, Buffer.from('\n')]),
)
const patch = `${repo}\\_subset.patch`
writeFileSync(patch, out)
try {
  execFileSync(GIT, ['-C', repo, 'apply', '--cached', '--verbose', patch], {
    stdio: 'inherit',
  })
  console.log('APPLY_OK')
} finally {
  unlinkSync(patch)
}
