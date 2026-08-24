import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFile } from "node:fs/promises";

const require = createRequire(import.meta.url);
const TemuDomCapture = require("./temu_dom_capture.js");

class FakeElement {
  constructor(tagName, { id = "", text = "", attrs = {}, style = {}, rect = { width: 120, height: 40 } } = {}) {
    this.tagName = String(tagName || "div").toUpperCase();
    this.id = id;
    this.innerText = text;
    this.textContent = text;
    this.className = attrs.class || "";
    this.attributes = { ...attrs };
    this.style = { ...style };
    this.rect = rect;
    this.children = [];
    this.parentElement = null;
  }

  append(...children) {
    for (const child of children) {
      child.parentElement = this;
      this.children.push(child);
    }
    return this;
  }

  getAttribute(name) {
    if (name === "id") return this.id;
    if (name === "class") return this.className;
    if (name === "style") return this.attributes.style || "";
    return this.attributes[name] || "";
  }

  getBoundingClientRect() {
    return { top: 0, left: 0, right: this.rect.width, bottom: this.rect.height, ...this.rect };
  }

  querySelectorAll(selector) {
    const descendants = [];
    const visit = (node) => {
      for (const child of node.children) {
        descendants.push(child);
        visit(child);
      }
    };
    visit(this);
    if (selector === "*") return descendants;
    if (selector === "img, source") return descendants.filter((node) => node.tagName === "IMG" || node.tagName === "SOURCE");
    return [];
  }
}

function fakeDocument(leftContent, rightContent, goodsPrice = null) {
  return {
    location: { href: "https://www.temu.com/product-g-123456789.html" },
    getElementById(id) {
      if (id === "leftContent") return leftContent;
      if (id === "rightContent") return rightContent;
      if (id === "goods_price") return goodsPrice;
      return null;
    }
  };
}

const fakeView = {
  location: { href: "https://www.temu.com/product-g-123456789.html" },
  getComputedStyle(element) {
    return {
      display: element.style.display || "block",
      visibility: element.style.visibility || "visible",
      opacity: element.style.opacity ?? "1",
      backgroundImage: element.style.backgroundImage || "none",
      borderWidth: element.style.borderWidth || "0px",
      textDecorationLine: element.style.textDecorationLine || "none"
    };
  }
};

test("collects every Temu gallery background image even when carousel items are offscreen", () => {
  const left = new FakeElement("div", { id: "leftContent" });
  left.append(
    new FakeElement("li", { attrs: { "data-index": "0" } }).append(
      new FakeElement("div", {
        style: { backgroundImage: 'url("https://img.kwcdn.com/product/fancy/hero.jpg?imageView2/2/w/180/q/70/format/avif")' }
      })
    ),
    new FakeElement("li", { attrs: { "data-index": "1" }, style: { display: "none" } }).append(
      new FakeElement("div", {
        style: { backgroundImage: 'url("https://img.kwcdn.com/product/fancy/side.jpg?imageView2/2/w/180/q/70/format/avif")' }
      })
    ),
    new FakeElement("li", { attrs: { "data-index": "2" } }).append(
      new FakeElement("div", {
        style: { backgroundImage: 'url("https://aimg.kwcdn.com/upload_aimg/aftersales/service.png")' }
      })
    )
  );
  const right = new FakeElement("div", { id: "rightContent" }).append(
    new FakeElement("div", { style: { backgroundImage: 'url("https://img.kwcdn.com/recommend/not-product.jpg")' } })
  );

  const result = TemuDomCapture.extract(fakeDocument(left, right), fakeView);

  assert.deepEqual(result.gallery_images.map((item) => item.url), [
    "https://img.kwcdn.com/product/fancy/hero.jpg",
    "https://img.kwcdn.com/product/fancy/side.jpg"
  ]);
  assert.equal(result.gallery_images[1].carousel_index, 1);
});

test("extracts semantic Temu SKU labels and plain-div options without dynamic class names", () => {
  const left = new FakeElement("div", { id: "leftContent" });
  const right = new FakeElement("div", { id: "rightContent" });
  right.append(
    new FakeElement("div", { text: "容量" }),
    new FakeElement("div").append(
      new FakeElement("div", { text: "🔥16L", attrs: { "aria-selected": "true" }, style: { borderWidth: "2px" } }),
      new FakeElement("div", { text: "12升/3.2加仑" }),
      new FakeElement("div", { text: "NEW-3.7gal", style: { backgroundImage: 'url("https://img.kwcdn.com/product/fancy/sku-green.jpg?imageView2/2/w/180")' } })
    ),
    new FakeElement("div", { text: "数量" }),
    new FakeElement("div").append(new FakeElement("div", { text: "1" }))
  );

  const result = TemuDomCapture.extract(fakeDocument(left, right), fakeView);

  assert.deepEqual(result.sku_groups, [{
    name: "Capacity",
    source_name: "容量",
    values: [
      { value: "16L", image_url: "", selected: true, selectable: true },
      { value: "12升/3.2加仑", image_url: "", selected: false, selectable: true },
      { value: "NEW-3.7gal", image_url: "https://img.kwcdn.com/product/fancy/sku-green.jpg", selected: false, selectable: true }
    ]
  }]);
});

test("selects the visible Temu sale price from goods_price and rejects struck and installment prices", () => {
  const left = new FakeElement("div", { id: "leftContent" });
  const right = new FakeElement("div", { id: "rightContent" });
  const goodsPrice = new FakeElement("div", { id: "goods_price" }).append(
    new FakeElement("span", { text: "$137.97", style: { textDecorationLine: "line-through" } }),
    new FakeElement("div").append(
      new FakeElement("span", { text: "$19.59", attrs: { class: "_14At0Pe5" } }),
      new FakeElement("span", { text: "$" , attrs: { "aria-hidden": "true" } }),
      new FakeElement("span", { text: "19", attrs: { "aria-hidden": "true" } }),
      new FakeElement("span", { text: ".59", attrs: { "aria-hidden": "true" } })
    ),
    new FakeElement("div", { text: "今天支付$4.89" })
  );

  const result = TemuDomCapture.extract(fakeDocument(left, right, goodsPrice), fakeView);

  assert.deepEqual(result.current_price, {
    amount: 19.59,
    currency: "USD",
    value: "$19.59",
    source: "temu-goods-price-visible"
  });
});

test("uses Temu radio aria labels as SKU values and reads each descendant SKU image URL", () => {
  const left = new FakeElement("div", { id: "leftContent" });
  const right = new FakeElement("div", { id: "rightContent" });
  const group = new FakeElement("div").append(
    new FakeElement("div", { text: "颜色" }),
    new FakeElement("div", { attrs: { role: "radio", "aria-label": "Black", "aria-checked": "false" } }).append(
      new FakeElement("img", { attrs: { src: "https://img.kwcdn.com/product/fancy/sku-black.jpg?imageView2/2/w/180" } })
    ),
    new FakeElement("div", { attrs: { role: "radio", "aria-label": "多彩", "aria-checked": "true" } }).append(
      new FakeElement("div", { style: { backgroundImage: 'url("https://img.kwcdn.com/product/fancy/sku-color.jpg?imageView2/2/w/180")' } })
    ),
    new FakeElement("div", { attrs: { role: "radio", "aria-label": "卡其色", "aria-checked": "false" } }).append(
      new FakeElement("img", { attrs: { "data-src": "https://img.kwcdn.com/product/fancy/sku-khaki.jpg?imageView2/2/w/180" } })
    ),
    new FakeElement("div", { text: "数量" })
  );
  right.append(group);

  const result = TemuDomCapture.extract(fakeDocument(left, right), fakeView);

  assert.deepEqual(result.sku_groups, [{
    name: "Color",
    source_name: "颜色",
    values: [
      { value: "Black", image_url: "https://img.kwcdn.com/product/fancy/sku-black.jpg", selected: false, selectable: true },
      { value: "多彩", image_url: "https://img.kwcdn.com/product/fancy/sku-color.jpg", selected: true, selectable: true },
      { value: "卡其色", image_url: "https://img.kwcdn.com/product/fancy/sku-khaki.jpg", selected: false, selectable: true }
    ]
  }]);
});

test("builds every color by size Temu SKU combination and reuses the color image", () => {
  const left = new FakeElement("div", { id: "leftContent" });
  const right = new FakeElement("div", { id: "rightContent" }).append(
    new FakeElement("div", { text: "颜色" }),
    new FakeElement("div", { attrs: { role: "radio", "aria-label": "米白", "aria-checked": "false" } }).append(
      new FakeElement("img", { attrs: { src: "https://img.kwcdn.com/product/fancy/sofa-cream.jpg?imageView2/2/w/180" } })
    ),
    new FakeElement("div", { attrs: { role: "radio", "aria-label": "灰色", "aria-checked": "true" } }).append(
      new FakeElement("img", { attrs: { src: "https://img.kwcdn.com/product/fancy/sofa-gray.jpg?imageView2/2/w/180" } })
    ),
    new FakeElement("div", { text: "尺码" }),
    new FakeElement("div", { attrs: { role: "radio", "aria-label": "单座 70*70cm" } }),
    new FakeElement("div", { attrs: { role: "radio", "aria-label": "双人沙发 90*160cm" } }),
    new FakeElement("div", { attrs: { role: "radio", "aria-label": "三座 90*180cm" } }),
    new FakeElement("div", { text: "数量" })
  );
  const goodsPrice = new FakeElement("div", { id: "goods_price" }).append(
    new FakeElement("span", { text: "$10.49" })
  );

  const result = TemuDomCapture.extract(fakeDocument(left, right, goodsPrice), fakeView);

  assert.equal(result.sku_groups.length, 2);
  assert.equal(result.variant_combinations.length, 6);
  assert.deepEqual(result.variant_combinations[0], {
    attributes: { "颜色": "米白", "尺码": "单座 70*70cm" },
    image_url: "https://img.kwcdn.com/product/fancy/sofa-cream.jpg",
    price: "$10.49",
    currency: "USD",
    selected: false,
    selectable: true,
    source: "temu-semantic-groups",
    confidence: "medium"
  });
  assert.equal(result.variant_combinations[2].image_url, "https://img.kwcdn.com/product/fancy/sofa-cream.jpg");
  assert.equal(result.variant_combinations[3].image_url, "https://img.kwcdn.com/product/fancy/sofa-gray.jpg");
  assert.deepEqual(result.variant_combinations[5].attributes, { "颜色": "灰色", "尺码": "三座 90*180cm" });
});

test("keeps every SKU dimension when the cartesian product exceeds the capture limit", () => {
  const groups = [
    { source_name: "颜色", values: Array.from({ length: 20 }, (_, index) => ({ value: `颜色${index + 1}` })) },
    { source_name: "尺码", values: Array.from({ length: 10 }, (_, index) => ({ value: `尺码${index + 1}` })) },
    { source_name: "包装", values: [{ value: "单件" }, { value: "双件" }] }
  ];

  const combinations = TemuDomCapture.skuCombinations(groups, { value: "$9.99", currency: "USD" });

  assert.equal(combinations.length, 200);
  assert.ok(combinations.every((combo) => Object.keys(combo.attributes).length === 3));
  assert.ok(combinations.every((combo) => combo.currency === "USD"));
});

test("extension injects the semantic helper before product extraction and keeps it in the MAIN world", async () => {
  const manifest = JSON.parse(await readFile(new URL("./manifest.json", import.meta.url), "utf8"));
  const mainWorldScripts = manifest.content_scripts.find((entry) => entry.world === "MAIN")?.js || [];
  const background = await readFile(new URL("./background.js", import.meta.url), "utf8");

  assert.ok(mainWorldScripts.includes("temu_dom_capture.js"));
  assert.match(background, /ensureTemuDomCaptureHelper\(tab\.id\)/);
  assert.match(background, /WorkbenchTemuDomCapture\.extract\(document, window\)/);
  assert.match(background, /addImage\(item\?\.url,\s*38,\s*"temu-semantic-gallery"/);
  assert.match(background, /\.\.\.temuSemanticCapture\.sku_groups/);
  assert.match(background, /temuSemanticCapture\.variant_combinations/);
  assert.match(background, /temuSemanticCapture\.current_price/);
  assert.match(background, /mergeComboEvidence\(groupCombos, jsonVariantData\.combos\)/);
  assert.match(background, /combo\.price\s*\|\|\s*capturedPrice/);
  assert.match(background, /const productImageLimit = platform === "temu" \? 24 : 6/);
});

test("extension upgrades repair stale controls on open Temu pages and returns capture exceptions", async () => {
  const background = await readFile(new URL("./background.js", import.meta.url), "utf8");
  const content = await readFile(new URL("./content.js", import.meta.url), "utf8");

  assert.match(background, /function isWorkbenchPageControlTab\(tab\)/);
  assert.match(background, /\(temu\|1688\|alibaba\|pinduoduo\|yangkeduo\|amazon\)\\\.com/);
  assert.match(background, /async function ensureOpenWorkbenchPageControls\(\)/);
  assert.match(background, /https:\/\/\*\.temu\.com\/\*/);
  assert.match(background, /captureProductToWorkbench\(sender\.tab\)\.then\(sendResponse, \(error\) =>/);
  assert.match(background, /error:\s*"product_capture_runtime_failed"/);
  assert.match(content, /采集失败：\$\{message\.slice\(0, 48\)\}/);
});
