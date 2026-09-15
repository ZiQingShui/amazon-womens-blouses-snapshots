import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';

const root = path.resolve(import.meta.dirname, '..');
const html = fs.readFileSync(path.join(root, 'docs', 'index.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)?.[1];
assert.ok(script, 'dashboard script exists');
new vm.Script(script);

const selected = ['movementOf', 'comparison', 'diffFor']
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
const context = vm.createContext({ current, previous, esc: value => String(value) });
vm.runInContext(`${selected}\nthis.testApi = { movementOf, comparison, diffFor };`, context);
const { movementOf, comparison, diffFor } = context.testApi;
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
