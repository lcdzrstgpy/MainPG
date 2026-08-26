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

function packageMeasurementDocument(headers, rows) {
  const makeCell = (value) => ({ innerText: String(value), textContent: String(value) });
  const makeRow = (values, tagName = "TD") => ({
    querySelectorAll(selector) {
      assert.equal(selector, "th, td");
      return values.map(makeCell);
    },
    tagName: "TR",
    cellTagName: tagName
  });
  const table = {
    querySelectorAll(selector) {
      assert.equal(selector, "tr");
      return [makeRow(headers, "TH"), ...rows.map((row) => makeRow(row))];
    }
  };
  const root = {
    querySelectorAll(selector) {
      assert.equal(selector, "table");
      return [table];
    }
  };
  return {
    querySelector(selector) {
      assert.equal(selector, "#productPackInfo");
      return root;
    }
  };
}

async function load1688PackageMeasurementCapture(document) {
  const background = await readFile(new URL("./background.js", import.meta.url), "utf8");
  const start = background.indexOf("const capture1688ShippingPackageRecords =");
  const end = background.indexOf("\n    const cleanFreightContext", start);
  assert.notEqual(start, -1, "1688 package-measurement parser must exist");
  assert.notEqual(end, -1, "1688 package-measurement parser must end before freight parsing");
  const source = background.slice(start, end);
  return Function("document", "text", `${source}; return capture1688ShippingPackageRecords;`)(
    document,
    (value) => String(value || "").replace(/\s+/g, " ").trim()
  );
}

test("captures 1688 package rows by table headers and binds a unique normalized SKU", async () => {
  const capture = await load1688PackageMeasurementCapture(packageMeasurementDocument(
    ["重量(g)", "规格", "高(cm)", "体积(cm³)", "宽(cm)", "长(cm)"],
    [["1100", "颜色：拉丝本色；尺码：L", "6", "7488", "24", "52"]]
  ));
  const result = capture([{ sku_id: "SKU-1", spec_text: "颜色:拉丝本色; 尺码:L" }]);

  assert.deepEqual(result.shipping_package_records, [{
    variant_key: "SKU-1",
    specification: "颜色：拉丝本色；尺码：L",
    length_cm: 52,
    width_cm: 24,
    height_cm: 6,
    volume_cm3: 7488,
    weight_g: 1100,
    match_status: "matched",
    source: "1688_product_pack_info"
  }]);
  assert.deepEqual(result.source_variant_records[0].shipping_package, result.shipping_package_records[0]);
});

test("keeps only valid 1688 package rows and leaves unmatched rows detached from SKUs", async () => {
  const capture = await load1688PackageMeasurementCapture(packageMeasurementDocument(
    ["规格", "长(cm)", "宽(cm)", "高(cm)", "体积(cm³)", "重量(g)"],
    [
      ["蓝色", "52", "24", "6", "7488", "1200"],
      ["无效规格", "0", "24", "6", "0", "1200"]
    ]
  ));
  const result = capture([{ sku_id: "SKU-1", spec_text: "颜色:红色" }]);

  assert.deepEqual(result.shipping_package_records, [{
    variant_key: "蓝色",
    specification: "蓝色",
    length_cm: 52,
    width_cm: 24,
    height_cm: 6,
    volume_cm3: 7488,
    weight_g: 1200,
    match_status: "unmatched",
    source: "1688_product_pack_info"
  }]);
  assert.equal(result.source_variant_records[0].shipping_package, undefined);
});

test("captures a spec-only 1688 package table without dimension columns", async () => {
  // 真实商品级件重尺表常只含「规格 | 重量(g)」两列（如水龙头「【单档】…| 49」），
  // 不应因为缺少长/宽/高列而整表跳过。
  const capture = await load1688PackageMeasurementCapture(packageMeasurementDocument(
    ["规格", "重量(g)"],
    [["【单档】锌合金水龙头净水器", "49"], ["【双档】锌合金水龙头净水器", "49"]]
  ));
  const result = capture([{ sku_id: "SKU-1", spec_text: "型号：单档" }, { sku_id: "SKU-2", spec_text: "型号：双档" }]);
  assert.equal(result.has_package_root, true);
  assert.equal(result.package_info_status, "ok");
  assert.equal(result.package_info_error, "");
  assert.equal(result.shipping_package_records.length, 2);
  // 无尺寸列时，长/宽/高应为 null 而非被丢弃
  assert.equal(result.shipping_package_records[0].length_cm, null);
  assert.equal(result.shipping_package_records[0].width_cm, null);
  assert.equal(result.shipping_package_records[0].height_cm, null);
  assert.equal(result.shipping_package_records[0].weight_g, 49);
  assert.equal(result.shipping_package_records[1].weight_g, 49);
});

test("recognizes a 1688 package table whose spec column header is 颜色 instead of 规格", async () => {
  // 帆布包商品件重尺表头为「颜色 | 重量(g)」，规格列名是「颜色」而非「规格」。
  const capture = await load1688PackageMeasurementCapture(packageMeasurementDocument(
    ["颜色", "重量(g)"],
    [["【花漾迷你款】帆布包", "7g"], ["【柿柿如意】帆布包", "8g"], ["【粉红小】帆布包", "9g"]]
  ));
  const result = capture([{ sku_id: "SKU-1", spec_text: "花漾迷你款" }]);
  assert.equal(result.has_package_root, true);
  assert.equal(result.package_info_status, "ok");
  assert.equal(result.shipping_package_records.length, 3);
  assert.equal(result.shipping_package_records[0].weight_g, 7);
  assert.equal(result.shipping_package_records[1].weight_g, 8);
  assert.equal(result.shipping_package_records[2].weight_g, 9);
  // 规格列名是「颜色」，规格文本应被保留为商品名描述
  assert.equal(result.shipping_package_records[0].specification, "【花漾迷你款】帆布包");
});

test("captures 1688 package weight values carrying unit suffixes (e.g. 7g)", async () => {
  const capture = await load1688PackageMeasurementCapture(packageMeasurementDocument(
    ["规格", "重量(g)"],
    [["【花漾迷你款】帆布包", "7g"], ["【柿柿如意】帆布包", "8g"]]
  ));
  const result = capture([{ sku_id: "SKU-1", spec_text: "帆布包" }]);
  assert.equal(result.shipping_package_records.length, 2);
  assert.equal(result.shipping_package_records[0].weight_g, 7);
  assert.equal(result.shipping_package_records[1].weight_g, 8);
});

test("reports a clear package-info status when the 1688 module is present but empty", async () => {
  // 表头缺列（只有规格、无重量）时不会匹配为件重尺表 → records 为空 → status=empty
  const capture = await load1688PackageMeasurementCapture(packageMeasurementDocument(
    ["规格", "长(cm)", "宽(cm)"],
    [["蓝色", "52", "24"]]
  ));
  const result = capture([{ sku_id: "BLUE", spec_text: "颜色：蓝色" }]);
  assert.equal(result.has_package_root, true);
  assert.equal(result.package_info_status, "empty");
  assert.match(result.package_info_error, /未解析到有效的件重尺数据/);
});

test("matches 1688 package specifications strictly instead of binding 深红色 to 红色", async () => {
  const capture = await load1688PackageMeasurementCapture(packageMeasurementDocument(
    ["规格", "长(cm)", "宽(cm)", "高(cm)", "体积(cm³)", "重量(g)"],
    [["颜色：深红色", "52", "24", "6", "7488", "1200"]]
  ));
  const result = capture([
    { sku_id: "RED", spec_text: "颜色：红色" },
    { sku_id: "DARK-RED", spec_text: "颜色：深红色" }
  ]);

  assert.equal(result.shipping_package_records[0].match_status, "matched");
  assert.equal(result.shipping_package_records[0].variant_key, "DARK-RED");
  assert.equal(result.source_variant_records[0].shipping_package, undefined);
  assert.equal(result.source_variant_records[1].shipping_package.variant_key, "DARK-RED");
});

test("matches a 1688 商品名（黑色） package row only by its complete trailing specification value", async () => {
  const capture = await load1688PackageMeasurementCapture(packageMeasurementDocument(
    ["规格", "长(cm)", "宽(cm)", "高(cm)", "体积(cm³)", "重量(g)"],
    [
      ["201不锈钢抽拉式水龙头（黑色）", "52", "24", "6", "7488", "1150"],
      ["201不锈钢抽拉式水龙头（深红色）", "52", "24", "6", "7488", "1200"]
    ]
  ));
  const result = capture([
    { sku_id: "BLACK", attributes: { "颜色": "黑色" } },
    { sku_id: "RED", attributes: { "颜色": "红色" } },
    { sku_id: "DARK-RED", attributes: { "颜色": "深红色" } }
  ]);

  assert.equal(result.shipping_package_records[0].variant_key, "BLACK");
  assert.equal(result.shipping_package_records[1].variant_key, "DARK-RED");
  assert.equal(result.source_variant_records[1].shipping_package, undefined);
});

test("merges duplicate visible and structured SKU representations before matching 1688 package rows", async () => {
  const capture = await load1688PackageMeasurementCapture(packageMeasurementDocument(
    ["规格", "长(cm)", "宽(cm)", "高(cm)", "体积(cm³)", "重量(g)"],
    [["水龙头（黑色）", "52", "24", "6", "7488", "1150"]]
  ));
  const result = capture([
    { spec_text: "颜色/款式:黑色", source: "visible_sku_option" },
    { sku_id: "sku-black", spec_text: "颜色:黑色", attributes: { "颜色": "黑色" }, source: "structured_sku_data" }
  ]);

  assert.equal(result.shipping_package_records[0].match_status, "matched");
  assert.equal(result.shipping_package_records[0].variant_key, "sku-black");
  assert.equal(result.source_variant_records.length, 1);
  assert.equal(result.source_variant_records[0].sku_id, "sku-black");
  assert.equal(result.source_variant_records[0].shipping_package.variant_key, "sku-black");
});

test("keeps a package row unmatched when multiple distinct real SKU ids share its strict specification", async () => {
  const capture = await load1688PackageMeasurementCapture(packageMeasurementDocument(
    ["规格", "长(cm)", "宽(cm)", "高(cm)", "体积(cm³)", "重量(g)"],
    [["水龙头（黑色）", "52", "24", "6", "7488", "1150"]]
  ));
  const result = capture([
    { sku_id: "sku-black-a", attributes: { "颜色": "黑色" }, source: "structured_sku_data" },
    { sku_id: "sku-black-b", attributes: { "颜色": "黑色" }, source: "structured_sku_data" }
  ]);

  assert.equal(result.shipping_package_records[0].match_status, "unmatched");
  assert.equal(result.source_variant_records.length, 2);
  assert.equal(result.source_variant_records.some((record) => record.shipping_package), false);
});

test("converts explicit kilogram headers and rejects ambiguous 1688 package measurements", async () => {
  const kilograms = await load1688PackageMeasurementCapture(packageMeasurementDocument(
    ["规格", "长(cm)", "宽(cm)", "高(cm)", "体积(cm³)", "重量(kg)"],
    [["蓝色", "52", "24", "6", "7488", "1.2"]]
  ));
  const kilogramResult = kilograms([{ sku_id: "BLUE", spec_text: "颜色：蓝色" }]);
  assert.equal(kilogramResult.shipping_package_records[0].weight_g, 1200);

  const ambiguous = await load1688PackageMeasurementCapture(packageMeasurementDocument(
    ["规格", "长(cm)", "宽(cm)", "高(cm)", "体积(cm³)", "重量(g)"],
    [["蓝色", "约52", "24", "6", "7488", "1200-1300"]]
  ));
  assert.deepEqual(ambiguous([{ sku_id: "BLUE", spec_text: "颜色：蓝色" }]).shipping_package_records, []);
});

test("returns a product-level package weight only for one selected matched SKU", async () => {
  const capture = await load1688PackageMeasurementCapture(packageMeasurementDocument(
    ["规格", "长(cm)", "宽(cm)", "高(cm)", "体积(cm³)", "重量(g)"],
    [["蓝色", "52", "24", "6", "7488", "1200"]]
  ));
  const selected = capture([{ sku_id: "BLUE", spec_text: "颜色：蓝色", selected: true }]);
  assert.equal(selected.selected_shipping_package.weight_g, 1200);

  const notSelected = capture([{ sku_id: "BLUE", spec_text: "颜色：蓝色", selected: false }]);
  assert.equal(notSelected.selected_shipping_package, null);
});

test("runs 1688 package matching against every captured variant and derives global weight only from its selected package row", async () => {
  const background = await readFile(new URL("./background.js", import.meta.url), "utf8");
  assert.match(background, /capture1688ShippingPackageRecords\(variantRecords\)/);
  assert.doesNotMatch(background, /capture1688ShippingPackageRecords\(sourceVariantRecords\)/);
  assert.match(background, /const selectedPackageRecord = packageMeasurementCapture\.selected_shipping_package;/);
  assert.match(background, /weightText = selectedPackageRecord \? `重量 \$\{selectedPackageRecord\.weight_g\}g` : "";/);
  assert.match(background, /weightKg = selectedPackageRecord \? selectedPackageRecord\.weight_g \/ 1000 : null;/);
  assert.match(background, /weight_text_sample: packageMeasurementCapture\.has_package_root \? "" : contextOf\(/);
  assert.match(background, /const has1688PackageInfoRoot = Boolean\(document\.querySelector\("#productPackInfo"\)\);/);
  assert.match(background, /const capturePackageInfoText = \(\) => \{\s*if \(has1688PackageInfoRoot\) return "";/);
  assert.match(background, /const refreshWeightEvidence = \(\) => \{\s*if \(has1688PackageInfoRoot\) return;/);
  assert.match(background, /const initialCombinedWeight = has1688PackageInfoRoot \? \{ text: "", kg: null, source: "" \} : parseWeightFromText\(combinedText\);/);
});

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
  assert.match(background, /const hasTemuSemanticGallery = \/\(\^\|\\\.\)temu\\\.com\$\/i\.test\(host\) && temuSemanticCapture\.gallery_images\.length > 0/);
  assert.match(background, /if \(!hasTemuSemanticGallery\)\s*\{[\s\S]*document\.querySelectorAll\("img"\)\.forEach[\s\S]*document\.querySelectorAll\("\[style\*='background-image'\]"\)/);
  assert.match(background, /const productImageUrls = platform === "temu" && temuSemanticCapture\.gallery_images\.length\s*\?\s*semanticGalleryImageUrls/);
  assert.match(background, /const semanticGalleryImageUrls = temuSemanticCapture\.gallery_images[\s\S]*normalizeImageUrl\(item\?\.url\)/);
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
