"use client";

import { Card, CardContent } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { OUTPUT_SCHEMES, type OutputSchemeId, type OutputSchemeSnapshot } from "@/lib/output-schemes";
import { optionCardClass, optionChipClass, SelectionCheck } from "./option-state-styles";

const SCHEME_CARDS: ReadonlyArray<{
  id: OutputSchemeId;
  title: string;
  chain: string;
  detail: string;
}> = [
  { id: "draft", title: "免费草稿", chain: "脚本 → 商品图/静态素材 → 可选配音 → FFmpeg", detail: "静态合成，不调用生视频模型；无视频模型费用" },
  { id: "controlled-rapid", title: "导演动态·极速试片", chain: "脚本 → 逐镜关键帧 → 逐镜 I2V → 本地合成", detail: "720p · 每镜 4 秒 · 轻运镜 · 无接缝" },
  { id: "controlled-balanced", title: "导演动态·智能均衡", chain: "脚本 → 逐镜关键帧 → 逐镜 I2V → 本地合成", detail: "720p · 每镜 5 秒 · 中运镜 · 钉帧衔接" },
  { id: "controlled-cinematic", title: "导演动态·品牌大片", chain: "脚本 → 逐镜关键帧 → 逐镜 I2V → 本地合成", detail: "1080p · 每镜 8 秒 · 强运镜 · 尾帧续拍" },
  { id: "native-film", title: "原生整片", chain: "脚本 → 九宫格参考图 → 一次整片视频模型调用", detail: "整片画面与音轨一次生成，不走逐镜 I2V" },
];

export function OutputSchemePanel({ value, onChange, disabled }: {
  value: OutputSchemeSnapshot;
  onChange: (value: OutputSchemeSnapshot) => void;
  disabled?: boolean;
}) {
  const native = value.id === "native-film";

  return (
    <Card className="glass-card">
      <CardContent className="p-5 space-y-5">
        <div className="space-y-3">
          <div className="flex items-center justify-between gap-3">
            <Label className="text-sm font-medium">出片方案</Label>
            <span className="text-xs text-muted-foreground">方案决定生成链路、画质和费用</span>
          </div>
          <div role="radiogroup" aria-label="出片方案" className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3">
            {SCHEME_CARDS.map((card) => {
              const selected = card.id === value.id;
              return (
                <button
                  key={card.id}
                  type="button"
                  role="radio"
                  aria-checked={selected}
                  data-scheme={card.id}
                  disabled={disabled}
                  onClick={() => onChange({ ...OUTPUT_SCHEMES[card.id] })}
                  className={`rounded-lg p-4 text-left ${optionCardClass(selected)}`}
                >
                  {selected && <SelectionCheck className="absolute right-3 top-3" />}
                  <span className="block pr-6 text-sm font-semibold">{card.title}</span>
                  <span className={`mt-2 block text-xs ${selected ? "text-primary-foreground/90" : "text-foreground"}`}>{card.chain}</span>
                  <span className={`mt-2 block text-[11px] ${selected ? "text-primary-foreground/75" : "text-muted-foreground"}`}>{card.detail}</span>
                </button>
              );
            })}
          </div>
        </div>

        <div className="pt-4 border-t border-border/40 space-y-2">
          <Label className="text-sm font-medium">音频策略</Label>
          {native ? (
            <p className="text-xs text-muted-foreground">模型原生音频 · 整片由视频模型生成音轨，方案固定使用原生音频。</p>
          ) : (
            <div role="radiogroup" aria-label="音频策略" className="flex flex-wrap gap-2">
              {([
                { id: "volcengine-tts", label: "火山语音配音（TTS）" },
                { id: "mute", label: "静音（不加人声）" },
              ] as const).map((option) => {
                const selected = value.audioStrategy === option.id;
                return (
                  <button
                    key={option.id}
                    type="button"
                    role="radio"
                    aria-checked={selected}
                    data-audio-strategy={option.id}
                    disabled={disabled}
                    onClick={() => onChange({ ...value, audioStrategy: option.id })}
                    className={optionChipClass(selected)}
                  >
                    {selected && <SelectionCheck />}
                    {option.label}
                  </button>
                );
              })}
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
