/* 预览版新增逻辑 —— 由 tools/wire_style_tags.py 注入到正式版主脚本顶层。
   设计：机器只给「建议」（虚线、不算数），标签值由用户选；每个维度都能加自定义项，
   自定义项存在本机并在导出时一并带走。与打标工作台共用同一套 key。 */

const CUSTOM_KEY = "styleTagsCustomOptions";
let customOptions = { sleeve: [], season: [], style: [] };

function loadCustomOptions() {
  try {
    customOptions = Object.assign({ sleeve: [], season: [], style: [] },
      JSON.parse(localStorage.getItem(CUSTOM_KEY) || "{}"));
  } catch (e) { }
}
function saveCustomOptions() {
  try { localStorage.setItem(CUSTOM_KEY, JSON.stringify(customOptions)); } catch (e) { }
}
function presetOf(dim) {
  return dim === "sleeve" ? SLEEVE_ORDER : dim === "season" ? SEASON_ORDER : STYLE_ORDER;
}

/* 弹窗里显示哪些项：机器建议 + 我自定义过的 + 当前已选（预设列表不再出现） */
function editOptions(dim, draft, sugg) {
  const out = [];
  const add = v => { if (v && out.indexOf(v) < 0) out.push(v); };
  if (dim === "season") (sugg.season || []).forEach(add); else add(sugg[dim]);
  (customOptions[dim] || []).forEach(add);
  if (dim === "season") (draft.season || []).forEach(add); else add(draft[dim]);
  return out;
}

/* 筛选下拉：**只列「我确认过的」值**（机器建议一概不算，也不出现在这里），
   预设词按原顺序排前面。本机改过之后要重跑（saveLocalStyleTags 里会调）。 */
function syncStyleOptions() {
  const seen = { sleeve: new Set(), season: new Set(), style: new Set() };
  const take = t => {
    if (t.sleeve) seen.sleeve.add(t.sleeve);
    (t.season || []).forEach(x => x && seen.season.add(x));
    const s = t.style;          /* localTags 里主风格字段叫 style */
    if (typeof s === "string") { if (s) seen.style.add(s); }
    else if (Array.isArray(s)) s.forEach(x => x && seen.style.add(x));
  };
  Object.values(localTags).forEach(take);
  const orderBy = dim => {
    const set = seen[dim];
    const pref = presetOf(dim).filter(v => set.has(v));
    return pref.concat([...set].filter(v => pref.indexOf(v) < 0).sort());
  };
  const fill = (id, list, allLabel) => {
    const select = $(id), keep = select.value;
    select.innerHTML = '<option value="">' + allLabel + '</option>'
      + list.map(v => '<option value="' + esc(v) + '">' + esc(v) + '</option>').join("");
    select.value = keep;
    if (![...select.options].some(o => o.value === keep)) select.value = "";
  };
  fill("filterSleeve", orderBy("sleeve"), "全部袖型");
  fill("filterSeason", orderBy("season"), "全部季节");
  fill("filterStyle", orderBy("style"), "全部风格");
}

function loadLocalStyleTags() {
  LOCAL_KEY = "styleTagsManual_" + (STYLE_DOC_DATE || "");
  try { localTags = JSON.parse(localStorage.getItem(LOCAL_KEY) || "{}"); } catch (e) { localTags = {}; }
}
function saveLocalStyleTags() {
  try { localStorage.setItem(LOCAL_KEY, JSON.stringify(localTags)); } catch (e) { }
  const n = Object.keys(localTags).length;
  const b = document.getElementById("styExport");
  if (b) { b.hidden = !n; b.textContent = "导出我的修改" + (n ? " (" + n + ")" : ""); }
  const c = document.getElementById("styClear");
  if (c) c.hidden = !n;
  /* 本机改过之后，筛选下拉要跟着出现新值（自定义项、被改成的值） */
  if (typeof syncStyleOptions === "function") { try { syncStyleOptions(); } catch (e) { } }
}
/* 卡片/筛选统一走这里：本机手改 > 标签库；带 confirmed 标记用于区分"确认过"和"机器建议" */
function tagOf(p) {
  const key = p.parentAsin || p.asin;
  const base = STYLE_TAGS[key] || STYLE_TAGS[p.asin] || null;
  const m = localTags[key];
  if (!m) return base;
  return Object.assign({}, base || {}, {
    sleeve: m.sleeve || "", season: m.season || [], stylePrimary: m.style || "",
    source: "manual", confirmed: true
  });
}
function styFlash(msg) {
  let t = document.getElementById("styToast");
  if (!t) {
    t = document.createElement("div"); t.id = "styToast"; t.className = "sty-toast";
    document.body.appendChild(t);
  }
  t.textContent = msg; t.classList.add("show");
  clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("show"), 2200);
}

function openStyleEditor(parent, asin) {
  const p = ((current && current.items) || []).find(x => x.asin === asin) || {};
  const base = STYLE_TAGS[parent] || STYLE_TAGS[asin] || {};
  const m = localTags[parent];
  const draft = m ? { sleeve: m.sleeve || "", season: (m.season || []).slice(), style: m.style || "" }
    : { sleeve: "", season: [], style: "" };
  const sugg = { sleeve: base.sleeve || "", season: base.season || [], style: base.stylePrimary || "" };
  const rows = [["sleeve", "袖型", false], ["season", "季节", true], ["style", "主风格", false]];
  const mask = document.createElement("div");
  mask.className = "sty-mask";

  const suggText = dim => dim === "season" ? (sugg.season || []).join("、") : (sugg[dim] || "");

  const bodyHtml = () => '<div class="sty-head"><img src="' + (p.image ? safeImageUrl(p.image) : "") + '" alt="">'
    + '<div><b style="font-size:13px">' + esc((p.title || asin).slice(0, 90)) + '</b>'
    + '<div class="t">' + esc(asin) + (p.brand ? " · " + esc(p.brand) : "") + '</div></div></div>'
    + '<div class="sty-note">机器只给<b>建议</b>、不替你决定：<b>选中的才算数</b>，没选的维度当作未标注。'
    + '想用别的值就点「＋ 自定义」，加过的会留下来，下次还能选。'
    + '筛选与计数<b>只统计你确认过的商品</b>，机器建议不参与。</div>'
    + rows.map(function (r) {
      const dim = r[0], label = r[1], multi = r[2], sg = suggText(dim);
      return '<div class="sty-row"><b>' + label + '<em>' + (multi ? "可多选" : "单选") + '</em>' +
        (sg ? '<span class="sty-sg">建议：' + esc(sg) + '</span>' : '<span class="sty-sg">没有建议，自己选</span>') + '</b>'
        + '<div class="sty-chips" data-dim="' + dim + '">'
        + editOptions(dim, draft, sugg).map(function (v) {
          const on = multi ? draft[dim].indexOf(v) >= 0 : draft[dim] === v;
          const isS = !on && !!sg && (multi ? (sugg[dim] || []).indexOf(v) >= 0 : sugg[dim] === v);
          return '<button type="button" class="sty-chip' + (on ? " on" : "") + (isS ? " sugg" : "")
            + '" data-v="' + esc(v) + '">' + esc(v) + '</button>';
        }).join("")
        + '<button type="button" class="sty-chip add" data-add="1">＋ 自定义</button>'
        + '</div></div>';
    }).join("")
    + '<div class="sty-foot">'
    + '<button type="button" class="sty-btn" data-act="clear">清空此项</button>'
    + '<button type="button" class="sty-btn" data-act="cancel">取消</button>'
    + '<button type="button" class="sty-btn pri" data-act="save">保存</button>'
    + '</div>';

  mask.innerHTML = '<div class="sty-box" role="dialog" aria-label="修改款式标签">' + bodyHtml() + '</div>';
  document.body.appendChild(mask);
  const close = () => mask.remove();
  const paint = () => {
    const box = mask.querySelector(".sty-box"), top = box.scrollTop;
    box.innerHTML = bodyHtml(); box.scrollTop = top;
  };

  mask.addEventListener("click", event => {
    if (event.target === mask) { close(); return; }
    const chip = event.target.closest(".sty-chip");
    if (chip) {
      const dim = chip.parentNode.dataset.dim;
      if (chip.dataset.add) {
        const names = { sleeve: "袖型", season: "季节", style: "主风格" };
        const v = (prompt("添加自定义" + names[dim] + "（同名会直接选中）") || "").trim();
        if (!v) return;
        if ((customOptions[dim] || []).indexOf(v) < 0) {
          customOptions[dim] = (customOptions[dim] || []).concat([v]); saveCustomOptions();
        }
        if (dim === "season") { if (draft.season.indexOf(v) < 0) draft.season.push(v); }
        else { draft[dim] = v; }
        paint(); return;
      }
      const v = chip.dataset.v;
      if (dim === "season") {
        const i = draft.season.indexOf(v);
        if (i < 0) draft.season.push(v); else draft.season.splice(i, 1);
      } else { draft[dim] = draft[dim] === v ? "" : v; }
      paint(); return;
    }
    const btn = event.target.closest("[data-act]");
    if (!btn) return;
    const act = btn.dataset.act;
    if (act === "cancel") { close(); }
    else if (act === "clear") {
      delete localTags[parent]; saveLocalStyleTags();
      if (current) render(); close();
      styFlash("已清空此项，卡片上不再显示它的标签");
    } else if (act === "save") {
      localTags[parent] = { sleeve: draft.sleeve, season: draft.season.slice(), style: draft.style, ts: Date.now() };
      saveLocalStyleTags(); if (current) render(); close();
      styFlash("已保存 · 点「导出我的修改」落盘");
    }
  });
  document.addEventListener("keydown", function esc(e) {
    if (e.key === "Escape") { close(); document.removeEventListener("keydown", esc); }
  });
}

function exportLocalStyleTags() {
  const n = Object.keys(localTags).length;
  if (!n) { styFlash("还没有改过任何标签"); return; }
  const out = {
    updatedAt: new Date().toISOString(), source: "dashboard-edit", date: STYLE_DOC_DATE,
    options: customOptions, tags: localTags
  };
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([JSON.stringify(out, null, 1)], { type: "application/json" }));
  a.download = "style-tags-manual-" + (STYLE_DOC_DATE || "latest") + ".json";
  a.click();
  styFlash("已导出 " + n + " 条 · 放进 work/ 后跑 tag_style.py 即永久生效");
}
