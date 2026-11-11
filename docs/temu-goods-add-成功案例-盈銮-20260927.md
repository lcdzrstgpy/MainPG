# Temu 商品发布接口调用示例（盈銮集成服务端）

> 本示例取自一次**真实发布成功**的调用：Temu 方法 `bg.glo.goods.add`，
> 返回 `success=true`，生成商品 **productId = 9282408501**。
> 原始调用时间 2026-09-27 03:21:36，HTTP 200，耗时 1086ms。
> 以下已按**盈銮集成服务端**的对外契约改写，外部系统照此格式调用即可。

## 一、请求入口

| 项 | 值 |
|---|---|
| 服务地址 | https://yingluan-api.qibang-global.vip |
| 接口路径 | `POST /api/temu-product/openapi/router` |
| 兼容路径 | `POST /api/integrations/api/temu-product/openapi/router`（与上一行等价，服务端同时挂载） |
| Content-Type | `application/json` |
| 调用链路 | 外部系统 → 盈銮集成服务端 → Temu 官方 OpenAPI |

## 二、鉴权

用请求头传入分配给你的调用凭证：

```http
x-yl-client: your-client-id
x-yl-token:  your-yingluan-client-token
```

也可以放在 JSON 请求体里：`"client": "..."`、`"token": "..."`。

> 注意：`x-yl-client` / `x-yl-token` 是**盈銮给外部系统分配的调用凭证**，
> **不是** Temu 店铺的 `access_token`。两者是两套东西，别混用。

## 三、请求体结构

```text
{
  "type": "bg.glo.goods.add",
  "access_token": "<你的 Temu 店铺 access_token>",
  "gateway": "PARTNER",
  "request": { ... Temu 业务参数 ... }
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `type` | 是 | Temu 方法名，这里是 `bg.glo.goods.add` |
| `access_token` | 是 | **你自己店铺**的 Temu 授权 token |
| `gateway` | 是 | 商品网关。`bg.glo.goods.add` 属于 PARTNER 方法集，固定传 `PARTNER`；全托管 CN 方法传 `CN` |
| `request` | 是 | Temu 业务参数对象，即商品资料本体 |

### 不要传这些字段

`app_key`、`app_secret`、`sign`、`timestamp`、`data_type`、`version` 由**盈銮服务端统一生成**，
外部系统传了也不会被采用（`sign`、`app_key`、`timestamp` 等属于保留字段，服务端会忽略）。

## 四、完整请求示例（发布成功真实案例）

### 4.1 cURL

```bash
curl -X POST "https://yingluan-api.qibang-global.vip/api/temu-product/openapi/router" \
  -H "Content-Type: application/json" \
  -H "x-yl-client: your-client-id" \
  -H "x-yl-token: your-yingluan-client-token" \
  -d '<下方请求体>'
```

### 4.2 请求体（结构展示版，详情图已折叠）

```json
{
  "type": "bg.glo.goods.add",
  "access_token": "<你的 Temu 店铺 access_token>",
  "gateway": "PARTNER",
  "request": {
    "width": 800,
    "cat1Id": 9711,
    "cat2Id": 12015,
    "cat3Id": 12256,
    "cat4Id": 12262,
    "cat5Id": 0,
    "cat6Id": 0,
    "cat7Id": 0,
    "cat8Id": 0,
    "cat9Id": 0,
    "height": 800,
    "cat10Id": 0,
    "productName": "12Pcs Artificial Plant Set, 4 Large 18-Leaf Boston Ferns & 8 Camellia Stems, for Outdoor, Home Office, Garden Porch Farmhouse, Wedding Anniversary, Christmas Halloween Thanksgiving Decor",
    "materialImgUrl": "https://img.kwcdn.com/product/open/7f50db93fef04cd49270ab53dc0e76b9-goods.jpeg",
    "productSkcReqs": [
      {
        "width": 800,
        "height": 800,
        "extCode": "605698448579601",
        "isBasePlate": 0,
        "colorImageUrl": "https://img.kwcdn.com/product/open/65fbf6b33f364fa1a64f87c96226b15a-goods.jpeg",
        "previewImgUrls": [
          "https://img.kwcdn.com/product/open/65fbf6b33f364fa1a64f87c96226b15a-goods.jpeg"
        ],
        "productSkuReqs": [
          {
            "width": 800,
            "height": 800,
            "extCode": "605698448579601-sku-1",
            "thumbUrl": "https://img.kwcdn.com/product/open/65fbf6b33f364fa1a64f87c96226b15a-goods.jpeg",
            "currencyType": "CNY",
            "productSkuSpecReqs": [
              {
                "specId": 18191285,
                "specName": "4件",
                "parentSpecId": 15998553,
                "parentSpecName": "数量"
              }
            ],
            "siteSupplierPrices": [
              {
                "siteId": 100,
                "supplierPrice": 8500
              }
            ],
            "productSkuBarCodeReqs": [],
            "productSkuMultiPackReq": {
              "pieceUnitCode": 1,
              "numberOfPieces": 1,
              "pieceNewUnitCode": 1,
              "numberOfPiecesNew": 12,
              "skuClassification": 1,
              "productSkuNetContentReq": {}
            },
            "productSkuWhExtAttrReq": {
              "productSkuVolumeReq": {
                "len": 500,
                "width": 500,
                "height": 300
              },
              "productSkuWeightReq": {
                "value": 800000
              },
              "productSkuSensitiveAttrReq": {
                "isSensitive": 0,
                "sensitiveList": [],
                "sensitiveTypes": []
              },
              "productSkuSensitiveLimitReq": {}
            },
            "productSkuStockQuantityReq": {
              "warehouseStockQuantityReqs": [
                {
                  "warehouseId": "WH-03351775549570503",
                  "targetStockAvailable": 100
                }
              ]
            }
          }
        ],
        "mainProductSkuSpecReqs": [
          {
            "specId": 0,
            "specName": "",
            "parentSpecId": 0,
            "parentSpecName": ""
          }
        ]
      }
    ],
    "productI18nReqs": [
      {
        "language": "en",
        "productName": "12Pcs Artificial Plant Set, 4 Large 18-Leaf Boston Ferns & 8 Camellia Stems, for Outdoor, Home Office, Garden Porch Farmhouse, Wedding Anniversary, Christmas Halloween Thanksgiving Decor"
      }
    ],
    "productCustomReq": {
      "cat10Id": 0,
      "isRecommendedTag": false
    },
    "carouselImageUrls": [
      "https://img.kwcdn.com/product/open/7f50db93fef04cd49270ab53dc0e76b9-goods.jpeg",
      "https://img.kwcdn.com/product/open/65fbf6b33f364fa1a64f87c96226b15a-goods.jpeg",
      "https://img.kwcdn.com/product/open/f3e91fd989864413be375b6baa6ab6af-goods.jpeg",
      "https://img.kwcdn.com/product/open/a18cdef8564c4c539c966387e1b4ccf8-goods.jpeg",
      "https://img.kwcdn.com/product/open/a191a143ea674c74a596a6de42211d29-goods.jpeg",
      "https://img.kwcdn.com/product/open/ae13e2aab2f24e3fb388a7a9304382b8-goods.jpeg",
      "https://img.kwcdn.com/product/open/b926d70670ca40048c9e5920384fde27-goods.jpeg",
      "https://img.kwcdn.com/product/open/a935c57e10c24802aa0a6f4947c715d4-goods.jpeg",
      "https://img.kwcdn.com/product/open/9a62bf30b16b4058a853bb71d102b085-goods.jpeg"
    ],
    "productShipmentReq": {
      "freightTemplateId": "HFT-18431274984529990503",
      "shipmentLimitSecond": 777600
    },
    "productPropertyReqs": [
      {
        "pid": 842,
        "vid": 37864,
        "refPid": 596,
        "propName": "植物类型",
        "propValue": "其他植物",
        "valueUnit": "",
        "templatePid": 1701674
      },
      {
        "pid": 26,
        "vid": 3768,
        "refPid": 96,
        "propName": "场合",
        "propValue": "圣诞节",
        "valueUnit": "",
        "templatePid": 1701675
      },
      {
        "pid": 1013,
        "vid": 25073,
        "refPid": 423,
        "propName": "节假日",
        "propValue": "圣诞节",
        "valueUnit": "",
        "templatePid": 1701676
      },
      {
        "pid": 1460,
        "vid": 37545,
        "refPid": 1948,
        "propName": "是否含容器",
        "propValue": "不含容器",
        "valueUnit": "",
        "templatePid": 1701677
      },
      {
        "pid": 1053,
        "vid": 25595,
        "refPid": 732,
        "propName": "放置",
        "propValue": "悬挂",
        "valueUnit": "",
        "templatePid": 1701678
      },
      {
        "pid": 89,
        "vid": 2148,
        "refPid": 121,
        "propName": "材料",
        "propValue": "塑料",
        "valueUnit": "",
        "templatePid": 1701679
      },
      {
        "pid": 90,
        "vid": 22481,
        "refPid": 241,
        "propName": "房型",
        "propValue": "户外庭院",
        "valueUnit": "",
        "templatePid": 1701680
      },
      {
        "pid": 305,
        "vid": 25601,
        "refPid": 235,
        "propName": "具体用途",
        "propValue": "家居装饰",
        "valueUnit": "",
        "templatePid": 1701681
      },
      {
        "pid": 125,
        "vid": 3473,
        "refPid": 203,
        "propName": "四季",
        "propValue": "冬天",
        "valueUnit": "",
        "templatePid": 1701682
      },
      {
        "pid": 1425,
        "vid": 36627,
        "refPid": 1561,
        "propName": "供电方式",
        "propValue": "无需接电使用",
        "valueUnit": "",
        "templatePid": 1701684
      },
      {
        "pid": 1539,
        "vid": 52032,
        "refPid": 2153,
        "propName": "电池属性",
        "propValue": "不带电池",
        "valueUnit": "",
        "templatePid": 1701685
      },
      {
        "pid": 1742,
        "vid": 401588,
        "refPid": 7644,
        "propName": "是否包含真植物",
        "propValue": "否",
        "valueUnit": "",
        "templatePid": 1701693
      }
    ],
    "productWhExtAttrReq": {
      "outerGoodsUrl": "https://www.temu.com/us-zh-Hans/4-g-605698448579601.html",
      "productOrigin": {
        "region2Id": 43000000000006,
        "region1ShortName": "CN"
      }
    },
    "productSecondHandReq": {
      "isSecondHand": false
    },
    "personalizationSwitch": 0,
    "productSemiManagedReq": {
      "bindSiteIds": [
        100
      ],
      "semiLanguageStrategy": 1
    },
    "productSpecPropertyReqs": [
      {
        "pid": 15998553,
        "vid": 0,
        "refPid": 15998553,
        "specId": 18191285,
        "propName": "数量",
        "specName": "4件",
        "propValue": "4件",
        "valueUnit": "",
        "templatePid": 15998553,
        "parentSpecId": 15998553,
        "valueGroupId": 0,
        "parentSpecName": "数量",
        "valueGroupName": ""
      }
    ],
    "goodsLayerDecorationReqs": [
      {
        "key": "DecImage",
        "lang": "en",
        "type": "image",
        "priority": 1,
        "contentList": [
          {
            "key": "DecImage",
            "width": 800,
            "height": 800,
            "imgUrl": "https://img.kwcdn.com/product/open/1205ab642c3644899ff5578d998a2be1-goods.jpeg"
          }
        ]
      },
      {
        "key": "DecImage",
        "lang": "en",
        "type": "image",
        "priority": 2,
        "contentList": [
          {
            "key": "DecImage",
            "width": 800,
            "height": 800,
            "imgUrl": "https://img.kwcdn.com/product/open/a7ed40c77f2d4d51ab35e7a30bcc9722-goods.jpeg"
          }
        ]
      },
      {
        "key": "DecImage",
        "lang": "en",
        "type": "image",
        "priority": 3,
        "contentList": [
          {
            "key": "DecImage",
            "width": 800,
            "height": 800,
            "imgUrl": "https://img.kwcdn.com/product/open/6d0c2ee5aaad42ab860e1e9eddf40c97-goods.jpeg"
          }
        ]
      },
      {
        "...": "其余 17 组 DecImage 结构相同，priority 4~20"
      }
    ],
    "productWarehouseRouteReq": {
      "targetRouteList": [
        {
          "siteIdList": [
            100
          ],
          "warehouseId": "WH-03351775549570503"
        }
      ]
    }
  }
}
```

### 4.3 请求体（完整版，含全部 20 组详情图）

```json
{
  "type": "bg.glo.goods.add",
  "access_token": "<你的 Temu 店铺 access_token>",
  "gateway": "PARTNER",
  "request": {
    "width": 800,
    "cat1Id": 9711,
    "cat2Id": 12015,
    "cat3Id": 12256,
    "cat4Id": 12262,
    "cat5Id": 0,
    "cat6Id": 0,
    "cat7Id": 0,
    "cat8Id": 0,
    "cat9Id": 0,
    "height": 800,
    "cat10Id": 0,
    "productName": "12Pcs Artificial Plant Set, 4 Large 18-Leaf Boston Ferns & 8 Camellia Stems, for Outdoor, Home Office, Garden Porch Farmhouse, Wedding Anniversary, Christmas Halloween Thanksgiving Decor",
    "materialImgUrl": "https://img.kwcdn.com/product/open/7f50db93fef04cd49270ab53dc0e76b9-goods.jpeg",
    "productSkcReqs": [
      {
        "width": 800,
        "height": 800,
        "extCode": "605698448579601",
        "isBasePlate": 0,
        "colorImageUrl": "https://img.kwcdn.com/product/open/65fbf6b33f364fa1a64f87c96226b15a-goods.jpeg",
        "previewImgUrls": [
          "https://img.kwcdn.com/product/open/65fbf6b33f364fa1a64f87c96226b15a-goods.jpeg"
        ],
        "productSkuReqs": [
          {
            "width": 800,
            "height": 800,
            "extCode": "605698448579601-sku-1",
            "thumbUrl": "https://img.kwcdn.com/product/open/65fbf6b33f364fa1a64f87c96226b15a-goods.jpeg",
            "currencyType": "CNY",
            "productSkuSpecReqs": [
              {
                "specId": 18191285,
                "specName": "4件",
                "parentSpecId": 15998553,
                "parentSpecName": "数量"
              }
            ],
            "siteSupplierPrices": [
              {
                "siteId": 100,
                "supplierPrice": 8500
              }
            ],
            "productSkuBarCodeReqs": [],
            "productSkuMultiPackReq": {
              "pieceUnitCode": 1,
              "numberOfPieces": 1,
              "pieceNewUnitCode": 1,
              "numberOfPiecesNew": 12,
              "skuClassification": 1,
              "productSkuNetContentReq": {}
            },
            "productSkuWhExtAttrReq": {
              "productSkuVolumeReq": {
                "len": 500,
                "width": 500,
                "height": 300
              },
              "productSkuWeightReq": {
                "value": 800000
              },
              "productSkuSensitiveAttrReq": {
                "isSensitive": 0,
                "sensitiveList": [],
                "sensitiveTypes": []
              },
              "productSkuSensitiveLimitReq": {}
            },
            "productSkuStockQuantityReq": {
              "warehouseStockQuantityReqs": [
                {
                  "warehouseId": "WH-03351775549570503",
                  "targetStockAvailable": 100
                }
              ]
            }
          }
        ],
        "mainProductSkuSpecReqs": [
          {
            "specId": 0,
            "specName": "",
            "parentSpecId": 0,
            "parentSpecName": ""
          }
        ]
      }
    ],
    "productI18nReqs": [
      {
        "language": "en",
        "productName": "12Pcs Artificial Plant Set, 4 Large 18-Leaf Boston Ferns & 8 Camellia Stems, for Outdoor, Home Office, Garden Porch Farmhouse, Wedding Anniversary, Christmas Halloween Thanksgiving Decor"
      }
    ],
    "productCustomReq": {
      "cat10Id": 0,
      "isRecommendedTag": false
    },
    "carouselImageUrls": [
      "https://img.kwcdn.com/product/open/7f50db93fef04cd49270ab53dc0e76b9-goods.jpeg",
      "https://img.kwcdn.com/product/open/65fbf6b33f364fa1a64f87c96226b15a-goods.jpeg",
      "https://img.kwcdn.com/product/open/f3e91fd989864413be375b6baa6ab6af-goods.jpeg",
      "https://img.kwcdn.com/product/open/a18cdef8564c4c539c966387e1b4ccf8-goods.jpeg",
      "https://img.kwcdn.com/product/open/a191a143ea674c74a596a6de42211d29-goods.jpeg",
      "https://img.kwcdn.com/product/open/ae13e2aab2f24e3fb388a7a9304382b8-goods.jpeg",
      "https://img.kwcdn.com/product/open/b926d70670ca40048c9e5920384fde27-goods.jpeg",
      "https://img.kwcdn.com/product/open/a935c57e10c24802aa0a6f4947c715d4-goods.jpeg",
      "https://img.kwcdn.com/product/open/9a62bf30b16b4058a853bb71d102b085-goods.jpeg"
    ],
    "productShipmentReq": {
      "freightTemplateId": "HFT-18431274984529990503",
      "shipmentLimitSecond": 777600
    },
    "productPropertyReqs": [
      {
        "pid": 842,
        "vid": 37864,
        "refPid": 596,
        "propName": "植物类型",
        "propValue": "其他植物",
        "valueUnit": "",
        "templatePid": 1701674
      },
      {
        "pid": 26,
        "vid": 3768,
        "refPid": 96,
        "propName": "场合",
        "propValue": "圣诞节",
        "valueUnit": "",
        "templatePid": 1701675
      },
      {
        "pid": 1013,
        "vid": 25073,
        "refPid": 423,
        "propName": "节假日",
        "propValue": "圣诞节",
        "valueUnit": "",
        "templatePid": 1701676
      },
      {
        "pid": 1460,
        "vid": 37545,
        "refPid": 1948,
        "propName": "是否含容器",
        "propValue": "不含容器",
        "valueUnit": "",
        "templatePid": 1701677
      },
      {
        "pid": 1053,
        "vid": 25595,
        "refPid": 732,
        "propName": "放置",
        "propValue": "悬挂",
        "valueUnit": "",
        "templatePid": 1701678
      },
      {
        "pid": 89,
        "vid": 2148,
        "refPid": 121,
        "propName": "材料",
        "propValue": "塑料",
        "valueUnit": "",
        "templatePid": 1701679
      },
      {
        "pid": 90,
        "vid": 22481,
        "refPid": 241,
        "propName": "房型",
        "propValue": "户外庭院",
        "valueUnit": "",
        "templatePid": 1701680
      },
      {
        "pid": 305,
        "vid": 25601,
        "refPid": 235,
        "propName": "具体用途",
        "propValue": "家居装饰",
        "valueUnit": "",
        "templatePid": 1701681
      },
      {
        "pid": 125,
        "vid": 3473,
        "refPid": 203,
        "propName": "四季",
        "propValue": "冬天",
        "valueUnit": "",
        "templatePid": 1701682
      },
      {
        "pid": 1425,
        "vid": 36627,
        "refPid": 1561,
        "propName": "供电方式",
        "propValue": "无需接电使用",
        "valueUnit": "",
        "templatePid": 1701684
      },
      {
        "pid": 1539,
        "vid": 52032,
        "refPid": 2153,
        "propName": "电池属性",
        "propValue": "不带电池",
        "valueUnit": "",
        "templatePid": 1701685
      },
      {
        "pid": 1742,
        "vid": 401588,
        "refPid": 7644,
        "propName": "是否包含真植物",
        "propValue": "否",
        "valueUnit": "",
        "templatePid": 1701693
      }
    ],
    "productWhExtAttrReq": {
      "outerGoodsUrl": "https://www.temu.com/us-zh-Hans/4-g-605698448579601.html",
      "productOrigin": {
        "region2Id": 43000000000006,
        "region1ShortName": "CN"
      }
    },
    "productSecondHandReq": {
      "isSecondHand": false
    },
    "personalizationSwitch": 0,
    "productSemiManagedReq": {
      "bindSiteIds": [
        100
      ],
      "semiLanguageStrategy": 1
    },
    "productSpecPropertyReqs": [
      {
        "pid": 15998553,
        "vid": 0,
        "refPid": 15998553,
        "specId": 18191285,
        "propName": "数量",
        "specName": "4件",
        "propValue": "4件",
        "valueUnit": "",
        "templatePid": 15998553,
        "parentSpecId": 15998553,
        "valueGroupId": 0,
        "parentSpecName": "数量",
        "valueGroupName": ""
      }
    ],
    "goodsLayerDecorationReqs": [
      {
        "key": "DecImage",
        "lang": "en",
        "type": "image",
        "priority": 1,
        "contentList": [
          {
            "key": "DecImage",
            "width": 800,
            "height": 800,
            "imgUrl": "https://img.kwcdn.com/product/open/1205ab642c3644899ff5578d998a2be1-goods.jpeg"
          }
        ]
      },
      {
        "key": "DecImage",
        "lang": "en",
        "type": "image",
        "priority": 2,
        "contentList": [
          {
            "key": "DecImage",
            "width": 800,
            "height": 800,
            "imgUrl": "https://img.kwcdn.com/product/open/a7ed40c77f2d4d51ab35e7a30bcc9722-goods.jpeg"
          }
        ]
      },
      {
        "key": "DecImage",
        "lang": "en",
        "type": "image",
        "priority": 3,
        "contentList": [
          {
            "key": "DecImage",
            "width": 800,
            "height": 800,
            "imgUrl": "https://img.kwcdn.com/product/open/6d0c2ee5aaad42ab860e1e9eddf40c97-goods.jpeg"
          }
        ]
      },
      {
        "key": "DecImage",
        "lang": "en",
        "type": "image",
        "priority": 4,
        "contentList": [
          {
            "key": "DecImage",
            "width": 800,
            "height": 800,
            "imgUrl": "https://img.kwcdn.com/product/open/df6b030ae6b54ec1b09aee06cb070aba-goods.jpeg"
          }
        ]
      },
      {
        "key": "DecImage",
        "lang": "en",
        "type": "image",
        "priority": 5,
        "contentList": [
          {
            "key": "DecImage",
            "width": 800,
            "height": 800,
            "imgUrl": "https://img.kwcdn.com/product/open/b308e8044e954732bcbcf8857830086c-goods.jpeg"
          }
        ]
      },
      {
        "key": "DecImage",
        "lang": "en",
        "type": "image",
        "priority": 6,
        "contentList": [
          {
            "key": "DecImage",
            "width": 800,
            "height": 800,
            "imgUrl": "https://img.kwcdn.com/product/open/599c1e396bc0481fbe63559839042fd1-goods.jpeg"
          }
        ]
      },
      {
        "key": "DecImage",
        "lang": "en",
        "type": "image",
        "priority": 7,
        "contentList": [
          {
            "key": "DecImage",
            "width": 800,
            "height": 800,
            "imgUrl": "https://img.kwcdn.com/product/open/da92ed58c88a4c5aab55c65d43853a91-goods.jpeg"
          }
        ]
      },
      {
        "key": "DecImage",
        "lang": "en",
        "type": "image",
        "priority": 8,
        "contentList": [
          {
            "key": "DecImage",
            "width": 800,
            "height": 800,
            "imgUrl": "https://img.kwcdn.com/product/open/aa13ac8dc1f1421eb488b39e73684126-goods.jpeg"
          }
        ]
      },
      {
        "key": "DecImage",
        "lang": "en",
        "type": "image",
        "priority": 9,
        "contentList": [
          {
            "key": "DecImage",
            "width": 800,
            "height": 800,
            "imgUrl": "https://img.kwcdn.com/product/open/63ad54c6656740b28c4fe403ce7fccd3-goods.jpeg"
          }
        ]
      },
      {
        "key": "DecImage",
        "lang": "en",
        "type": "image",
        "priority": 10,
        "contentList": [
          {
            "key": "DecImage",
            "width": 800,
            "height": 800,
            "imgUrl": "https://img.kwcdn.com/product/open/714d047a47b04a4f8ca8038b4466428d-goods.jpeg"
          }
        ]
      },
      {
        "key": "DecImage",
        "lang": "en",
        "type": "image",
        "priority": 11,
        "contentList": [
          {
            "key": "DecImage",
            "width": 800,
            "height": 800,
            "imgUrl": "https://img.kwcdn.com/product/open/1205ab642c3644899ff5578d998a2be1-goods.jpeg"
          }
        ]
      },
      {
        "key": "DecImage",
        "lang": "en",
        "type": "image",
        "priority": 12,
        "contentList": [
          {
            "key": "DecImage",
            "width": 800,
            "height": 800,
            "imgUrl": "https://img.kwcdn.com/product/open/a7ed40c77f2d4d51ab35e7a30bcc9722-goods.jpeg"
          }
        ]
      },
      {
        "key": "DecImage",
        "lang": "en",
        "type": "image",
        "priority": 13,
        "contentList": [
          {
            "key": "DecImage",
            "width": 800,
            "height": 800,
            "imgUrl": "https://img.kwcdn.com/product/open/6d0c2ee5aaad42ab860e1e9eddf40c97-goods.jpeg"
          }
        ]
      },
      {
        "key": "DecImage",
        "lang": "en",
        "type": "image",
        "priority": 14,
        "contentList": [
          {
            "key": "DecImage",
            "width": 800,
            "height": 800,
            "imgUrl": "https://img.kwcdn.com/product/open/df6b030ae6b54ec1b09aee06cb070aba-goods.jpeg"
          }
        ]
      },
      {
        "key": "DecImage",
        "lang": "en",
        "type": "image",
        "priority": 15,
        "contentList": [
          {
            "key": "DecImage",
            "width": 800,
            "height": 800,
            "imgUrl": "https://img.kwcdn.com/product/open/b308e8044e954732bcbcf8857830086c-goods.jpeg"
          }
        ]
      },
      {
        "key": "DecImage",
        "lang": "en",
        "type": "image",
        "priority": 16,
        "contentList": [
          {
            "key": "DecImage",
            "width": 800,
            "height": 800,
            "imgUrl": "https://img.kwcdn.com/product/open/599c1e396bc0481fbe63559839042fd1-goods.jpeg"
          }
        ]
      },
      {
        "key": "DecImage",
        "lang": "en",
        "type": "image",
        "priority": 17,
        "contentList": [
          {
            "key": "DecImage",
            "width": 800,
            "height": 800,
            "imgUrl": "https://img.kwcdn.com/product/open/da92ed58c88a4c5aab55c65d43853a91-goods.jpeg"
          }
        ]
      },
      {
        "key": "DecImage",
        "lang": "en",
        "type": "image",
        "priority": 18,
        "contentList": [
          {
            "key": "DecImage",
            "width": 800,
            "height": 800,
            "imgUrl": "https://img.kwcdn.com/product/open/aa13ac8dc1f1421eb488b39e73684126-goods.jpeg"
          }
        ]
      },
      {
        "key": "DecImage",
        "lang": "en",
        "type": "image",
        "priority": 19,
        "contentList": [
          {
            "key": "DecImage",
            "width": 800,
            "height": 800,
            "imgUrl": "https://img.kwcdn.com/product/open/63ad54c6656740b28c4fe403ce7fccd3-goods.jpeg"
          }
        ]
      },
      {
        "key": "DecImage",
        "lang": "en",
        "type": "image",
        "priority": 20,
        "contentList": [
          {
            "key": "DecImage",
            "width": 800,
            "height": 800,
            "imgUrl": "https://img.kwcdn.com/product/open/714d047a47b04a4f8ca8038b4466428d-goods.jpeg"
          }
        ]
      }
    ],
    "productWarehouseRouteReq": {
      "targetRouteList": [
        {
          "siteIdList": [
            100
          ],
          "warehouseId": "WH-03351775549570503"
        }
      ]
    }
  }
}
```

## 五、成功响应

盈銮**原样透传** Temu 的响应：

```json
{
  "result": {
    "productId": 9282408501,
    "productSkcList": [
      {
        "productSkcId": 34954585338
      }
    ],
    "productSkuList": [
      {
        "extCode": "605698448579601-sku-1",
        "skuSpecList": [
          {
            "specId": 18191285,
            "specName": "4件",
            "parentSpecId": 15998553,
            "parentSpecName": "数量"
          }
        ],
        "productSkcId": 34954585338,
        "productSkuId": 34565971503
      }
    ]
  },
  "success": true,
  "errorMsg": "",
  "errorCode": 1000000,
  "requestId": "pa-0ef9be42-40fb-462b-81e0-096c5ce51522"
}
```

| 返回字段 | 值 |
|---|---|
| `success` | true |
| `errorCode` | 1000000（成功） |
| `result.productId` | **9282408501**（新商品 ID） |
| `result.productSkcId` | 34954585338 |
| `result.productSkuId` | 34565971503 |

## 六、关键业务参数说明

| 参数 | 本案例取值 | 说明 |
|---|---|---|
| `productName` | 12Pcs Artificial Plant Set, 4 Large 18-L… | 商品标题 |
| `cat1Id`~`cat4Id` | 9711 / 12015 / 12256 / 12262 | 类目链，由 `bg.glo.goods.cats.get` + `category.match` 得到 |
| `productSkcReqs` | 1 个 SKC、1 个 SKU | SKC/SKU 主体，含规格、价格、体积重量、库存 |
| `productSkuSpecReqs` | 数量=4件（specId 18191285） | SKU 销售属性，specId 需先调 `bg.glo.goods.spec.create` 创建 |
| `siteSupplierPrices` | siteId=100，supplierPrice=8500 | 供货价，单位 0.01 元 → 85 元 |
| `productSkuVolumeReq` | 500×500×300 | 体积，单位 0.1mm → 50×50×30 cm |
| `productSkuWeightReq` | 800000 | 重量，单位 0.001g → 800 g |
| `productSkuMultiPackReq` | numberOfPiecesNew=12 | 多件装数量 |
| `productSkuStockQuantityReq` | WH-03351775549570503 / 100 | 仓库与可用库存 |
| `productPropertyReqs` | 12 条 | 类目属性，取自 `bg.glo.goods.attrs.get` / `catsmandatory.get` |
| `productSpecPropertyReqs` | 1 条 | 销售属性声明 |
| `goodsLayerDecorationReqs` | 20 组 | 详情图，需先调 `bg.goods.image.upload.global` 拿到 URL |
| `productSemiManagedReq` | bindSiteIds=[100] | 半托管站点绑定 |
| `productWarehouseRouteReq` | siteIdList=[100]，WH-03351775549570503 | 仓库路由 |
| `productShipmentReq` | 运费模板 | 由 `bg.glo.logistics.template.get` 获取 |

## 七、发品前置链路

本次成功发布前，系统按顺序调用了这些接口（外部系统按同样顺序准备数据）：

```text
bg.glo.goods.cats.get            取类目
bg.glo.goods.attrs.get           取类目属性
bg.glo.goods.catsmandatory.get   取必填项
bg.glo.goods.category.match      类目匹配
bg.glo.logistics.template.get    取运费模板
bg.btg.goods.stock.warehouse.list.get  取仓库
bg.glo.goods.parentspec.get      取父规格
bg.glo.goods.spec.create         创建规格值，拿到 specId
bg.goods.image.upload.global     上传图片，拿到图片 URL（本例 17 张）
bg.glo.goods.add                 发布商品 ← 本示例
```

## 八、限流与错误

每个外部系统可单独配置 `rateLimitPerMinute` / `rateLimitPerHour` / `rateLimitPerDay`，
超限返回 HTTP 429：

```json
{
  "success": false,
  "errorCode": "RATE_LIMIT_EXCEEDED",
  "message": "Per-minute API call limit exceeded"
}
```

---

## 附录：内部直连 Temu 的原始请求体

下面是本系统在内部直连 Temu 时实际发出的请求体（`sign` 由内部服务生成）。
与上文盈銮格式的对照关系：

| 内部（直连 Temu） | 盈銮对外格式 |
|---|---|
| `type` 平铺在顶层 | 仍在顶层，作为路由方法名 |
| `access_token` 平铺在顶层 | 仍在顶层，含义不变 |
| 其余业务参数平铺在顶层 | 全部移入 `request` 对象 |
| `sign` 由调用方生成 | **删除**，改由盈銮服务端生成 |
| 无 | 新增 `gateway: "PARTNER"` |
| 无 | 新增请求头 `x-yl-client` / `x-yl-token` |

```json
{
  "sign": "",
  "type": "bg.glo.goods.add",
  "width": 800,
  "cat1Id": 9711,
  "cat2Id": 12015,
  "cat3Id": 12256,
  "cat4Id": 12262,
  "cat5Id": 0,
  "cat6Id": 0,
  "cat7Id": 0,
  "cat8Id": 0,
  "cat9Id": 0,
  "height": 800,
  "cat10Id": 0,
  "productName": "12Pcs Artificial Plant Set, 4 Large 18-Leaf Boston Ferns & 8 Camellia Stems, for Outdoor, Home Office, Garden Porch Farmhouse, Wedding Anniversary, Christmas Halloween Thanksgiving Decor",
  "access_token": "jpp7or***qww2f9",
  "materialImgUrl": "https://img.kwcdn.com/product/open/7f50db93fef04cd49270ab53dc0e76b9-goods.jpeg",
  "productSkcReqs": [
    {
      "width": 800,
      "height": 800,
      "extCode": "605698448579601",
      "isBasePlate": 0,
      "colorImageUrl": "https://img.kwcdn.com/product/open/65fbf6b33f364fa1a64f87c96226b15a-goods.jpeg",
      "previewImgUrls": [
        "https://img.kwcdn.com/product/open/65fbf6b33f364fa1a64f87c96226b15a-goods.jpeg"
      ],
      "productSkuReqs": [
        {
          "width": 800,
          "height": 800,
          "extCode": "605698448579601-sku-1",
          "thumbUrl": "https://img.kwcdn.com/product/open/65fbf6b33f364fa1a64f87c96226b15a-goods.jpeg",
          "currencyType": "CNY",
          "productSkuSpecReqs": [
            {
              "specId": 18191285,
              "specName": "4件",
              "parentSpecId": 15998553,
              "parentSpecName": "数量"
            }
          ],
          "siteSupplierPrices": [
            {
              "siteId": 100,
              "supplierPrice": 8500
            }
          ],
          "productSkuBarCodeReqs": [],
          "productSkuMultiPackReq": {
            "pieceUnitCode": 1,
            "numberOfPieces": 1,
            "pieceNewUnitCode": 1,
            "numberOfPiecesNew": 12,
            "skuClassification": 1,
            "productSkuNetContentReq": {}
          },
          "productSkuWhExtAttrReq": {
            "productSkuVolumeReq": {
              "len": 500,
              "width": 500,
              "height": 300
            },
            "productSkuWeightReq": {
              "value": 800000
            },
            "productSkuSensitiveAttrReq": {
              "isSensitive": 0,
              "sensitiveList": [],
              "sensitiveTypes": []
            },
            "productSkuSensitiveLimitReq": {}
          },
          "productSkuStockQuantityReq": {
            "warehouseStockQuantityReqs": [
              {
                "warehouseId": "WH-03351775549570503",
                "targetStockAvailable": 100
              }
            ]
          }
        }
      ],
      "mainProductSkuSpecReqs": [
        {
          "specId": 0,
          "specName": "",
          "parentSpecId": 0,
          "parentSpecName": ""
        }
      ]
    }
  ],
  "productI18nReqs": [
    {
      "language": "en",
      "productName": "12Pcs Artificial Plant Set, 4 Large 18-Leaf Boston Ferns & 8 Camellia Stems, for Outdoor, Home Office, Garden Porch Farmhouse, Wedding Anniversary, Christmas Halloween Thanksgiving Decor"
    }
  ],
  "productCustomReq": {
    "cat10Id": 0,
    "isRecommendedTag": false
  },
  "carouselImageUrls": [
    "https://img.kwcdn.com/product/open/7f50db93fef04cd49270ab53dc0e76b9-goods.jpeg",
    "https://img.kwcdn.com/product/open/65fbf6b33f364fa1a64f87c96226b15a-goods.jpeg",
    "https://img.kwcdn.com/product/open/f3e91fd989864413be375b6baa6ab6af-goods.jpeg",
    "https://img.kwcdn.com/product/open/a18cdef8564c4c539c966387e1b4ccf8-goods.jpeg",
    "https://img.kwcdn.com/product/open/a191a143ea674c74a596a6de42211d29-goods.jpeg",
    "https://img.kwcdn.com/product/open/ae13e2aab2f24e3fb388a7a9304382b8-goods.jpeg",
    "https://img.kwcdn.com/product/open/b926d70670ca40048c9e5920384fde27-goods.jpeg",
    "https://img.kwcdn.com/product/open/a935c57e10c24802aa0a6f4947c715d4-goods.jpeg",
    "https://img.kwcdn.com/product/open/9a62bf30b16b4058a853bb71d102b085-goods.jpeg"
  ],
  "productShipmentReq": {
    "freightTemplateId": "HFT-18431274984529990503",
    "shipmentLimitSecond": 777600
  },
  "productPropertyReqs": [
    {
      "pid": 842,
      "vid": 37864,
      "refPid": 596,
      "propName": "植物类型",
      "propValue": "其他植物",
      "valueUnit": "",
      "templatePid": 1701674
    },
    {
      "pid": 26,
      "vid": 3768,
      "refPid": 96,
      "propName": "场合",
      "propValue": "圣诞节",
      "valueUnit": "",
      "templatePid": 1701675
    },
    {
      "pid": 1013,
      "vid": 25073,
      "refPid": 423,
      "propName": "节假日",
      "propValue": "圣诞节",
      "valueUnit": "",
      "templatePid": 1701676
    },
    {
      "pid": 1460,
      "vid": 37545,
      "refPid": 1948,
      "propName": "是否含容器",
      "propValue": "不含容器",
      "valueUnit": "",
      "templatePid": 1701677
    },
    {
      "pid": 1053,
      "vid": 25595,
      "refPid": 732,
      "propName": "放置",
      "propValue": "悬挂",
      "valueUnit": "",
      "templatePid": 1701678
    },
    {
      "pid": 89,
      "vid": 2148,
      "refPid": 121,
      "propName": "材料",
      "propValue": "塑料",
      "valueUnit": "",
      "templatePid": 1701679
    },
    {
      "pid": 90,
      "vid": 22481,
      "refPid": 241,
      "propName": "房型",
      "propValue": "户外庭院",
      "valueUnit": "",
      "templatePid": 1701680
    },
    {
      "pid": 305,
      "vid": 25601,
      "refPid": 235,
      "propName": "具体用途",
      "propValue": "家居装饰",
      "valueUnit": "",
      "templatePid": 1701681
    },
    {
      "pid": 125,
      "vid": 3473,
      "refPid": 203,
      "propName": "四季",
      "propValue": "冬天",
      "valueUnit": "",
      "templatePid": 1701682
    },
    {
      "pid": 1425,
      "vid": 36627,
      "refPid": 1561,
      "propName": "供电方式",
      "propValue": "无需接电使用",
      "valueUnit": "",
      "templatePid": 1701684
    },
    {
      "pid": 1539,
      "vid": 52032,
      "refPid": 2153,
      "propName": "电池属性",
      "propValue": "不带电池",
      "valueUnit": "",
      "templatePid": 1701685
    },
    {
      "pid": 1742,
      "vid": 401588,
      "refPid": 7644,
      "propName": "是否包含真植物",
      "propValue": "否",
      "valueUnit": "",
      "templatePid": 1701693
    }
  ],
  "productWhExtAttrReq": {
    "outerGoodsUrl": "https://www.temu.com/us-zh-Hans/4-g-605698448579601.html",
    "productOrigin": {
      "region2Id": 43000000000006,
      "region1ShortName": "CN"
    }
  },
  "productSecondHandReq": {
    "isSecondHand": false
  },
  "personalizationSwitch": 0,
  "productSemiManagedReq": {
    "bindSiteIds": [
      100
    ],
    "semiLanguageStrategy": 1
  },
  "productSpecPropertyReqs": [
    {
      "pid": 15998553,
      "vid": 0,
      "refPid": 15998553,
      "specId": 18191285,
      "propName": "数量",
      "specName": "4件",
      "propValue": "4件",
      "valueUnit": "",
      "templatePid": 15998553,
      "parentSpecId": 15998553,
      "valueGroupId": 0,
      "parentSpecName": "数量",
      "valueGroupName": ""
    }
  ],
  "goodsLayerDecorationReqs": [
    {
      "key": "DecImage",
      "lang": "en",
      "type": "image",
      "priority": 1,
      "contentList": [
        {
          "key": "DecImage",
          "width": 800,
          "height": 800,
          "imgUrl": "https://img.kwcdn.com/product/open/1205ab642c3644899ff5578d998a2be1-goods.jpeg"
        }
      ]
    },
    {
      "key": "DecImage",
      "lang": "en",
      "type": "image",
      "priority": 2,
      "contentList": [
        {
          "key": "DecImage",
          "width": 800,
          "height": 800,
          "imgUrl": "https://img.kwcdn.com/product/open/a7ed40c77f2d4d51ab35e7a30bcc9722-goods.jpeg"
        }
      ]
    },
    {
      "key": "DecImage",
      "lang": "en",
      "type": "image",
      "priority": 3,
      "contentList": [
        {
          "key": "DecImage",
          "width": 800,
          "height": 800,
          "imgUrl": "https://img.kwcdn.com/product/open/6d0c2ee5aaad42ab860e1e9eddf40c97-goods.jpeg"
        }
      ]
    },
    {
      "key": "DecImage",
      "lang": "en",
      "type": "image",
      "priority": 4,
      "contentList": [
        {
          "key": "DecImage",
          "width": 800,
          "height": 800,
          "imgUrl": "https://img.kwcdn.com/product/open/df6b030ae6b54ec1b09aee06cb070aba-goods.jpeg"
        }
      ]
    },
    {
      "key": "DecImage",
      "lang": "en",
      "type": "image",
      "priority": 5,
      "contentList": [
        {
          "key": "DecImage",
          "width": 800,
          "height": 800,
          "imgUrl": "https://img.kwcdn.com/product/open/b308e8044e954732bcbcf8857830086c-goods.jpeg"
        }
      ]
    },
    {
      "key": "DecImage",
      "lang": "en",
      "type": "image",
      "priority": 6,
      "contentList": [
        {
          "key": "DecImage",
          "width": 800,
          "height": 800,
          "imgUrl": "https://img.kwcdn.com/product/open/599c1e396bc0481fbe63559839042fd1-goods.jpeg"
        }
      ]
    },
    {
      "key": "DecImage",
      "lang": "en",
      "type": "image",
      "priority": 7,
      "contentList": [
        {
          "key": "DecImage",
          "width": 800,
          "height": 800,
          "imgUrl": "https://img.kwcdn.com/product/open/da92ed58c88a4c5aab55c65d43853a91-goods.jpeg"
        }
      ]
    },
    {
      "key": "DecImage",
      "lang": "en",
      "type": "image",
      "priority": 8,
      "contentList": [
        {
          "key": "DecImage",
          "width": 800,
          "height": 800,
          "imgUrl": "https://img.kwcdn.com/product/open/aa13ac8dc1f1421eb488b39e73684126-goods.jpeg"
        }
      ]
    },
    {
      "key": "DecImage",
      "lang": "en",
      "type": "image",
      "priority": 9,
      "contentList": [
        {
          "key": "DecImage",
          "width": 800,
          "height": 800,
          "imgUrl": "https://img.kwcdn.com/product/open/63ad54c6656740b28c4fe403ce7fccd3-goods.jpeg"
        }
      ]
    },
    {
      "key": "DecImage",
      "lang": "en",
      "type": "image",
      "priority": 10,
      "contentList": [
        {
          "key": "DecImage",
          "width": 800,
          "height": 800,
          "imgUrl": "https://img.kwcdn.com/product/open/714d047a47b04a4f8ca8038b4466428d-goods.jpeg"
        }
      ]
    },
    {
      "key": "DecImage",
      "lang": "en",
      "type": "image",
      "priority": 11,
      "contentList": [
        {
          "key": "DecImage",
          "width": 800,
          "height": 800,
          "imgUrl": "https://img.kwcdn.com/product/open/1205ab642c3644899ff5578d998a2be1-goods.jpeg"
        }
      ]
    },
    {
      "key": "DecImage",
      "lang": "en",
      "type": "image",
      "priority": 12,
      "contentList": [
        {
          "key": "DecImage",
          "width": 800,
          "height": 800,
          "imgUrl": "https://img.kwcdn.com/product/open/a7ed40c77f2d4d51ab35e7a30bcc9722-goods.jpeg"
        }
      ]
    },
    {
      "key": "DecImage",
      "lang": "en",
      "type": "image",
      "priority": 13,
      "contentList": [
        {
          "key": "DecImage",
          "width": 800,
          "height": 800,
          "imgUrl": "https://img.kwcdn.com/product/open/6d0c2ee5aaad42ab860e1e9eddf40c97-goods.jpeg"
        }
      ]
    },
    {
      "key": "DecImage",
      "lang": "en",
      "type": "image",
      "priority": 14,
      "contentList": [
        {
          "key": "DecImage",
          "width": 800,
          "height": 800,
          "imgUrl": "https://img.kwcdn.com/product/open/df6b030ae6b54ec1b09aee06cb070aba-goods.jpeg"
        }
      ]
    },
    {
      "key": "DecImage",
      "lang": "en",
      "type": "image",
      "priority": 15,
      "contentList": [
        {
          "key": "DecImage",
          "width": 800,
          "height": 800,
          "imgUrl": "https://img.kwcdn.com/product/open/b308e8044e954732bcbcf8857830086c-goods.jpeg"
        }
      ]
    },
    {
      "key": "DecImage",
      "lang": "en",
      "type": "image",
      "priority": 16,
      "contentList": [
        {
          "key": "DecImage",
          "width": 800,
          "height": 800,
          "imgUrl": "https://img.kwcdn.com/product/open/599c1e396bc0481fbe63559839042fd1-goods.jpeg"
        }
      ]
    },
    {
      "key": "DecImage",
      "lang": "en",
      "type": "image",
      "priority": 17,
      "contentList": [
        {
          "key": "DecImage",
          "width": 800,
          "height": 800,
          "imgUrl": "https://img.kwcdn.com/product/open/da92ed58c88a4c5aab55c65d43853a91-goods.jpeg"
        }
      ]
    },
    {
      "key": "DecImage",
      "lang": "en",
      "type": "image",
      "priority": 18,
      "contentList": [
        {
          "key": "DecImage",
          "width": 800,
          "height": 800,
          "imgUrl": "https://img.kwcdn.com/product/open/aa13ac8dc1f1421eb488b39e73684126-goods.jpeg"
        }
      ]
    },
    {
      "key": "DecImage",
      "lang": "en",
      "type": "image",
      "priority": 19,
      "contentList": [
        {
          "key": "DecImage",
          "width": 800,
          "height": 800,
          "imgUrl": "https://img.kwcdn.com/product/open/63ad54c6656740b28c4fe403ce7fccd3-goods.jpeg"
        }
      ]
    },
    {
      "key": "DecImage",
      "lang": "en",
      "type": "image",
      "priority": 20,
      "contentList": [
        {
          "key": "DecImage",
          "width": 800,
          "height": 800,
          "imgUrl": "https://img.kwcdn.com/product/open/714d047a47b04a4f8ca8038b4466428d-goods.jpeg"
        }
      ]
    }
  ],
  "productWarehouseRouteReq": {
    "targetRouteList": [
      {
        "siteIdList": [
          100
        ],
        "warehouseId": "WH-03351775549570503"
      }
    ]
  }
}
```

> 案例中 `access_token` 在日志里已按系统规则脱敏，真实值不会出现在任何对外材料中。
