import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';

const root = path.resolve(import.meta.dirname, '..');
const html = fs.readFileSync(path.join(root, 'docs', 'index.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)?.[1];
assert.ok(script);
new vm.Script(script);
const selected = ['displayPrice', 'feedbackNumber', 'feedbackText', 'feedbackDelta', 'feedbackHtml']
  .map(name => script.split('\n').find(line => line.startsWith(`function ${name}(`)))
  .join('\n');
const context = vm.createContext({
  esc: value => String(value),
  fmt: value => Number(value).toLocaleString('en-US'),
});
vm.runInContext(`${selected}\nthis.feedback = { displayPrice, feedbackText, feedbackDelta, feedbackHtml };`, context);
const { displayPrice, feedbackText, feedbackDelta, feedbackHtml } = context.feedback;
assert.equal(displayPrice({ price: '未显示/无法获取', priceNote: '原始榜单标记不可售' }), '不可售');
assert.equal(displayPrice({ price: '未显示/无法获取', priceNote: '原始榜单不可核对' }), '价格未显示');
assert.equal(displayPrice({ price: '$15.08' }), '$15.08');
assert.equal(feedbackText(null, 'rating'), '未记录');
assert.equal(feedbackText(0, 'reviews'), '0');
assert.equal(feedbackText(4.3, 'rating'), '4.3');
assert.equal(feedbackDelta(4.3, null, 'rating'), '');
assert.equal(feedbackDelta(552, null, 'reviews'), '');
assert.match(feedbackDelta(552, 550, 'reviews'), /↑2/);
assert.match(feedbackDelta(4.3, 4.4, 'rating'), /↓0\.1/);
assert.match(feedbackHtml({ rating: 4.3, reviewCount: 552 }, { rating: null, reviewCount: null }), /评论 552/);
assert.doesNotMatch(feedbackHtml({ rating: 4.3, reviewCount: 552 }, { rating: null, reviewCount: null }), /feedback-delta/);

// 2026-09-20 清理了 09-17 之前的快照，样本期相应改到现存的最新一期。
for (const [node, relative] of [
  ['2368365011', 'data/daily/2026/09/2026-09-19.json'],
  ['2368383011', 'data/categories/2368383011/daily/2026/09/2026-09-19.json'],
]) {
  const latest = JSON.parse(fs.readFileSync(path.join(root, 'docs', relative), 'utf8'));
  assert.equal(String(latest.category.node), node);
  assert.equal(latest.items.length, 100);
  assert.equal(latest.items.filter(item => typeof item.rating === 'number').length, 100);
  assert.equal(latest.items.filter(item => Number.isInteger(item.reviewCount)).length, 100);
}
// 「旧期档案不含 rating / reviewCount 字段」这条契约，以前用 2026-09-13 那一期
// 校验。该期已在清理中删除，且后续各期都带完整反馈字段，归档里再无此类样本。
// 对应的前端行为（缺字段时不渲染 delta）由上面第 30~31 行的人造用例覆盖。
assert.match(html, /<span>评分<\/span><span>评论数<\/span><span>促销<\/span>/);
console.log('Dashboard feedback checks passed.');
