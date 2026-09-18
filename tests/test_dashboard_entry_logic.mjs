import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';

const root = path.resolve(import.meta.dirname, '..');
const html = fs.readFileSync(path.join(root, 'docs', 'index.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)?.[1];
assert.ok(script, 'dashboard script exists');
new vm.Script(script);

// pk 是父体匹配键函数（同款合并用），movementOf / diffFor / comparison 都依赖它，
// 抽取执行时必须一并带上，否则会 ReferenceError。
const selected = ['hasParentData', 'pk', 'movementOf', 'comparison', 'diffFor']
  .map(name => script.split('\n').find(line => line.startsWith(`function ${name}(`)))
  .join('\n');
const current = {
  snapshotDate: '2026-09-13',
  items: [
    { asin: 'B0H3PMRYPC', rank: 17, history: { firstSeen: '2026-09-11', appearances: 2 } },
    { asin: 'FIRST', rank: 20, history: { firstSeen: '2026-09-13', appearances: 1 } },
    { asin: 'UNKNOWN', rank: 30 },
    { asin: 'SAME', rank: 10, history: { firstSeen: '2026-09-11' } },
  ],
};
const previous = { snapshotDate: '2026-09-12', items: [{ asin: 'SAME', rank: 11 }, { asin: 'EXIT', rank: 45 }] };
// parentKeyActive=false 表示按子体（ASIN）匹配，与这批没有 parentAsin 的测试数据一致。
const context = vm.createContext({ current, previous, parentKeyActive: false, esc: value => String(value) });
vm.runInContext(`${selected}\nthis.testApi = { hasParentData, movementOf, comparison, diffFor };`, context);
const { hasParentData, movementOf, comparison, diffFor } = context.testApi;

// 父体覆盖率不足 80% 时判定为「该期没有父体数据」——对比会回退到 ASIN 匹配。
// 背景：2026-09-18 之前 09-10~09-16 七期快照的 parentAsin 全为空，「同款合并」视图下
// 对比这些日期时 100 件商品会被全部判成「新进」，脉搏全是灰色。
assert.equal(hasParentData([{parentAsin:'A'},{parentAsin:'B'},{parentAsin:'C'},{parentAsin:'D'},{}]), true, '80% 覆盖率判定为有父体数据');
assert.equal(hasParentData([{parentAsin:'A'},{parentAsin:'B'},{parentAsin:'C'},{},{}]), false, '60% 覆盖率判定为缺父体数据');
assert.equal(hasParentData([{parentAsin:'A'}]), true, '全都有父体时判定为有');
assert.equal(hasParentData([]), false, '空数组不算有父体数据');
assert.equal(hasParentData(null), false, 'null 不算有父体数据');
const oldMap = new Map(previous.items.map(item => [item.asin, item]));

assert.equal(movementOf(current.items[0], oldMap), 'return');
assert.equal(movementOf(current.items[1], oldMap), 'new');
assert.equal(movementOf(current.items[2], oldMap), 'unknown');
assert.equal(movementOf(current.items[3], oldMap), 'up');
assert.match(diffFor(current.items[0], oldMap).movement, /重新入榜.*2026-09-11/);
assert.doesNotMatch(diffFor(current.items[0], oldMap).movement, /本期新入榜|首次进入/);
const result = comparison();
assert.equal(result.entrants.length, 1);
assert.equal(result.returns.length, 1);
assert.equal(result.unverified.length, 1);
assert.equal(result.exits.length, 1);
previous.snapshotDate = '2026-09-10';
assert.equal(movementOf(current.items[0], oldMap), 'historical');
assert.equal(comparison().returns.length, 0);
previous.snapshotDate = '2026-09-12';
const actual13 = JSON.parse(fs.readFileSync(path.join(root, 'docs', 'data', 'daily', '2026', '09', '2026-09-13.json'), 'utf8'));
const actual12 = JSON.parse(fs.readFileSync(path.join(root, 'docs', 'data', 'daily', '2026', '09', '2026-09-12.json'), 'utf8'));
const actualProduct = actual13.items.find(item => item.asin === 'B0H3PMRYPC');
assert.ok(actualProduct);
assert.equal(actualProduct.history.firstSeen, '2026-09-11');
assert.equal(actual12.items.some(item => item.asin === 'B0H3PMRYPC'), false);
assert.equal(movementOf(actualProduct, new Map(actual12.items.map(item => [item.asin, item]))), 'return');
console.log('Dashboard entry-status regression checks passed.');
