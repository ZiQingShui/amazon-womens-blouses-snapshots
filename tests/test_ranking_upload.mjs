import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";

const root = path.resolve(import.meta.dirname, "..");
const worker = fs.readFileSync(path.join(root, "dist/server/index.js"), "utf8");
const source = worker.split("export default {")[0];
const parse = vm.runInNewContext(`${source}\nparseRankingExport`, {});
const heading = "ASIN|图片ID|标题|价格|评论数|评分|类目|节点|排名|抓取时间".split("|");
const node = "2368365011";
function makeFile(skip = 0) {
  const rows = Array.from({length: 100}, (_, i) => i + 1).filter(rank => rank !== skip).map(rank => {
    const asin = `B${String(rank).padStart(9, "0")}`;
    const cells = [asin, "71AbcdEFghL", `Top &amp; Blouse ${rank}`, "19.99", "10", "4.5", "Women&#39;s Blouses", node, String(rank), "2026-9-15 16:25:45"];
    return `<tr>${cells.map(cell => `<td>${cell}</td>`).join("")}</tr>`;
  }).join("");
  return `<html><table id="result_table"><thead><tr>${heading.map(cell => `<td>${cell}</td>`).join("")}</tr></thead><tbody>${rows}</tbody></table></html>`;
}
const parsed = parse(makeFile(), node);
assert.equal(parsed.rows.length, 100);
assert.equal(parsed.rows[0].title, "Top & Blouse 1");
assert.equal(parsed.snapshotDate, "2026-09-15");
assert.throws(() => parse(makeFile(48), node), /48/);
assert.throws(() => parse(makeFile(), "2368383011"), /节点/);
if (process.argv[2]) {
  const actual = parse(fs.readFileSync(process.argv[2], "utf8"), node);
  assert.equal(actual.rows.length, 100);
  assert.equal(actual.rows[0].asin, "B0GWZRJ64P");
  assert.equal(actual.rows.at(-1).asin, "B0H57T6NMM");
  console.log(`supplied export: ${actual.rows.length} products, ${actual.snapshotDate}, ${actual.sourceCapturedAt}`);
}
console.log("ranking upload parser checks passed");
