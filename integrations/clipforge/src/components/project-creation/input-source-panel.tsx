"use client";

import { useRef, useState } from "react";
import { LuLink2, LuLoader, LuUpload, LuX } from "react-icons/lu";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { INPUT_MODE_OPTIONS, MAX_SOURCE_IMAGES } from "./creation-brief-defaults";
import type { InputMode } from "./creation-brief-types";

/** A local file picked by the user, kept next to its object URL for the preview grid. */
export interface InputSourceImage {
  id: string;
  url: string;
  file: File;
}

export interface InputSourcePanelProps {
  inputMode: InputMode;
  onInputModeChange: (mode: InputMode) => void;
  images: InputSourceImage[];
  onFilesSelected: (files: FileList | null) => void;
  onRemoveImage: (id: string) => void;
  linkUrl: string;
  onLinkUrlChange: (url: string) => void;
  /** Only rendered when the hosting page supplies the import handler (it issues an API request). */
  onImportLink?: (url: string) => void;
  importing?: boolean;
  importError?: string;
  /** One-sentence topic, used when the brief's input mode is `topic`. */
  topic: string;
  onTopicChange: (topic: string) => void;
  disabled?: boolean;
}

export function InputSourcePanel({
  inputMode,
  onInputModeChange,
  images,
  onFilesSelected,
  onRemoveImage,
  linkUrl,
  onLinkUrlChange,
  onImportLink,
  importing,
  importError,
  topic,
  onTopicChange,
  disabled,
}: InputSourcePanelProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [isDragging, setIsDragging] = useState(false);
  const activeMode = INPUT_MODE_OPTIONS.find((option) => option.id === inputMode) ?? INPUT_MODE_OPTIONS[0];
  const limitReached = images.length >= MAX_SOURCE_IMAGES;
  // topic mode carries no product images: the one-sentence topic replaces them
  const wantsImages = inputMode !== "topic";

  return (
    <Card className="glass-card">
      <CardContent className="p-5 space-y-5">
        <div>
          <div className="flex items-center justify-between mb-3">
            <span className="text-sm font-semibold">
              来源
              <span className="text-destructive ml-0.5">*</span>
            </span>
            <span className="text-xs text-muted-foreground">
              商品图 {images.length}/{MAX_SOURCE_IMAGES}
            </span>
          </div>
          <div className="flex flex-wrap gap-2" role="radiogroup" aria-label="创作来源">
            {INPUT_MODE_OPTIONS.map((option) => {
              const active = option.id === inputMode;
              return (
                <button
                  key={option.id}
                  type="button"
                  role="radio"
                  aria-checked={active}
                  data-input-mode={option.id}
                  disabled={disabled}
                  onClick={() => onInputModeChange(option.id)}
                  className={`px-3 py-1.5 rounded-full border text-xs font-medium transition-all disabled:opacity-40 ${
                    active
                      ? "bg-primary/15 text-primary border-primary/30"
                      : "bg-muted/20 text-muted-foreground border-border/50 hover:border-primary/30"
                  }`}
                >
                  {option.label}
                </button>
              );
            })}
          </div>
          <p className="text-xs text-muted-foreground mt-2">{activeMode.description}</p>
        </div>

        {wantsImages && (
          <div>
            <Label className="text-sm font-medium mb-2 block">商品图</Label>
            {!limitReached && (
              <div
                className={`relative border-2 border-dashed rounded-xl p-6 text-center cursor-pointer transition-all ${
                  isDragging ? "border-primary bg-primary/5" : "border-border/60 hover:border-primary/50 hover:bg-muted/20"
                }`}
                onDragOver={(event) => {
                  event.preventDefault();
                  setIsDragging(true);
                }}
                onDragLeave={(event) => {
                  event.preventDefault();
                  setIsDragging(false);
                }}
                onDrop={(event) => {
                  event.preventDefault();
                  setIsDragging(false);
                  onFilesSelected(event.dataTransfer.files);
                }}
                onClick={() => fileInputRef.current?.click()}
              >
                <input
                  ref={fileInputRef}
                  type="file"
                  accept="image/*"
                  multiple
                  className="hidden"
                  disabled={disabled}
                  onChange={(event) => {
                    onFilesSelected(event.target.files);
                    event.target.value = "";
                  }}
                />
                <div className="flex flex-col items-center gap-2">
                  <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-muted/50">
                    <LuUpload className="w-5 h-5 text-muted-foreground" />
                  </div>
                  <p className="text-sm font-medium">
                    拖拽到此处，或<span className="brand-gradient-text font-semibold">点击上传</span>
                  </p>
                  <p className="text-xs text-muted-foreground">支持 PNG / JPG，最多 {MAX_SOURCE_IMAGES} 张</p>
                </div>
              </div>
            )}

            {images.length > 0 && (
              <div className={`grid grid-cols-3 sm:grid-cols-5 gap-3 ${limitReached ? "" : "mt-4"}`}>
                {images.map((image) => (
                  <div key={image.id} className="group relative aspect-square rounded-lg overflow-hidden border border-border/50 bg-muted/20">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={image.url} alt="商品图" className="h-full w-full object-cover" />
                    <button
                      type="button"
                      aria-label="移除商品图"
                      onClick={() => onRemoveImage(image.id)}
                      className="absolute top-1 right-1 flex h-6 w-6 items-center justify-center rounded-full bg-black/60 text-white opacity-0 group-hover:opacity-100 transition-opacity hover:bg-red-500"
                    >
                      <LuX className="w-3 h-3" />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {inputMode === "link" && (
          <div className="pt-4 border-t border-border/40">
            <p className="text-xs text-muted-foreground mb-2 flex items-center gap-1.5">
              <LuLink2 className="w-3.5 h-3.5" />
              粘贴商品链接，自动抓取标题、价格与图片
            </p>
            <div className="flex gap-2">
              <Input
                value={linkUrl}
                onChange={(event) => onLinkUrlChange(event.target.value)}
                placeholder="https://..."
                disabled={disabled || importing}
              />
              {onImportLink && (
                <Button
                  type="button"
                  onClick={() => onImportLink(linkUrl.trim())}
                  disabled={disabled || importing || !linkUrl.trim()}
                  className="shrink-0"
                >
                  {importing ? <LuLoader className="w-4 h-4 animate-spin" /> : "导入链接"}
                </Button>
              )}
            </div>
            {importError && <p className="text-xs text-destructive mt-2">{importError}</p>}
          </div>
        )}

        {inputMode === "topic" && (
          <div className="pt-4 border-t border-border/40">
            <Label className="text-sm font-medium mb-2 block">
              一句话主题
              <span className="text-destructive ml-0.5">*</span>
            </Label>
            <Textarea
              value={topic}
              onChange={(event) => onTopicChange(event.target.value)}
              rows={2}
              placeholder="例如：在家如何泡一杯手冲咖啡"
              disabled={disabled}
            />
          </div>
        )}

        {(inputMode === "product-library" || inputMode === "clone") && (
          <div className="pt-4 border-t border-border/40">
            <p className="text-xs text-muted-foreground">
              {inputMode === "product-library"
                ? "从商品库选好商品后回到这里：名称、品类与描述会被预填，你仍然可以逐项修改。"
                : "爆款复刻会沿用原片的结构与节奏，商品图与文案仍按本次填写生成。"}
            </p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
