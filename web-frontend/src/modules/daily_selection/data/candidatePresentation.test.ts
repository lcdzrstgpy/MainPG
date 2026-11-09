import assert from "node:assert/strict";
import test from "node:test";

import {
  candidateAttributeEntries,
  candidateGalleryUrls,
  candidateSalesCount,
  candidateSalesLabel,
  candidateSkuPriceRange,
  candidateTierEntries,
  candidateWarningTags,
  estimateQuote,
  galleryCount,
  isCandidateComplete,
  parseSalesCount,
  reasonLabel,
  recommendationTags,
  riskLabel,
  sortCandidates,
} from "./candidatePresentation.ts";
import type { DailySelectionCandidate } from "../types.ts";

function candidate(overrides: Partial<DailySelectionCandidate> = {}): DailySelectionCandidate {
  return {
    candidate_id: "candidate-1",
    offer_id: "offer-1",
    source_platform: "1688",
    source_url: "https://detail.1688.com/offer/1.html",
    source_title: "示例商品",
    query_keyword: null,
    selection_result_label: null,
    listed_at: null,
    main_image_url: "https://img.example.com/1.jpg",
    source_image_urls: [],
    source_detail_image_urls: [],
    source_variant_records: [],
    source_attributes: {},
    price_cny: 9.9,
    min_order_quantity: 2,
    selection_score: 10,
    selection_reasons: [],
    risk_tags: [],
    status: "candidate",
    shop_name: null,
    location: null,
    sales_text: null,
    weight_text: null,
    package_info_text: null,
    freight_cny: null,
    captured_fields: [],
    missing_capture_fields: [],
    score_components: {},
    ...overrides,
  };
}

test("parses upstream sales copy into a number", () => {
  assert.equal(parseSalesCount("已售 1.2万"), 12000);
  assert.equal(parseSalesCount("5000+"), 5000);
  assert.equal(parseSalesCount("成交 3.5k"), 3500);
  assert.equal(parseSalesCount("1,024"), 1024);
  assert.equal(parseSalesCount(""), null);
  assert.equal(parseSalesCount(null), null);
  assert.equal(parseSalesCount("暂无数据"), null);
});

test("formats sales compactly and keeps a placeholder when missing", () => {
  assert.equal(candidateSalesLabel(candidate({ sales_text: "已售 1.2万" })), "1.2万");
  assert.equal(candidateSalesLabel(candidate({ sales_text: "5000+" })), "5,000");
  assert.equal(candidateSalesLabel(candidate({ sales_text: null })), "—");
});

test("falls back to summed SKU sales when the product-level sales text is missing", () => {
  const withSkuSales = candidate({
    sales_text: null,
    source_variant_records: [
      { sku_id: "s1", sales: 30 } as never,
      { sku_id: "s2", sales: 70 } as never,
    ],
  });
  assert.equal(candidateSalesCount(withSkuSales), 100);
  assert.equal(candidateSalesLabel(withSkuSales), "100");
});

test("keeps backend order for the default score sort", () => {
  const first = candidate({ candidate_id: "a" });
  const second = candidate({ candidate_id: "b" });
  assert.deepEqual(sortCandidates([second, first], "score").map((item) => item.candidate_id), ["b", "a"]);
});

test("sorts by sales descending with missing values last", () => {
  const high = candidate({ candidate_id: "high", sales_text: "1万" });
  const low = candidate({ candidate_id: "low", sales_text: "200" });
  const missing = candidate({ candidate_id: "missing", sales_text: null });
  assert.deepEqual(
    sortCandidates([low, missing, high], "sales").map((item) => item.candidate_id),
    ["high", "low", "missing"],
  );
});

test("sorts by price and MOQ ascending with missing values last", () => {
  const cheap = candidate({ candidate_id: "cheap", price_cny: 3, min_order_quantity: 1 });
  const pricey = candidate({ candidate_id: "pricey", price_cny: 30, min_order_quantity: 5 });
  const unknown = candidate({ candidate_id: "unknown", price_cny: null, min_order_quantity: null });
  assert.deepEqual(
    sortCandidates([pricey, unknown, cheap], "price").map((item) => item.candidate_id),
    ["cheap", "pricey", "unknown"],
  );
  assert.deepEqual(
    sortCandidates([pricey, unknown, cheap], "moq").map((item) => item.candidate_id),
    ["cheap", "pricey", "unknown"],
  );
});

test("treats price, main image and SKU records as the completeness trio", () => {
  assert.equal(isCandidateComplete(candidate()), false);
  assert.equal(
    isCandidateComplete(candidate({ source_variant_records: [{ sku_id: "s1" } as never] })),
    true,
  );
  assert.equal(isCandidateComplete(candidate({ main_image_url: null })), false);
  assert.equal(isCandidateComplete(candidate({ price_cny: null })), false);
});

test("maps risk tags and filter reasons to readable Chinese labels", () => {
  assert.equal(riskLabel("food"), "食品类");
  assert.equal(riskLabel("risk_ip"), "侵权风险");
  assert.equal(reasonLabel("missing_moq"), "起订量未知");
  assert.equal(reasonLabel("unknown_code"), "待复核");
  assert.deepEqual(
    candidateWarningTags(candidate({
      risk_tags: ["food", "food"],
      selection_reasons: ["risk_food", "moq_above_limit"],
    })),
    ["食品类", "起订量超限"],
  );
});

test("derives recommendation tags from fields when score components are absent", () => {
  const tags = recommendationTags(candidate({
    query_keyword: "收纳盒",
    source_image_urls: ["a", "b", "c"],
    source_attributes: { 材质: "PP" },
    source_variant_records: [{ sku_id: "s1" } as never],
  }));
  assert.deepEqual(tags, ["SKU 1 个", "图集 3 张", "属性齐全", "关键词命中"]);
  assert.equal(galleryCount(candidate({ source_image_urls: ["a", "b"] })), 2);
});

test("collects gallery urls in display order without duplicates", () => {
  const urls = candidateGalleryUrls(candidate({
    main_image_url: "main",
    source_image_urls: ["main", "gallery-1"],
    source_detail_image_urls: ["detail-1", "gallery-1"],
  }));
  assert.deepEqual(urls, ["main", "gallery-1", "detail-1"]);
});

test("flattens attribute values into label and text rows", () => {
  const entries = candidateAttributeEntries(candidate({
    source_attributes: {
      材质: "PP",
      尺寸: ["小", "大"],
      颜色: { 主色: "白", 辅色: "灰" },
      空值: null,
      " ": "忽略",
    },
  }));
  assert.deepEqual(entries, [
    { label: "材质", value: "PP" },
    { label: "尺寸", value: "小 / 大" },
    { label: "颜色", value: "主色: 白 / 辅色: 灰" },
  ]);
});

test("sorts tier prices by MOQ and drops invalid entries", () => {
  const tiers = candidateTierEntries(candidate({
    tiered_prices: [
      { min_order_quantity: 50, price_cny: 8 },
      { min_order_quantity: 2, price_cny: 9.5 },
      { min_order_quantity: 0, price_cny: 7 },
      { min_order_quantity: 100, price_cny: "abc" as never },
    ],
  }));
  assert.deepEqual(tiers, [
    { minOrderQuantity: 2, priceCny: 9.5 },
    { minOrderQuantity: 50, priceCny: 8 },
  ]);
  assert.deepEqual(candidateTierEntries(candidate()), []);
});

test("reports the SKU price range", () => {
  const range = candidateSkuPriceRange(candidate({
    source_variant_records: [
      { sku_id: "s1", price_cny: 12 } as never,
      { sku_id: "s2", price_cny: 6.5 } as never,
      { sku_id: "s3", price_cny: null } as never,
    ],
  }));
  assert.deepEqual(range, { min: 6.5, max: 12 });
  assert.equal(candidateSkuPriceRange(candidate()), null);
});

test("estimates landed cost and margin from CNY cost and exchange rate", () => {
  const quote = estimateQuote({
    priceCny: 10,
    freightCny: 2,
    exchangeRate: 7.2,
    salePriceUsd: 4,
  });
  assert.deepEqual(quote, {
    landedCostCny: 12,
    landedCostUsd: 1.67,
    profitUsd: 2.33,
    marginRate: 0.58,
    breakEvenUsd: 1.67,
  });
  assert.equal(estimateQuote({ priceCny: null, exchangeRate: 7.2 }), null);
  assert.equal(estimateQuote({ priceCny: 10, exchangeRate: 0 }), null);
});
