图片超分模型（POD/产品图交付清晰化用）
=====================================

realesr-general-x4v3.onnx
- 用途：Real-ESRGAN 通用 4× 超分（Compact/SRVGGNet 变体），由 wh_local/media_enhance.py 加载。
- 输入：float32 [1,3,H,W]，RGB，范围 [0,1]；输出：[1,3,4H,4W]，范围 [0,1]。
- 来源：Heliosoph/realesrgan-onnx（HuggingFace 再分发），上游为 xinntao/Real-ESRGAN（Tencent ARC Lab）。
    https://huggingface.co/Heliosoph/realesrgan-onnx
    https://github.com/xinntao/Real-ESRGAN
- 许可：BSD-3-Clause（与上游一致）。
- 说明：模型随包分发，运行期不联网下载；推理走已有 onnxruntime（CPUExecutionProvider）。
