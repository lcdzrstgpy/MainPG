# 盈銮 Temu 外部对接接口说明

版本日期：2026-08-16

本文档面向外部系统，说明如何通过盈銮集成服务端调用 Temu 订单、履约、文档下载、商品和媒体接口。

服务统一入口：

```text
https://yingluan-api.qibang-global.vip
```

## 1. 接入说明

- 所有业务接口均通过 HTTPS 调用。
- 请求与响应默认使用 JSON。
- 文档下载接口返回二进制文件内容。
- 外部系统只保存自己被分配的 `client/token`，以及自己店铺对应的 Temu `access_token`。
- Temu 的 `app_key/app_secret/sign/timestamp` 由盈銮服务端处理，外部系统不需要也不应直接传这些签名字段。

## 2. 调用链路

```text
外部系统 -> 盈銮集成服务端 -> Temu 官方 OpenAPI
```

## 3. 鉴权与限流

### 3.1 调用凭证

外部请求使用请求头传入调用凭证：

```http
x-yl-client: your-client-id
x-yl-token: your-token
```

也可以把调用凭证放在 JSON 请求体中：

```json
{
  "client": "your-client-id",
  "token": "your-token"
}
```

说明：

- `x-yl-client` 是外部系统的调用方标识。
- `x-yl-token` 是外部系统的调用密钥。
- 这组凭证不是 Temu 店铺的 `access_token`。

### 3.2 调用限制

每个外部系统都可以单独配置：

- `rateLimitPerMinute`
- `rateLimitPerHour`
- `rateLimitPerDay`

超限时返回：

```json
{
  "success": false,
  "errorCode": "RATE_LIMIT_EXCEEDED",
  "message": "Per-minute API call limit exceeded"
}
```

## 4. 接口总览

| 能力 | 方法 | 地址 |
| --- | --- | --- |
| Temu OpenAPI（US / EU） | POST | `https://yingluan-api.qibang-global.vip/api/temu/openapi/router` |
| Temu OpenAPI（GLOBAL） | POST | `https://yingluan-api.qibang-global.vip/api/temu-global/openapi/router` |
| Temu 文档下载（US / EU） | POST | `https://yingluan-api.qibang-global.vip/api/temu/document/download` |
| Temu 文档下载（GLOBAL） | POST | `https://yingluan-api.qibang-global.vip/api/temu-global/document/download` |
| Temu 商品 OpenAPI | POST | `https://yingluan-api.qibang-global.vip/api/temu-product/openapi/router` |
| Temu 商品媒体上传 | POST | `https://yingluan-api.qibang-global.vip/api/temu-product/media/{operation}` |

## 5. Temu OpenAPI

### 5.1 请求格式

```http
POST /api/temu/openapi/router
Content-Type: application/json
x-yl-client: your-client-id
x-yl-token: your-token
```

`region` 规则：

- `US` 和 `EU` 使用 `/api/temu/openapi/router`
- `GLOBAL` 使用 `/api/temu-global/openapi/router`

推荐请求体：

```json
{
  "region": "US",
  "type": "bg.order.list.v2.get",
  "access_token": "temu-access-token",
  "request": {}
}
```

字段说明：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `region` | 是 | Temu 区域，支持 `US`、`EU`、`GLOBAL` |
| `type` | 是 | Temu OpenAPI 方法名 |
| `access_token` | 条件必填 | Temu 店铺授权 token；调用 `bg.open.accesstoken.create` 时不传 |
| `request` | 是 | Temu 业务参数对象 |

### 5.2 响应格式

成功响应示例：

```json
{
  "success": true,
  "result": {}
}
```

失败响应示例：

```json
{
  "success": false,
  "errorCode": "CLIENT_PERMISSION_DENIED",
  "message": "Client is not allowed to call this API"
}
```

### 5.3 常用示例

订单列表：

```bash
curl -X POST "https://yingluan-api.qibang-global.vip/api/temu/openapi/router" \
  -H "Content-Type: application/json" \
  -H "x-yl-client: your-client-id" \
  -H "x-yl-token: your-token" \
  -d '{
    "region": "US",
    "type": "bg.order.list.v2.get",
    "access_token": "temu-access-token",
    "request": {
      "pageNumber": 1,
      "pageSize": 50,
      "sortby": "updateTime"
    }
  }'
```

订单详情：

```bash
curl -X POST "https://yingluan-api.qibang-global.vip/api/temu/openapi/router" \
  -H "Content-Type: application/json" \
  -H "x-yl-client: your-client-id" \
  -H "x-yl-token: your-token" \
  -d '{
    "region": "US",
    "type": "bg.order.detail.v2.get",
    "access_token": "temu-access-token",
    "request": {
      "parentOrderSn": "PO-211-08899129406072220"
    }
  }'
```

授权 code 换 token：

```bash
curl -X POST "https://yingluan-api.qibang-global.vip/api/temu/openapi/router" \
  -H "Content-Type: application/json" \
  -H "x-yl-client: your-client-id" \
  -H "x-yl-token: your-token" \
  -d '{
    "region": "US",
    "type": "bg.open.accesstoken.create",
    "request": {
      "code": "temu-authorization-code"
    }
  }'
```

### 5.4 方法清单

| Temu 方法 | 说明 |
| --- | --- |
| `bg.open.accesstoken.create` | 授权 code 换取 Temu access token |
| `bg.open.accesstoken.info.get` | 查询 Temu access token 信息 |
| `bg.order.list.v2.get` | 查询订单列表 |
| `bg.order.detail.v2.get` | 查询单个父订单详情 |
| `bg.order.shippinginfo.v2.get` | 查询脱敏收货信息 |
| `bg.order.decryptshippinginfo.get` | 查询解密收货信息 |
| `bg.logistics.warehouse.list.get` | 查询在线下单可用发货仓库 |
| `bg.logistics.shippingservices.get` | 查询可用物流服务和报价 |
| `bg.logistics.shipment.create` | 在线下单创建包裹和面单 |
| `bg.logistics.shipment.result.get` | 查询在线下单结果 |
| `bg.logistics.shipment.update` | 重试或确认在线下单包裹 |
| `bg.logistics.shipment.shippingtype.update` | 修改已创建包裹的发货类型 |
| `temu.logistics.label.list.get` | 查询物流面单状态和包裹信息 |
| `bg.logistics.shipment.document.get` | 获取面单文档临时资源地址，下载 PDF 需继续调用第 6 节文档下载接口 |
| `temu.logistics.scanform.create` | 创建 USPS Manifest / 扫描单 |
| `temu.logistics.scanform.document.get` | 获取 USPS Manifest / 扫描单文件 |
| `temu.track.trackinginfo.get` | 查询物流轨迹 |
| `bg.tmc.message.update` | 更新 Temu 消息状态 |

## 6. 面单 / 文档下载

Temu 的 `bg.logistics.shipment.document.get` 返回的是临时资源 URL，不是可以直接用浏览器打开的公网直链。该 URL 通常约 10 分钟失效，下载时还需要 Temu 文档下载签名请求头。

盈銮已经把“获取临时 URL”和“代签下载 PDF”封装成一个接口。外部系统直接调用文档下载接口即可拿到 PDF，不需要自己调用 `bg.logistics.shipment.document.get`。

### 6.1 接口

接口地址：

```http
POST /api/temu/document/download
POST /api/temu-global/document/download
Content-Type: application/json
x-yl-client: your-client-id
x-yl-token: your-token
```

`region` 规则：

- `/api/temu/document/download` 用于 `US` 和 `EU`
- `/api/temu-global/document/download` 仅用于 `GLOBAL`

### 6.2 推荐请求体：直接传包裹号下载 PDF

请求体：

```json
{
  "region": "US",
  "access_token": "temu-access-token",
  "request": {
    "documentType": "SHIPPING_LABEL_PDF",
    "packageSnList": [
      "PK-3233452379340931874"
    ]
  },
  "fileName": "temu-label.pdf"
}
```

`packageSnList` 可以传一个或多个包裹号。传多个包裹号时，盈銮会逐个获取 Temu 面单 PDF，并合并成一个 PDF 文件返回。

字段说明：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `region` | 是 | Temu 区域，支持 `US`、`EU`、`GLOBAL` |
| `access_token` | 是 | Temu 店铺授权 token |
| `request.documentType` | 否 | 文档类型，默认 `SHIPPING_LABEL_PDF` |
| `request.packageSnList` | 是 | Temu 包裹号列表，支持一个或多个包裹号 |
| `fileName` | 否 | 下载文件名，默认 `temu-label.pdf` |

成功响应不是 JSON，而是文件二进制内容。调用方应按文件流或二进制处理：

```http
Content-Type: application/pdf
Content-Disposition: attachment; filename="temu-label.pdf"
Cache-Control: no-store
```

示例：

```bash
curl -X POST "https://yingluan-api.qibang-global.vip/api/temu/document/download" \
  -H "Content-Type: application/json" \
  -H "x-yl-client: your-client-id" \
  -H "x-yl-token: your-token" \
  -o temu-label.pdf \
  -d '{
    "region": "US",
    "access_token": "temu-access-token",
    "request": {
      "documentType": "SHIPPING_LABEL_PDF",
      "packageSnList": [
        "PK-3233452379340931874"
      ]
    },
    "fileName": "temu-label.pdf"
  }'
```

Node.js 示例：

```js
import axios from 'axios'
import fs from 'node:fs/promises'

const response = await axios.post(
  'https://yingluan-api.qibang-global.vip/api/temu/document/download',
  {
    region: 'US',
    access_token: 'temu-access-token',
    request: {
      documentType: 'SHIPPING_LABEL_PDF',
      packageSnList: ['PK-3233452379340931874']
    },
    fileName: 'temu-label.pdf'
  },
  {
    responseType: 'arraybuffer',
    headers: {
      'content-type': 'application/json',
      'x-yl-client': 'your-client-id',
      'x-yl-token': 'your-token'
    }
  }
)

await fs.writeFile('temu-label.pdf', Buffer.from(response.data))
```

多个包裹号示例：

```json
{
  "region": "US",
  "access_token": "temu-access-token",
  "request": {
    "documentType": "SHIPPING_LABEL_PDF",
    "packageSnList": [
      "PK-0770537840967811874",
      "PK-3233441641922691874"
    ]
  },
  "fileName": "temu-label.pdf"
}
```

### 6.3 可选请求体：传 URL 或 result 下载

如果外部系统已经通过 `bg.logistics.shipment.document.get` 拿到了 `shippingLabelUrlList[].url`，也可以继续传完整 URL 下载：

```bash
curl -X POST "https://yingluan-api.qibang-global.vip/api/temu/document/download" \
  -H "Content-Type: application/json" \
  -H "x-yl-client: your-client-id" \
  -H "x-yl-token: your-token" \
  -o temu-label.pdf \
  -d '{
    "region": "US",
    "access_token": "temu-access-token",
    "url": "https://openapi-b-us.temu.com/pkg-label-u/xxx/xxx.pdf?signOM=...",
    "fileName": "temu-label.pdf"
  }'
```

也可以直接传 `bg.logistics.shipment.document.get` 的 `result`，盈銮会自动读取 `shippingLabelUrlList[].url`。如果包含多条 URL，也会合并成一个 PDF 返回：

```json
{
  "region": "US",
  "access_token": "temu-access-token",
  "fileName": "temu-label.pdf",
  "result": {
    "shippingLabelUrlList": [
      {
        "url": "https://openapi-b-us.temu.com/pkg-label-u/xxx/xxx.pdf?signOM=...",
        "packageSn": "PK-3233452379340931874",
        "documentType": "SHIPPING_LABEL_PDF"
      }
    ]
  }
}
```

处理要求：

- 如果传 `url`，必须保留 `shippingLabelUrlList[].url` 的完整值，包含 `signOM` 后面的全部查询参数。
- 不要截断、重新拼接、URL decode 后再存储，也不要只保存 `?` 前面的 PDF 路径。
- 不要把该 Temu URL 直接给浏览器、前端页面或打印客户端访问。
- 下载失败且距离获取 URL 已超过约 10 分钟时，请改用推荐请求体，直接传 `packageSnList` 重新下载。

### 6.4 兼容字段

以下请求体也支持：

```json
{
  "region": "US",
  "access_token": "temu-access-token",
  "documentType": "SHIPPING_LABEL_PDF",
  "packageSnList": [
    "PK-3233452379340931874"
  ],
  "fileName": "temu-label.pdf"
}
```

单个包裹也可以传：

```json
{
  "region": "US",
  "access_token": "temu-access-token",
  "packageSn": "PK-3233452379340931874",
  "fileName": "temu-label.pdf"
}
```

### 6.5 常见失败

| 错误码 | 说明 | 处理方式 |
| --- | --- | --- |
| `CLIENT_PERMISSION_DENIED` | 当前外部系统未开通 `document.download` 权限 | 联系管理员开通对应区域的文档下载权限 |
| `TEMU_REQUEST_INVALID` | 缺少 `access_token` 或包裹号 | 检查 `request.packageSnList`、顶层 `packageSnList` 或 `packageSn` |
| `TEMU_DOCUMENT_URL_GET_FAILED` | 获取 Temu 文档 URL 失败 | 检查包裹号、店铺 token 和 Temu 返回错误 |
| `TEMU_DOCUMENT_URL_MISSING` | Temu 返回结果中没有文档 URL | 检查包裹是否已生成可下载面单 |
| `TEMU_DOCUMENT_URL_INVALID` | URL 不是 HTTPS，或不是 Temu 允许的文档域名 | 使用接口自动下载，或使用 `bg.logistics.shipment.document.get` 原样返回的完整 URL |
| `DOCUMENT_DOWNLOAD_FAILED` | Temu 文档资源下载失败或 URL 已过期 | 直接传 `packageSnList` 重新下载 |

## 7. Temu 商品 OpenAPI

### 7.1 接口

```http
POST /api/temu-product/openapi/router
Content-Type: application/json
x-yl-client: your-client-id
x-yl-token: your-token
```

### 7.2 请求格式

推荐请求体：

```json
{
  "type": "bg.glo.goods.list.get",
  "access_token": "temu-product-access-token",
  "gateway": "PARTNER",
  "request": {
    "page": 1,
    "pageSize": 20
  }
}
```

字段说明：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `type` | 是 | Temu 商品方法名 |
| `access_token` | 条件必填 | Temu 店铺商品权限 token；调用 `bg.open.accesstoken.create` 时不传 |
| `gateway` | 否 | `PARTNER` 或 `CN`，默认 `PARTNER`。已开放商品方法会由盈銮服务端按方法自动选择，外部系统通常不用传。 |
| `request` | 是 | Temu 业务参数对象 |

说明：

- `request` 内放 Temu 业务参数。
- 外部系统不要传 `app_key`、`app_secret`、`sign`、`timestamp`、`data_type`、`version`。
- `bg.open.accesstoken.create` 只需要传 `request.code`。
- 商品 token 校验建议使用 `bg.open.accesstoken.info.get.global`。
- 已开放的 PA 商品方法固定按 PA/PARTNER 网关转发；尺寸图检测方法固定按 CN 网关转发，避免外部系统误传 `gateway` 导致路由错误。
- `bg.goods.image.upload.global` 属于 Temu 图片处理 API 组-PA，需要传 `request.image`。建议直接传完整 Data URL（例如 `data:image/jpeg;base64,/9j/...`）；如只传纯 base64，盈銮服务端会自动补齐图片 Data URL 前缀后转发。该方法固定按 PA/PARTNER 网关转发，外部系统可以不传 `gateway`。
- Temu 文档里的“数据存储 CN”不是盈銮请求体里的 `gateway: "CN"`。只有尺寸图检测这类属于非 PA 图片处理 API 组的方法才固定走 CN 网关。

### 7.3 响应格式

接口直接返回 Temu 商品 OpenAPI 的 JSON 响应。

失败时可能返回：

```json
{
  "success": false,
  "errorCode": "TEMU_PRODUCT_APP_CREDENTIAL_MISSING",
  "errorMsg": "Temu product app credentials are not configured"
}
```

### 7.4 商品方法清单与调用实例

除商品媒体上传方法外，商品 OpenAPI 方法统一调用：

```bash
curl -X POST "https://yingluan-api.qibang-global.vip/api/temu-product/openapi/router" \
  -H "Content-Type: application/json" \
  -H "x-yl-client: your-client-id" \
  -H "x-yl-token: your-yingluan-client-token" \
  -d '<下方任意一个请求体示例>'
```

下面 OpenAPI 示例只展示请求体。外部系统不要传 `app_key`、`app_secret`、`sign`、`timestamp`，这些由盈銮服务端统一生成。
`media.*` 商品媒体上传方法使用 `/api/temu-product/media/{operation}`，对应小节会给出完整 `curl` 示例。

示例中的 ID、类目、属性、规格、仓库、运费模板、站点、尺码表等值必须替换为店铺自己的真实数据。通常先按下面顺序取数：

1. 调 `bg.glo.goods.cats.get` 找叶子类目。
2. 调 `bg.glo.goods.attrs.get`、`bg.glo.goods.parentspec.get`、`bg.glo.goods.catsmandatory.get` 取属性、规格、必填配置。
3. 调 `bg.glo.goods.accessories.get`、`bg.glo.logistics.template.get`、`bg.btg.goods.stock.warehouse.list.get` 取包装清单、运费模板、仓库。
4. 图片先调 `bg.goods.image.upload.global` 上传，拿 Temu 返回的图片 URL 后再用于商品发布。
5. 尺码表先调 `bg.glo.goods.sizecharts.*` 查询或创建，拿到 `businessId` / `templateId` 后再绑定到商品。

#### bg.open.accesstoken.create - 授权 code 换取商品 access token

```json
{
  "type": "bg.open.accesstoken.create",
  "gateway": "PARTNER",
  "request": {
    "code": "temu-authorization-code"
  }
}
```

#### bg.open.accesstoken.info.get - 查询商品 access token 信息

```json
{
  "type": "bg.open.accesstoken.info.get",
  "access_token": "temu-product-access-token",
  "gateway": "PARTNER",
  "request": {}
}
```

#### bg.open.accesstoken.info.get.global - 查询商品 access token 信息

```json
{
  "type": "bg.open.accesstoken.info.get.global",
  "access_token": "temu-product-access-token",
  "gateway": "PARTNER",
  "request": {}
}
```

#### bg.glo.goods.list.get - 商品列表查询

```json
{
  "type": "bg.glo.goods.list.get",
  "access_token": "temu-product-access-token",
  "gateway": "PARTNER",
  "request": {
    "page": 1,
    "pageSize": 20,
    "productName": "Women Cotton T Shirt",
    "skcTopStatus": 100,
    "skcSiteStatus": 100,
    "productSkcIds": [1234567890],
    "skcExtCode": "SKC-RED-001",
    "bindSiteIds": [100],
    "cat1Id": 10000,
    "cat2Id": 20000,
    "cat3Id": 30000,
    "cat4Id": 40000,
    "cat5Id": 50000,
    "cat6Id": 60000,
    "cat7Id": 70000,
    "cat8Id": 80000,
    "cat9Id": 90000,
    "cat10Id": 0,
    "createdAtStart": 1722470400,
    "createdAtEnd": 1725148799,
    "isSupportPersonalization": false,
    "matchJitMode": false,
    "quickSellAgtSignStatus": 1
  }
}
```

常用最小请求只传 `page`、`pageSize` 即可；需要按 SKC 或状态同步时再加 `productSkcIds`、`skcTopStatus`、`skcSiteStatus`。

#### temu.goods.list.get - 商品列表查询

```json
{
  "type": "temu.goods.list.get",
  "access_token": "temu-product-access-token",
  "gateway": "PARTNER",
  "request": {
    "page": 1,
    "pageSize": 20,
    "productName": "Women Cotton T Shirt",
    "skcTopStatus": 100,
    "productSkcIds": [1234567890],
    "bindSiteIds": [100],
    "skcExtCode": "SKC-RED-001"
  }
}
```

#### bg.glo.goods.detail.get - 商品详情查询

```json
{
  "type": "bg.glo.goods.detail.get",
  "access_token": "temu-product-access-token",
  "gateway": "PARTNER",
  "request": {
    "productId": 123456789
  }
}
```

`productId` 从 `bg.glo.goods.list.get` 返回的商品列表中取得。

#### bg.semi.price.review.page.query.order - 半托管核价单分页查询

```json
{
  "type": "bg.semi.price.review.page.query.order",
  "access_token": "temu-product-access-token",
  "gateway": "PARTNER",
  "request": {
    "pageNo": 1,
    "pageSize": 10,
    "orderStatusList": [0, 1, 2, 3, 4, 5],
    "idGt": 0,
    "idLt": 9999999999999
  }
}
```

`orderStatusList` 可按业务过滤：`0` 待核价、`1` 待供应商确认、`2` 核价通过、`3` 核价驳回、`4` 废弃、`5` 价格同步中。`idGt`、`idLt` 可用于按 ID 范围分页补偿。

#### bg.glo.goods.cats.get - 商品类目查询

```json
{
  "type": "bg.glo.goods.cats.get",
  "access_token": "temu-product-access-token",
  "gateway": "PARTNER",
  "request": {
    "parentCatId": 0
  }
}
```

查询一级类目时可以不传 `parentCatId` 或传 `0`；查询子类目时传上一级类目 ID。

#### bg.glo.goods.category.match - 类目匹配

```json
{
  "type": "bg.glo.goods.category.match",
  "access_token": "temu-product-access-token",
  "gateway": "PARTNER",
  "request": {
    "searchText": "women cotton t shirt",
    "siteId": 100
  }
}
```

`searchText` 是类目搜索关键词，最长 200 字符；`siteId` 是站点 ID。

#### bg.goods.attrs.get - 类目属性查询

```json
{
  "type": "bg.goods.attrs.get",
  "access_token": "temu-product-access-token",
  "gateway": "PARTNER",
  "request": {
    "catId": 123456,
    "productCreateTime": 1725148799000,
    "supportedType": 1,
    "langList": ["en", "de", "fr"]
  }
}
```

`catId` 是叶子类目 ID；`productCreateTime` 是毫秒时间戳；不需要多语言属性时可不传 `langList`。

#### bg.glo.goods.attrs.get - 类目属性查询

```json
{
  "type": "bg.glo.goods.attrs.get",
  "access_token": "temu-product-access-token",
  "gateway": "PARTNER",
  "request": {
    "catId": 123456,
    "productCreateTime": 1725148799000,
    "supportedType": 1,
    "langList": ["en", "de", "fr"]
  }
}
```

#### bg.glo.goods.catsmandatory.get - 类目必填配置查询

```json
{
  "type": "bg.glo.goods.catsmandatory.get",
  "access_token": "temu-product-access-token",
  "gateway": "PARTNER",
  "request": {
    "leafCatId": 123456,
    "bindSiteIds": [100],
    "configItems": [1, 2, 3],
    "productPropertyReqs": [
      {
        "pid": 100001,
        "refPid": 100001,
        "templatePid": 200001,
        "vid": 300001,
        "propName": "Material",
        "propValue": "Cotton",
        "valueUnit": "",
        "numberInputValue": "",
        "valueGroupId": 0,
        "valueGroupName": ""
      }
    ]
  }
}
```

`leafCatId` 是叶子类目；`productPropertyReqs` 使用 `bg.glo.goods.attrs.get` 返回的属性 ID、属性值 ID 和模板属性 ID 组装。

#### bg.glo.goods.parentspec.get - 父规格查询

```json
{
  "type": "bg.glo.goods.parentspec.get",
  "access_token": "temu-product-access-token",
  "gateway": "PARTNER",
  "request": {}
}
```

该接口用于查询可用父规格列表，Temu 当前 PA 文档未要求业务入参。

#### bg.glo.goods.accessories.get - 包装清单 / 配件查询

```json
{
  "type": "bg.glo.goods.accessories.get",
  "access_token": "temu-product-access-token",
  "gateway": "PARTNER",
  "request": {
    "page": 1,
    "pageSize": 20,
    "fuzzyValue": "box",
    "vidList": [10001, 10002]
  }
}
```

`fuzzyValue` 用于按包装/配件名称模糊搜索；`vidList` 用于按属性值 ID 精确筛选。

#### bg.goods.accessories.get - 包装清单 / 配件查询

```json
{
  "type": "bg.goods.accessories.get",
  "access_token": "temu-product-access-token",
  "gateway": "PARTNER",
  "request": {
    "page": 1,
    "pageSize": 20,
    "fuzzyValue": "box",
    "vidList": [10001, 10002]
  }
}
```

#### bg.goods.sizecharts.get - 尺码模板查询

```json
{
  "type": "bg.goods.sizecharts.get",
  "access_token": "temu-product-access-token",
  "gateway": "PARTNER",
  "request": {
    "catId": 123456,
    "offset": 0,
    "pageSize": 20
  }
}
```

`offset` 为偏移量，第一页传 `0`；下一页通常传上一页 `offset + pageSize`。

#### bg.glo.goods.sizecharts.get - 尺码模板查询

```json
{
  "type": "bg.glo.goods.sizecharts.get",
  "access_token": "temu-product-access-token",
  "gateway": "PARTNER",
  "request": {
    "catId": 123456,
    "offset": 0,
    "pageSize": 20
  }
}
```

#### temu.goods.sizecharts.get - 尺码模板查询

```json
{
  "type": "temu.goods.sizecharts.get",
  "access_token": "temu-product-access-token",
  "gateway": "PARTNER",
  "request": {
    "catId": 123456,
    "offset": 0,
    "pageSize": 20
  }
}
```

#### bg.glo.goods.sizecharts.class.get - 尺码分类查询

```json
{
  "type": "bg.glo.goods.sizecharts.class.get",
  "access_token": "temu-product-access-token",
  "gateway": "PARTNER",
  "request": {
    "catId": 123456,
    "classId": 987654
  }
}
```

可只传 `catId` 查询类目关联的尺码分类；也可传 `classId` 查询指定分类关系。

#### temu.goods.sizecharts.class.get - 尺码分类查询

```json
{
  "type": "temu.goods.sizecharts.class.get",
  "access_token": "temu-product-access-token",
  "gateway": "PARTNER",
  "request": {
    "catId": 123456,
    "classId": 987654
  }
}
```

#### bg.goods.sizecharts.class.get - 尺码分类查询

```json
{
  "type": "bg.goods.sizecharts.class.get",
  "access_token": "temu-product-access-token",
  "gateway": "PARTNER",
  "request": {
    "catId": 123456,
    "classId": 987654
  }
}
```

#### bg.glo.goods.sizecharts.meta.get - 尺码模板元数据查询

```json
{
  "type": "bg.glo.goods.sizecharts.meta.get",
  "access_token": "temu-product-access-token",
  "gateway": "PARTNER",
  "request": {
    "catId": 123456,
    "classId": 987654
  }
}
```

`classId` 来自 `bg.glo.goods.sizecharts.class.get` 返回值；`catId` 可辅助按类目查询。

#### temu.goods.sizecharts.meta.get - 尺码模板元数据查询

```json
{
  "type": "temu.goods.sizecharts.meta.get",
  "access_token": "temu-product-access-token",
  "gateway": "PARTNER",
  "request": {
    "catId": 123456,
    "classId": 987654
  }
}
```

#### bg.goods.sizecharts.meta.get - 尺码模板元数据查询

```json
{
  "type": "bg.goods.sizecharts.meta.get",
  "access_token": "temu-product-access-token",
  "gateway": "PARTNER",
  "request": {
    "catId": 123456,
    "classId": 987654
  }
}
```

#### bg.glo.goods.sizecharts.settings.get - 尺码设置查询

```json
{
  "type": "bg.glo.goods.sizecharts.settings.get",
  "access_token": "temu-product-access-token",
  "gateway": "PARTNER",
  "request": {
    "catId": 123456,
    "classId": 987654,
    "shoeSizeType": 0
  }
}
```

`shoeSizeType` 可选：`0` 普通鞋码，`1` 宽版鞋码；非鞋类通常不传。

#### temu.goods.sizecharts.settings.get - 尺码设置查询

```json
{
  "type": "temu.goods.sizecharts.settings.get",
  "access_token": "temu-product-access-token",
  "gateway": "PARTNER",
  "request": {
    "catId": 123456,
    "classId": 987654,
    "shoeSizeType": 0
  }
}
```

#### bg.goods.sizecharts.settings.get - 尺码设置查询

```json
{
  "type": "bg.goods.sizecharts.settings.get",
  "access_token": "temu-product-access-token",
  "gateway": "PARTNER",
  "request": {
    "catId": 123456,
    "classId": 987654,
    "shoeSizeType": 0
  }
}
```

#### bg.glo.goods.sizecharts.create - 尺码表创建

```json
{
  "type": "bg.glo.goods.sizecharts.create",
  "access_token": "temu-product-access-token",
  "gateway": "PARTNER",
  "request": {
    "name": "US Women Size Chart",
    "catId": 123456,
    "classId": 987654,
    "reusable": true,
    "generalSizeType": 101,
    "localSizeSource": 1,
    "ext": {
      "shoeSizeType": 0,
      "isDoubleSize": false,
      "manualGroupIdList": [101]
    },
    "content": {
      "meta": {
        "groupList": [
          {
            "id": 1,
            "name": "Size"
          }
        ],
        "elementList": [
          {
            "id": 1001,
            "name": "Bust"
          },
          {
            "id": 1002,
            "name": "Length"
          }
        ]
      },
      "records": [
        {
          "values": {
            "1": "S",
            "1001": "88",
            "1002": "60"
          }
        },
        {
          "values": {
            "1": "M",
            "1001": "92",
            "1002": "62"
          }
        }
      ],
      "bodyMeta": {
        "groupList": [
          {
            "id": 1,
            "name": "Size"
          }
        ],
        "elementList": [
          {
            "id": 2001,
            "name": "Height"
          },
          {
            "id": 2002,
            "name": "Weight"
          }
        ]
      },
      "bodyRecords": [
        {
          "values": {
            "1": "S",
            "2001": "155-165",
            "2002": "45-55"
          }
        }
      ]
    }
  }
}
```

`content.meta`、`content.records` 的字段 ID 来自 `bg.glo.goods.sizecharts.meta.get` 返回的元数据；创建成功后返回 `businessId`，可用于商品尺码绑定。

#### bg.goods.sizecharts.create - 尺码表创建

```json
{
  "type": "bg.goods.sizecharts.create",
  "access_token": "temu-product-access-token",
  "gateway": "PARTNER",
  "request": {
    "name": "US Women Size Chart",
    "catId": 123456,
    "classId": 987654,
    "reusable": true,
    "content": {
      "meta": {
        "groupList": [
          {
            "id": 1,
            "name": "Size"
          }
        ],
        "elementList": [
          {
            "id": 1001,
            "name": "Bust"
          }
        ]
      },
      "records": [
        {
          "values": {
            "1": "S",
            "1001": "88"
          }
        }
      ]
    }
  }
}
```

#### temu.goods.sizecharts.create - 尺码表创建

```json
{
  "type": "temu.goods.sizecharts.create",
  "access_token": "temu-product-access-token",
  "gateway": "PARTNER",
  "request": {
    "name": "US Women Size Chart",
    "catId": 123456,
    "classId": 987654,
    "reusable": true,
    "content": {
      "meta": {
        "groupList": [
          {
            "id": 1,
            "name": "Size"
          }
        ],
        "elementList": [
          {
            "id": 1001,
            "name": "Bust"
          }
        ]
      },
      "records": [
        {
          "values": {
            "1": "S",
            "1001": "88"
          }
        }
      ]
    }
  }
}
```

#### bg.glo.goods.sizecharts.template.create - 尺码模板创建

```json
{
  "type": "bg.glo.goods.sizecharts.template.create",
  "access_token": "temu-product-access-token",
  "gateway": "PARTNER",
  "request": {
    "businessId": 987654321
  }
}
```

`businessId` 是已有尺码表模板 ID，接口会基于模板创建可用于货品的尺码表。

#### bg.glo.goods.size.template.edit - 商品尺码模板绑定 / 编辑

```json
{
  "type": "bg.glo.goods.size.template.edit",
  "access_token": "temu-product-access-token",
  "gateway": "PARTNER",
  "request": {
    "productId": 123456789,
    "sizeTemplateIds": [987654321],
    "showSizeTemplateIds": [987654321]
  }
}
```

`sizeTemplateIds` 是商品绑定的尺码表 ID 列表；`showSizeTemplateIds` 是前台重点展示的尺码表 ID 列表。

#### temu.goods.size.template.edit - 商品尺码模板绑定 / 编辑

```json
{
  "type": "temu.goods.size.template.edit",
  "access_token": "temu-product-access-token",
  "gateway": "PARTNER",
  "request": {
    "productId": 123456789,
    "sizeTemplateIds": [987654321],
    "showSizeTemplateIds": [987654321]
  }
}
```

#### temu.logistics.template.get - 运费模板查询

```json
{
  "type": "temu.logistics.template.get",
  "access_token": "temu-product-access-token",
  "gateway": "PARTNER",
  "request": {
    "siteIds": [100]
  }
}
```

`siteIds` 是站点 ID 列表；不传时按店铺权限返回可用运费模板。

#### bg.logistics.template.get - 运费模板查询

```json
{
  "type": "bg.logistics.template.get",
  "access_token": "temu-product-access-token",
  "gateway": "PARTNER",
  "request": {
    "siteIds": [100]
  }
}
```

#### bg.glo.logistics.template.get - 运费模板查询

```json
{
  "type": "bg.glo.logistics.template.get",
  "access_token": "temu-product-access-token",
  "gateway": "PARTNER",
  "request": {
    "siteIds": [100]
  }
}
```

#### bg.btg.goods.stock.warehouse.list.get - 发货仓库查询

```json
{
  "type": "bg.btg.goods.stock.warehouse.list.get",
  "access_token": "temu-product-access-token",
  "gateway": "PARTNER",
  "request": {
    "openApiUser": {
      "supplierId": 123456789
    },
    "supplierId": 123456789,
    "siteIdList": [100]
  }
}
```

`supplierId` 是供应商 ID；`siteIdList` 是站点 ID 列表。返回的 `warehouseId` 可用于商品发布库存和仓库路由。

#### bg.goods.warehouse.list.get - 发货仓库查询

```json
{
  "type": "bg.goods.warehouse.list.get",
  "access_token": "temu-product-access-token",
  "gateway": "PARTNER",
  "request": {
    "openApiUser": {
      "supplierId": 123456789
    },
    "supplierId": 123456789,
    "siteIdList": [100]
  }
}
```

#### bg.glo.goods.spec.create - 商品规格创建

```json
{
  "type": "bg.glo.goods.spec.create",
  "access_token": "temu-product-access-token",
  "gateway": "PARTNER",
  "request": {
    "parentSpecId": 111111,
    "specName": "Black"
  }
}
```

`parentSpecId` 从 `bg.glo.goods.parentspec.get` 返回值中取得；创建成功后返回的 `specId` 可用于发布商品 SKU 规格。

#### bg.goods.spec.create - 商品规格创建

```json
{
  "type": "bg.goods.spec.create",
  "access_token": "temu-product-access-token",
  "gateway": "PARTNER",
  "request": {
    "parentSpecId": 111111,
    "specName": "Black"
  }
}
```

#### bg.glo.goods.add - 商品发布

```json
{
  "type": "bg.glo.goods.add",
  "access_token": "temu-product-access-token",
  "gateway": "PARTNER",
  "request": {
    "productSemiManagedReq": {
      "bindSiteIds": [100],
      "semiManagedSiteMode": 1,
      "semiLanguageStrategy": 1
    },
    "productName": "Women Cotton T Shirt",
    "productI18nReqs": [
      {
        "language": "en",
        "productName": "Women Cotton T Shirt"
      }
    ],
    "carouselImageUrls": [
      "https://img.kwcdn.com/product-main-1.jpg",
      "https://img.kwcdn.com/product-main-2.jpg"
    ],
    "productOuterPackageImageReqs": [
      {
        "imageUrl": "https://img.kwcdn.com/package.jpg"
      }
    ],
    "productShipmentReq": {
      "freightTemplateId": "FT-123456789",
      "shipmentLimitSecond": 86400
    },
    "cat1Id": 10000,
    "cat2Id": 20000,
    "cat3Id": 30000,
    "cat4Id": 40000,
    "cat5Id": 50000,
    "cat6Id": 60000,
    "cat7Id": 70000,
    "cat8Id": 80000,
    "cat9Id": 0,
    "cat10Id": 0,
    "productSpecPropertyReqs": [
      {
        "parentSpecId": 1001,
        "specId": 2001,
        "pid": 3001,
        "refPid": 3001,
        "templatePid": 4001,
        "vid": 5001,
        "propName": "Color",
        "propValue": "Red",
        "valueUnit": "",
        "valueGroupId": 0,
        "valueGroupName": ""
      }
    ],
    "productPropertyReqs": [
      {
        "pid": 6001,
        "refPid": 6001,
        "templatePid": 7001,
        "vid": 8001,
        "propName": "Material",
        "propValue": "Cotton",
        "valueUnit": "",
        "numberInputValue": "",
        "valueGroupId": 0,
        "valueGroupName": ""
      }
    ],
    "goodsLayerDecorationReqs": [
      {
        "lang": "en",
        "type": "image",
        "priority": 1,
        "key": "DecImage",
        "contentList": [
          {
            "imgUrl": "https://img.kwcdn.com/detail-1.jpg",
            "width": 800,
            "height": 800
          }
        ]
      }
    ],
    "sizeTemplateIds": [987654321],
    "showSizeTemplateIds": [987654321],
    "productWarehouseRouteReq": {
      "targetRouteList": [
        {
          "warehouseId": "WH-123456789",
          "siteIdList": [100]
        }
      ]
    },
    "productWhExtAttrReq": {
      "outerGoodsUrl": "",
      "productOrigin": {
        "region1ShortName": "CN",
        "region2Id": 0
      },
      "productOriginCertFiles": [
        {
          "fileName": "origin-cert.pdf",
          "fileUrl": "https://example.com/origin-cert.pdf"
        }
      ]
    },
    "productSkcReqs": [
      {
        "extCode": "SKC-RED-001",
        "previewImgUrls": [
          "https://img.kwcdn.com/skc-red-main.jpg"
        ],
        "colorImageUrl": "https://img.kwcdn.com/color-red.jpg",
        "mainProductSkuSpecReqs": [
          {
            "parentSpecId": 1001,
            "parentSpecName": "Color",
            "specId": 2001,
            "specName": "Red"
          }
        ],
        "productSkuReqs": [
          {
            "extCode": "SKU-RED-S",
            "currencyType": "USD",
            "thumbUrl": "https://img.kwcdn.com/sku-red-s.jpg",
            "siteSupplierPrices": [
              {
                "siteId": 100,
                "supplierPrice": 1299
              }
            ],
            "productSkuSuggestedPriceReq": {
              "suggestedPriceCurrencyType": "USD",
              "suggestedPrice": 1999
            },
            "productSkuStockQuantityReq": {
              "warehouseStockQuantityReqs": [
                {
                  "warehouseId": "WH-123456789",
                  "targetStockAvailable": 100
                }
              ]
            },
            "productSkuWhExtAttrReq": {
              "productSkuWeightReq": {
                "value": 200000,
                "inputValue": "0.2",
                "inputUnit": "kg"
              },
              "productSkuVolumeReq": {
                "len": 200,
                "width": 150,
                "height": 30,
                "inputLen": "20",
                "inputWidth": "15",
                "inputHeight": "3",
                "inputUnit": "cm"
              },
              "productSkuSensitiveAttrReq": {
                "isSensitive": 0,
                "sensitiveTypes": [],
                "sensitiveList": []
              },
              "productSkuSensitiveLimitReq": {}
            },
            "productSkuSpecReqs": [
              {
                "parentSpecId": 1001,
                "parentSpecName": "Color",
                "specId": 2001,
                "specName": "Red"
              },
              {
                "parentSpecId": 1002,
                "parentSpecName": "Size",
                "specId": 2002,
                "specName": "S"
              }
            ]
          }
        ]
      }
    ],
    "productCustomReq": {
      "goodsLabelName": "T Shirt",
      "isRecommendedTag": true
    }
  }
}
```

商品发布的图片 URL 应使用 `bg.goods.image.upload.global` 返回的 Temu 图片地址；价格类字段以最小货币单位传入，例如 USD 12.99 传 `1299`。`productSkcReqs[].extCode` 是 SKC 外部编码，`productSkuReqs[].extCode` 是 SKU 外部编码。

#### temu.goods.add - 商品发布

```json
{
  "type": "temu.goods.add",
  "access_token": "temu-product-access-token",
  "gateway": "PARTNER",
  "request": {
    "productName": "Women Cotton T Shirt",
    "carouselImageUrls": [
      "https://img.kwcdn.com/product-main-1.jpg"
    ],
    "productShipmentReq": {
      "freightTemplateId": "FT-123456789",
      "shipmentLimitSecond": 86400
    },
    "cat1Id": 10000,
    "cat2Id": 20000,
    "cat3Id": 30000,
    "cat4Id": 40000,
    "cat5Id": 50000,
    "cat6Id": 60000,
    "cat7Id": 70000,
    "cat8Id": 80000,
    "cat9Id": 0,
    "cat10Id": 0,
    "productPropertyReqs": [
      {
        "pid": 6001,
        "refPid": 6001,
        "templatePid": 7001,
        "vid": 8001,
        "propName": "Material",
        "propValue": "Cotton",
        "valueUnit": ""
      }
    ],
    "productSkcReqs": [
      {
        "extCode": "SKC-RED-001",
        "previewImgUrls": ["https://img.kwcdn.com/skc-red-main.jpg"],
        "productSkuReqs": [
          {
            "extCode": "SKU-RED-S",
            "currencyType": "USD",
            "siteSupplierPrices": [
              {
                "siteId": 100,
                "supplierPrice": 1299
              }
            ],
            "productSkuWhExtAttrReq": {
              "productSkuWeightReq": {
                "value": 200000
              },
              "productSkuVolumeReq": {
                "len": 200,
                "width": 150,
                "height": 30
              },
              "productSkuSensitiveAttrReq": {
                "isSensitive": 0
              },
              "productSkuSensitiveLimitReq": {}
            },
            "productSkuSpecReqs": [
              {
                "parentSpecId": 1001,
                "parentSpecName": "Color",
                "specId": 2001,
                "specName": "Red"
              }
            ]
          }
        ]
      }
    ]
  }
}
```

#### bg.goods.add - 商品发布

```json
{
  "type": "bg.goods.add",
  "access_token": "temu-product-access-token",
  "gateway": "PARTNER",
  "request": {
    "productName": "Women Cotton T Shirt",
    "carouselImageUrls": [
      "https://img.kwcdn.com/product-main-1.jpg"
    ],
    "productShipmentReq": {
      "freightTemplateId": "FT-123456789",
      "shipmentLimitSecond": 86400
    },
    "cat1Id": 10000,
    "cat2Id": 20000,
    "cat3Id": 30000,
    "cat4Id": 40000,
    "cat5Id": 50000,
    "cat6Id": 60000,
    "cat7Id": 70000,
    "cat8Id": 80000,
    "cat9Id": 0,
    "cat10Id": 0,
    "productPropertyReqs": [
      {
        "pid": 6001,
        "refPid": 6001,
        "templatePid": 7001,
        "vid": 8001,
        "propName": "Material",
        "propValue": "Cotton",
        "valueUnit": ""
      }
    ],
    "productSkcReqs": [
      {
        "extCode": "SKC-RED-001",
        "previewImgUrls": ["https://img.kwcdn.com/skc-red-main.jpg"],
        "productSkuReqs": [
          {
            "extCode": "SKU-RED-S",
            "currencyType": "USD",
            "siteSupplierPrices": [
              {
                "siteId": 100,
                "supplierPrice": 1299
              }
            ],
            "productSkuWhExtAttrReq": {
              "productSkuWeightReq": {
                "value": 200000
              },
              "productSkuVolumeReq": {
                "len": 200,
                "width": 150,
                "height": 30
              },
              "productSkuSensitiveAttrReq": {
                "isSensitive": 0
              },
              "productSkuSensitiveLimitReq": {}
            },
            "productSkuSpecReqs": [
              {
                "parentSpecId": 1001,
                "parentSpecName": "Color",
                "specId": 2001,
                "specName": "Red"
              }
            ]
          }
        ]
      }
    ]
  }
}
```

#### bg.goods.image.upload.global - 商品图片上传

```json
{
  "type": "bg.goods.image.upload.global",
  "access_token": "temu-product-access-token",
  "gateway": "PARTNER",
  "request": {
    "image": "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAA...",
    "imageBizType": 0,
    "options": {
      "cateId": 123456,
      "doIntelligenceCrop": false,
      "boost": false,
      "sizeMode": 0
    }
  }
}
```

注意：Temu 官方要求 `request.image` 是图片 base64 内容，支持 `jpg/jpeg`、`png` 等格式。外部系统可以直接传完整 Data URL（例如 `data:image/jpeg;base64,...`、`data:image/png;base64,...`），也可以传纯 base64；盈銮服务端会自动兼容并转发。`imageBizType=1` 返回的 URL 可用于商品发布外包装图片；`options.sizeMode` 可选 `0` 原图、`1` 800x800、`2` 1350x1800。

#### bg.goods.video.upload.sign.get.global - 视频上传签名获取

```json
{
  "type": "bg.goods.video.upload.sign.get.global",
  "access_token": "temu-product-access-token",
  "gateway": "PARTNER",
  "request": {
    "fileName": "product-video.mp4",
    "fileSize": 1048576,
    "fileMd5": "b1946ac92492d2347c6235b4d2611184",
    "contentType": "video/mp4"
  }
}
```

调用后使用返回的上传签名走 `media.small-video` 或大视频分片上传流程。

#### bg.goods.big.video.upload.result.get.global - 视频上传结果查询

```json
{
  "type": "bg.goods.big.video.upload.result.get.global",
  "access_token": "temu-product-access-token",
  "gateway": "PARTNER",
  "request": {
    "uploadId": "temu-video-upload-id",
    "vid": "temu-video-vid"
  }
}
```

`uploadId` 或 `vid` 使用视频上传签名/上传结果返回值，按 Temu 实际返回字段传入。

#### bg.glo.goods.instructions.upload - 商品说明书上传

```json
{
  "type": "bg.glo.goods.instructions.upload",
  "access_token": "temu-product-access-token",
  "gateway": "PARTNER",
  "request": {
    "fileName": "manual.pdf",
    "fileBase64": "JVBERi0xLjQK...",
    "contentType": "application/pdf",
    "languages": ["en"],
    "fileMd5": "b1946ac92492d2347c6235b4d2611184"
  }
}
```

成功后返回的说明书文件 ID / material ID 可放入商品发布的 `productGuideFileReqs[].pdfMaterialId`。

#### bg.algo.dimension.image.check - 尺寸图检测提交

```json
{
  "type": "bg.algo.dimension.image.check",
  "access_token": "temu-product-access-token",
  "gateway": "CN",
  "request": {
    "imageUrl": "https://example.com/size-chart.jpg",
    "mallId": "123456",
    "catId": 123456,
    "classId": 987654
  }
}
```

尺寸图检测属于 CN 网关方法，`gateway` 可以不传，盈銮会按方法固定走 CN；示例保留 `gateway: "CN"` 是为了让调用方看清楚分区。

#### bg.algo.dimension.image.check.result - 尺寸图检测结果查询

```json
{
  "type": "bg.algo.dimension.image.check.result",
  "access_token": "temu-product-access-token",
  "gateway": "CN",
  "request": {
    "taskId": "dimension-check-task-id",
    "mallId": "123456"
  }
}
```

#### media.small-video - 小视频上传

```bash
curl -X POST "https://yingluan-api.qibang-global.vip/api/temu-product/media/small-video" \
  -H "Content-Type: application/json" \
  -H "x-yl-client: your-client-id" \
  -H "x-yl-token: your-yingluan-client-token" \
  -d '{
    "fields": [
      {
        "name": "file",
        "base64": "base64-video-content",
        "fileName": "product-video.mp4",
        "contentType": "video/mp4"
      },
      {
        "name": "create_media",
        "value": "true"
      },
      {
        "name": "sign",
        "value": "temu-upload-sign"
      }
    ]
  }'
```

#### media.large-video-init - 大视频分片初始化

```bash
curl -X POST "https://yingluan-api.qibang-global.vip/api/temu-product/media/large-video-init" \
  -H "Content-Type: application/json" \
  -H "x-yl-client: your-client-id" \
  -H "x-yl-token: your-yingluan-client-token" \
  -d '{
    "fields": [
      {
        "name": "create_media",
        "value": "true"
      },
      {
        "name": "content_type",
        "value": "video/mp4"
      },
      {
        "name": "sign",
        "value": "temu-upload-sign"
      }
    ]
  }'
```

#### media.large-video-part - 大视频分片上传

```bash
curl -X POST "https://yingluan-api.qibang-global.vip/api/temu-product/media/large-video-part" \
  -H "Content-Type: application/json" \
  -H "x-yl-client: your-client-id" \
  -H "x-yl-token: your-yingluan-client-token" \
  -d '{
    "fields": [
      {
        "name": "part_file",
        "base64": "base64-video-part-content",
        "fileName": "product-video.mp4.part1",
        "contentType": "video/mp4"
      },
      {
        "name": "sign",
        "value": "temu-large-video-sign"
      },
      {
        "name": "part_num",
        "value": "1"
      }
    ]
  }'
```

#### media.large-video-complete - 大视频分片完成

```bash
curl -X POST "https://yingluan-api.qibang-global.vip/api/temu-product/media/large-video-complete" \
  -H "Content-Type: application/json" \
  -H "x-yl-client: your-client-id" \
  -H "x-yl-token: your-yingluan-client-token" \
  -d '{
    "fields": [
      {
        "name": "sign",
        "value": "temu-large-video-sign"
      }
    ]
  }'
```

## 8. 商品媒体上传

### 8.1 接口

```http
POST /api/temu-product/media/{operation}
Content-Type: application/json
x-yl-client: your-client-id
x-yl-token: your-token
```

### 8.2 operation

| operation | 说明 |
| --- | --- |
| `small-video` | 小视频上传 |
| `large-video-init` | 大视频分片初始化 |
| `large-video-part` | 大视频分片上传 |
| `large-video-complete` | 大视频分片完成 |

### 8.3 请求体

```json
{
  "access_token": "temu-product-access-token",
  "fields": [
    {
      "name": "file",
      "base64": "base64-file-content",
      "fileName": "product-video.mp4",
      "contentType": "video/mp4"
    },
    {
      "name": "create_media",
      "value": "true"
    }
  ]
}
```

字段说明：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `access_token` | 是 | Temu 店铺商品权限 token |
| `fields` | 是 | 媒体上传字段数组 |
| `fields[].name` | 是 | 字段名 |
| `fields[].value` | 条件必填 | 文本字段值 |
| `fields[].base64` | 条件必填 | 文件字段的 base64 内容 |
| `fields[].fileName` | 文件字段必填 | 文件名 |
| `fields[].contentType` | 文件字段必填 | MIME 类型 |

### 8.4 示例

```bash
curl -X POST "https://yingluan-api.qibang-global.vip/api/temu-product/media/small-video" \
  -H "Content-Type: application/json" \
  -H "x-yl-client: your-client-id" \
  -H "x-yl-token: your-token" \
  -d '{
    "access_token": "temu-product-access-token",
    "fields": [
      {
        "name": "file",
        "base64": "base64-file-content",
        "fileName": "product-video.mp4",
        "contentType": "video/mp4"
      }
    ]
  }'
```

## 9. 常见错误

| HTTP 状态 | 错误码 | 说明 |
| --- | --- | --- |
| 401 | `CLIENT_TOKEN_REQUIRED` | 缺少调用凭证 |
| 401 | `CLIENT_TOKEN_INVALID` | 凭证错误、停用或过期 |
| 403 | `CLIENT_PERMISSION_DENIED` | 当前凭证不可调用该区域或该方法 |
| 404 | `UPSTREAM_ROUTE_NOT_FOUND` | 当前方法未配置上游路由 |
| 429 | `RATE_LIMIT_EXCEEDED` | 超过调用频率限制 |
| 502 | `UPSTREAM_REQUEST_FAILED` | 上游请求失败 |
| 502 | `DOCUMENT_DOWNLOAD_FAILED` | 文档下载失败 |
| 503 | `UPSTREAM_CREDENTIAL_MISSING` | 上游 Temu 凭证未配置 |
| 503 | `TEMU_PRODUCT_APP_CREDENTIAL_MISSING` | Temu 商品 App 凭证未配置 |
| 400 | `TEMU_REQUEST_INVALID` | 商品请求参数缺失或非法 |
| 400 | `TEMU_REGION_PATH_MISMATCH` | 路径和 region 不匹配 |

## 10. 对接建议

1. 所有调用都建议放在服务端完成，不要把 `client/token` 或 `access_token` 放到前端。
2. `bg.open.accesstoken.create` 只传 `request.code`。
3. 订单与履约请求统一带 `region`。
4. 商品请求统一使用 `request` 包裹业务参数。
5. 尺寸图检测相关方法请固定使用 `gateway: "CN"`。
6. 请求失败时建议记录 `client`、`region`、`type`、HTTP 状态、错误码和请求时间，便于排查。
