# 豆包识图接入教程（Chat Completions）

本教程用于把图片理解能力接入其他项目。已在 `doubao-seed-2-0-mini-260428` 上验证过本地 JPEG 图片识别。

## 1. 安全准备

在火山方舟控制台创建 API Key，并只通过环境变量提供给应用。不要将 Key 写入源码、`.env` 示例、日志、截图或聊天消息；已泄露的 Key 应立即轮换。

```sh
export ARK_API_KEY='你的新 API Key'
```

接口地址：

```text
POST https://ark.cn-beijing.volces.com/api/v3/chat/completions
```

本文示例模型：

```text
doubao-seed-2-0-mini-260428
```

模型可用性、版本和价格以你的方舟控制台配置为准。

## 2. 请求结构

图片和问题放在同一条用户消息的 `content` 数组中，图片块使用 `image_url`，文字块使用 `text`：

```json
{
  "model": "doubao-seed-2-0-mini-260428",
  "messages": [
    {
      "role": "user",
      "content": [
        {
          "type": "image_url",
          "image_url": {
            "url": "https://example.com/product.jpg"
          }
        },
        {
          "type": "text",
          "text": "图片里是什么商品？请描述材质、数量和用途。"
        }
      ]
    }
  ]
}
```

公网 URL 必须能被方舟服务访问。若图片只在本地，推荐使用 Data URL，避免为了识图而把图片上传到公网：

```text
data:image/jpeg;base64,/9j/4AAQSk...
```

## 3. 最小 curl 示例（公网图片）

```sh
curl https://ark.cn-beijing.volces.com/api/v3/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -d '{
    "model": "doubao-seed-2-0-mini-260428",
    "messages": [{
      "role": "user",
      "content": [
        {"type": "image_url", "image_url": {"url": "https://example.com/product.jpg"}},
        {"type": "text", "text": "请描述图片中的商品。"}
      ]
    }]
  }'
```

## 4. Python 可复用封装（本地或公网图片）

下面函数使用 Python 标准库。传入本地路径时会自动编码为 Data URL；传入 `http://` 或 `https://` 时直接使用该 URL。为避免认证头在重定向时泄露，代码拒绝跟随重定向。

```python
import base64
import json
import mimetypes
import os
from pathlib import Path
import urllib.error
import urllib.request

API_URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
MODEL_ID = "doubao-seed-2-0-mini-260428"


class RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, new_url):
        raise urllib.error.HTTPError(request.full_url, code, "Redirects are not allowed", headers, file)


def image_reference(image: str) -> str:
    if image.startswith(("https://", "http://", "data:")):
        return image
    path = Path(image)
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    payload = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{payload}"


def recognize_image(image: str, question: str) -> str:
    api_key = os.environ["ARK_API_KEY"]
    body = {
        "model": MODEL_ID,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": image_reference(image)}},
                {"type": "text", "text": question},
            ],
        }],
    }
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.build_opener(RejectRedirects()).open(request, timeout=60) as response:
        data = json.load(response)
    return data["choices"][0]["message"]["content"]


answer = recognize_image("/absolute/path/to/product.jpg", "请提取商品名称、材质和数量。")
print(answer)
```

现有项目中的 [test_vision.py](test_vision.py) 也可以直接复制到其他项目，作为带命令行错误处理的版本。

## 5. 只读取最终回答

响应可能包含 `reasoning_content`。业务界面、数据库和日志通常只应使用：

```python
answer = response["choices"][0]["message"]["content"]
```

不要向终端、前端或日志直接透传完整响应，以避免内容冗长并减少敏感上下文暴露。

## 6. 提示词建议

将输出要求写清楚，准确率和后处理会更稳定：

```text
识别这张商品图。请只返回 JSON：
{
  "product_name": "",
  "material": "",
  "quantity": null,
  "use_cases": [],
  "confidence_notes": ""
}
若无法从图中确定材质或数量，填 null，并说明原因；不要猜测。
```

图片中的精确数量、材质、品牌和规格可能存在遮挡或视觉歧义。用于商品上架、质检或报价时，应保留人工复核或与商品主数据交叉验证。

## 7. 常见错误排查

| 现象 | 常见原因 | 处理方式 |
| --- | --- | --- |
| `401 AuthenticationError` | Key 缺失、过期、格式错误或被截断 | 重新复制完整的轮换后 Key，并确认环境变量已导出。 |
| `403` | 账号、项目或模型没有调用权限 | 在方舟控制台检查模型开通状态和项目权限。 |
| `400` | 请求 JSON、模型 ID、图片格式或 URL 不符合要求 | 检查 `content` 数组中的 `image_url` 和 `text` 块。 |
| 图片 URL 无法读取 | URL 不是公网可访问或已过期 | 使用有效的 HTTPS URL，或改为 Data URL。 |
| 返回内容太长 | 将完整响应或 `reasoning_content` 输出了 | 仅取 `choices[0].message.content`。 |

## 8. 上线检查清单

- [ ] Key 由部署环境的密钥管理或环境变量注入。
- [ ] 不记录 Authorization 请求头和完整 API Key。
- [ ] 对图片大小、格式和来源做服务端校验。
- [ ] 为网络超时、HTTP 错误和非 JSON 响应设置可观测日志与重试策略。
- [ ] 仅存储必要的最终答案，按业务合规要求处理原图与识别结果。
- [ ] 评估图片输入和输出 token 的费用，并设置预算/限额。
