import assert from "node:assert/strict";
import test from "node:test";

import { variantPresentation } from "./skuPresentation.ts";

test("presents a multi-dimensional SKU with its captured image and separate attributes", () => {
  assert.deepEqual(variantPresentation({
    sku_id: "sofa-gray-double",
    attributes: { "颜色": "灰色", "尺码": "双人沙发 90*160cm" },
    image_url: "https://img.kwcdn.com/product/fancy/sofa-gray.jpg",
    source_price: 17.44,
    source_currency: "USD"
  }), {
    label: "灰色/双人沙发 90*160cm",
    imageUrl: "https://img.kwcdn.com/product/fancy/sofa-gray.jpg",
    priceLabel: "$17.44",
    attributes: [
      { name: "颜色", value: "灰色" },
      { name: "尺码", value: "双人沙发 90*160cm" }
    ]
  });
});

test("rejects unsafe SKU image schemes while preserving the attribute label", () => {
  assert.deepEqual(variantPresentation({
    attributes: { "颜色": "米白" },
    image_url: "javascript:alert(1)"
  }), {
    label: "米白",
    imageUrl: "",
    priceLabel: "-",
    attributes: [{ name: "颜色", value: "米白" }]
  });
});

test("uses the product main image for an old text-only SKU without its own image", () => {
  assert.equal(
    variantPresentation(
      { attributes: { "尺码": "双人沙发 90*160cm" } },
      '',
      'https://img.kwcdn.com/product/fancy/sofa-main.jpg',
    ).imageUrl,
    'https://img.kwcdn.com/product/fancy/sofa-main.jpg',
  );
});

test("uses the yuan symbol only for an actual CNY variant price", () => {
  assert.equal(variantPresentation({ price_cny: 19.59 }).priceLabel, "¥19.59");
  assert.equal(variantPresentation({ source_price: 10.49, source_currency: "CAD" }).priceLabel, "CA$10.49");
  assert.equal(variantPresentation({ price_cny: 17.44 }, "USD").priceLabel, "$17.44");
});
