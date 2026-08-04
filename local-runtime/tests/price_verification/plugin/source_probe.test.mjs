import assert from "node:assert/strict";
import { createRequire } from "node:module";
import path from "node:path";
import test from "node:test";

const require = createRequire(import.meta.url);
const extensionRoot = path.resolve(import.meta.dirname, "../../../wh_local/price_verification/plugin/extension");
const {
  bindSourceTaskEvidence,
  extractSpecialSaleCards,
  sourceSearchUrl,
  verify1688Sku,
} = require(path.join(extensionRoot, "page_probe.js"));
const { sanitizeResult } = require(path.join(extensionRoot, "network_probe_utils.js"));

test("special-sale cards expose only sourcing fields", () => {
  const root = {
    querySelectorAll: () => [
      {
        dataset: {
          offerId: "123456789",
          url: "https://detail.1688.com/offer/123456789.html?token=private",
          title: "Canvas travel bag",
          price: "¥12.50",
          imageUrl: "https://img.1688.com/bag.jpg",
          moq: "2件起批",
        },
      },
    ],
  };

  assert.deepEqual(extractSpecialSaleCards(root), [{
    offer_id: "123456789",
    source_url: "https://detail.1688.com/offer/123456789.html",
    source_title: "Canvas travel bag",
    main_image_url: "https://img.1688.com/bag.jpg",
    price: 12.5,
    moq: 2,
  }]);
});

test("1688 SKU verification returns variant, price, minimum order, and freight only", () => {
  const root = {
    querySelectorAll: () => [
      {
        dataset: {
          variant: "Black / Large",
          price: "13.80",
          moq: "3",
          freight: "8.00",
        },
      },
    ],
  };

  assert.deepEqual(verify1688Sku(root), [{
    sku_attributes: "Black / Large",
    price: 13.8,
    moq: 3,
    domestic_freight: 8,
  }]);
});

test("source result retains the image and SKC binding plus SKU verification per task", () => {
  assert.deepEqual(
    sanitizeResult({
      items: [{
        task_key: "SKC-1",
        skc_id: "SKC-1",
        main_image_url: "https://img.1688.com/source.jpg?token=private",
        source_quote_keys: ["SKC-1:SKU-1"],
        status: "succeeded",
        sku_verification: [{ sku_attributes: "Black", price: 12.5, moq: 2, domestic_freight: 8 }],
        candidates: [],
      }],
    }),
    {
      items: [{
        task_key: "SKC-1",
        skc_id: "SKC-1",
        main_image_url: "https://img.1688.com/source.jpg",
        source_quote_keys: ["SKC-1:SKU-1"],
        status: "succeeded",
        sku_verification: [{ sku_attributes: "Black", price: 12.5, moq: 2, domestic_freight: 8 }],
        candidates: [],
      }],
    },
  );
});

test("source task navigates with its image and SKC then only returns bound evidence", () => {
  const task = {
    task_key: "SKC-1",
    skc_id: "SKC-1",
    main_image_url: "https://images.example/sku-1.jpg",
    source_quote_keys: ["SKC-1:SKU-1"],
  };
  assert.equal(
    sourceSearchUrl(task),
    "https://s.1688.com/selloffer/offer_search.htm?image_url=https%3A%2F%2Fimages.example%2Fsku-1.jpg&skc_id=SKC-1",
  );
  assert.deepEqual(
    bindSourceTaskEvidence(task, {
      candidates: [
        { offer_id: "1", source_skc_id: "SKC-1", price: null, moq: null, main_image_url: "https://img.1688.com/1.jpg" },
        { offer_id: "2", source_skc_id: "SKC-2", price: 8, moq: 1, main_image_url: "https://img.1688.com/2.jpg" },
      ],
      sku_verification: [{ sku_attributes: "Black", price: 12.5, moq: 2, domestic_freight: 8 }],
    }),
    {
      task_key: "SKC-1",
      skc_id: "SKC-1",
      main_image_url: "https://images.example/sku-1.jpg",
      source_quote_keys: ["SKC-1:SKU-1"],
      candidates: [{
        offer_id: "1",
        source_skc_id: "SKC-1",
        price: 12.5,
        moq: 2,
        domestic_freight: 8,
        main_image_url: "https://img.1688.com/1.jpg",
        sku_attributes: "Black",
        variants: ["Black"],
      }],
      sku_verification: [{ sku_attributes: "Black", price: 12.5, moq: 2, domestic_freight: 8 }],
    },
  );
});
