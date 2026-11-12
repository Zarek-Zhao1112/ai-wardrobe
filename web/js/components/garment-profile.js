/** §35：录入与详情共享的只读搭配信息；原图绑定、未知留空。 */
import { esc, icon } from "../core/ui.js";

export const PROFILE_LABELS = {
  tone:{light:"偏浅",medium:"中等明暗",dark:"偏深"},
  chroma:{muted:"偏柔和",moderate:"鲜艳程度适中",bright:"偏鲜艳"},
  volume:{fitted:"贴身",regular:"适中余量",loose:"偏宽松",oversized:"大廓形"},
  silhouette:{straight:"直身线条",boxy:"箱形轮廓",tapered:"收口线条",flared:"向下展开",a_line:"A字轮廓"},
  body_length:{cropped:"短款",regular:"常规衣长",long:"长款"},
  neckline:{crew:"圆领",v_neck:"V领",collared:"翻领",hooded:"连帽",high_neck:"高领",open_neck:"开放领口"},
  sleeve_length:{sleeveless:"无袖",short:"短袖",elbow:"及肘袖",three_quarter:"七分袖",long:"长袖"},
  closure:{pullover:"套头",buttons:"纽扣开合",zip:"拉链开合",open_front:"敞开门襟",lace:"系带"},
  pattern_scale:{none:"无明显图案",small:"小面积图案/标识",medium:"中等面积图案",large:"大面积图案"},
  pattern_density:{sparse:"图案疏朗",moderate:"图案密度适中",dense:"图案密集"},
  surface:{smooth:"表面较平整",woven:"可见织纹",knit:"可见针织纹理",pile:"绒面样外观",denim:"洗水/牛仔样纹理",ribbed:"罗纹纹理",quilted:"绗缝纹理"},
  sheen:{matte:"偏哑光",slight:"轻微光泽",glossy:"光泽明显"},
  wearing_options:{single:"单穿",inner:"作内搭",open_layer:"可尝试敞开叠穿"}
};
const object = value => value && typeof value === "object" && !Array.isArray(value);
const hash = value => typeof value === "string" && /^sha256:[a-f0-9]{32}$/.test(value);
const upper = new Set(["top","outer","full"]);

export function profileOf(item, taxonomy = {}) {
  const raw = object(item?.tags) && item.tags.visual_profile;
  if (!object(raw) || raw.version !== 1 || raw.item_type !== item.type || !hash(item.image_hash) || raw.image_hash !== item.image_hash || !object(raw.attributes)) return null;
  const slot = item.slot || taxonomy.types?.[item.type]?.slot, attributes = {};
  for (const [key, labels] of Object.entries(PROFILE_LABELS)) {
    const entry = raw.attributes[key];
    if (!object(entry) || !["image","product_text"].includes(entry.source) || !["clear","approximate"].includes(entry.certainty)) continue;
    const value = entry.value;
    if (key === "wearing_options") {
      if (slot !== "top" || !Array.isArray(value) || !value.length || value.length > 3 || value.some(v => typeof v !== "string" || !Object.hasOwn(labels,v))) continue;
    } else if (typeof value !== "string" || !Object.hasOwn(labels,value)) continue;
    if (["volume","silhouette"].includes(key) && !upper.has(slot) && slot !== "bottom") continue;
    if (["neckline","sleeve_length"].includes(key) && !upper.has(slot)) continue;
    if (key === "body_length" && (!["top","outer"].includes(slot) || entry.source !== "product_text")) continue;
    if (key === "closure" && (!upper.has(slot) && slot !== "shoes" || value === "lace" && slot !== "shoes")) continue;
    if (key === "silhouette" && (value === "boxy" && !["top","outer"].includes(slot) || value === "tapered" && slot !== "bottom" || value === "a_line" && !["skirt","dress"].includes(item.type))) continue;
    attributes[key] = {...entry, value:Array.isArray(value)?[...new Set(value)]:value};
  }
  if(attributes.pattern_scale?.value==="none")delete attributes.pattern_density;
  const scale=attributes.pattern_scale?.value;
  if(item.pattern==="solid"&&["medium","large"].includes(scale)||item.pattern&&!["solid","other"].includes(item.pattern)&&scale==="none"){
    delete attributes.pattern_scale;delete attributes.pattern_density;
  }
  const expected={slim:"fitted",regular:"regular",loose:"loose",oversized:"oversized"}[item.fit];
  if(expected&&attributes.volume&&attributes.volume.value!==expected)delete attributes.volume;
  const wearing = attributes.wearing_options;
  if (wearing) {
    wearing.value = wearing.value.filter(v => v !== "open_layer" || ["shirt","cardigan","vest"].includes(item.type) && ["buttons","zip","open_front"].includes(attributes.closure?.value) && attributes.volume);
    if (!wearing.value.length) delete attributes.wearing_options;
  }
  return Object.keys(attributes).length ? {...raw,attributes} : null;
}

export function garmentName(item, taxonomy = {}) {
  const vocab = taxonomy.vocabularies || {};
  return item.name || [vocab.color?.[item.primary_color],item.type_label || taxonomy.types?.[item.type]?.label].filter(Boolean).join("") || "这件衣服";
}

export function garmentProfileHtml(item, taxonomy = {}, {mode="import",open=false,disclosure=true} = {}) {
  const vocab = taxonomy.vocabularies || {}, profile = profileOf(item,taxonomy), attrs = profile?.attributes || {};
  const value = key => attrs[key]?.value;
  const label = key => {const v=value(key);return Array.isArray(v)?v.map(x=>PROFILE_LABELS[key][x]).join(" / "):PROFILE_LABELS[key]?.[v] || "";};
  const basic = (key,raw) => typeof raw === "string" ? vocab[key]?.[raw] || "" : "";
  const colors = [...new Set([item.primary_color,...(Array.isArray(item.colors)?item.colors:[])])].map(v=>basic("color",v)).filter(Boolean);
  const fit = label("volume") || label("silhouette") || basic("fit",item.fit), pattern = label("pattern_scale") || basic("pattern",item.pattern);
  const wearing = Array.isArray(value("wearing_options"))?value("wearing_options"):[];
  const summary = [["配色",[colors[0],label("tone")].filter(Boolean).join(" · ")],["轮廓",fit],["图案",pattern],["穿法",label("wearing_options")]];
  const rows = pairs => pairs.filter(([,v])=>v).map(([k,v])=>`<div class="gp-row"><span>${esc(k)}</span><span>${esc(v)}</span></div>`).join("");
  const section = (title,pairs,note="") => `<section class="gp-section"><h3>${esc(title)}</h3>${rows(pairs) || '<p class="gp-unknown-line">暂未确认</p>'}${note?`<p class="gp-note">${esc(note)}</p>`:""}</section>`;
  const unknown = [];
  if (!colors.length) unknown.push("颜色");
  if (!fit) unknown.push("版型");
  if (!basic("material",item.material)) unknown.push("材质成分");
  if (["top","outer"].includes(item.slot || taxonomy.types?.[item.type]?.slot) && !label("body_length")) unknown.push("实际衣长");
  unknown.push("实际尺寸","厚薄与垂坠");
  const extras = rows([["品牌",item.brand],["材质成分",basic("material",item.material)],["裤长",basic("length",item.length)],["季节",(Array.isArray(item.season)?item.season:[]).map(v=>basic("season",v)).filter(Boolean).join("、")]]);
  const full = `<div class="gp-full">
      ${section("配色线索",[["主色 / 辅色",colors.join(" / ")],["明暗线索",label("tone")],["鲜艳程度",label("chroma")]],"颜色为照片中的近似表现，会受光线影响。")}
      ${section("轮廓与结构",[["松紧线索",label("volume") || basic("fit",item.fit)],["轮廓线索",label("silhouette")],["衣身长短",label("body_length")],["领口 / 袖长",[label("neckline"),label("sleeve_length")].filter(Boolean).join(" / ")],["开合方式",label("closure")]])}
      ${section("图案表现",[["图案形式",basic("pattern",item.pattern)],["图案大小",label("pattern_scale")],["图案密度",label("pattern_density")]])}
      ${section("表面外观",[["可见表面",label("surface")],["光泽线索",label("sheen")]],"可见外观不能证明具体成分、触感或功能。")}
      <section class="gp-section"><h3>可尝试的穿法</h3><div class="gp-role-tags">${wearing.length?wearing.map(v=>`<span>${PROFILE_LABELS.wearing_options[v]}</span>`).join(""):'<span class="is-unknown">暂未确认</span>'}</div><p class="gp-note">穿法为条件性建议，内外层余量与实际效果仍需试穿确认。</p></section>
      ${disclosure&&extras?`<section class="gp-section"><h3>已有属性</h3>${extras}</section>`:""}
      <div class="gp-pending"><strong>暂未确认，先留空</strong><p>${esc(unknown.join("、"))}。</p></div>
      <div data-import-recheck hidden></div>
    </div>`;
  return `<section class="garment-profile" data-garment-profile>
    <div class="gp-card"><div class="gp-card-head"><span>${icon("spark")}${mode==="detail"?"搭配信息":"搭配特征"}</span><small>${profile?"AI 已整理":"已有基础信息"}</small></div><dl class="gp-summary">${summary.map(([k,v])=>`<div><dt>${k}</dt><dd>${esc(v || "暂未确认")}</dd></div>`).join("")}</dl></div>
    ${disclosure ? `<details class="gp-more more-fields${mode==="detail"?" item-more":""}" data-fold="1" data-garment-more ${open?"open":""}><summary><span>${mode==="detail"?"查看全部搭配信息":"查看 AI 整理的搭配信息"}</span>${icon("chevron")}</summary>${full}</details>` : full}
    <p class="gp-caption">没有确认的信息不会当作已知搭配条件。</p>
  </section>`;
}
