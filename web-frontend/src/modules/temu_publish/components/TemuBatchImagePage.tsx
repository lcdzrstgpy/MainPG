import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";

import type { TemuDraftEntry } from "../types";
import { CAROUSEL_MAX, CAROUSEL_MIN, resolveEntryImages, sameImages } from "../data/draftImages";

/**
 * 整页「批量编辑产品图片」（对齐店小秘 / Temu 后台）。
 *
 * 只有一条链路：选图片类型（我们只有轮播图）→ 选修改方式 → 填序号/地址 → 执行 → 更新。
 * 执行**只作用于勾选的行**（默认全勾），单个格子（缩略图的 × / +）随时可改。
 *
 * 不做「批量改尺寸 / 改格式 / 美图 / AI处理」那排按钮：本地没有图像处理能力，
 * 摆上去只能点出一个转圈，比没有更糟。
 */

type Mode = "delete" | "cover" | "add" | "trim";

const MODES: Array<{ key: Mode; label: string; hint: string }> = [
  { key: "delete", label: "删除图片", hint: "按序号删掉每一行的第 N 张" },
  { key: "cover", label: "切换首图", hint: "把第 N 张移到第一位（首图）" },
  { key: "add", label: "添加图片", hint: "在第 N 张后面插入一个图片地址" },
  { key: "trim", label: "超出移除", hint: "只保留前 N 张，其余全部移除" },
];

type Props = {
  /** 勾选的草稿 */
  entries: TemuDraftEntry[];
  /** 现有图片覆盖（entry.id → 图片列表） */
  imageEdits: Record<string, string[]>;
  onSave: (next: Record<string, string[]>) => void;
  onClose: () => void;
};

export function TemuBatchImagePage({ entries, imageEdits, onSave, onClose }: Props) {
  /** 每行当前要用的图（初始 = 覆盖值或源图） */
  const [draft, setDraft] = useState<Record<string, string[]>>(() => {
    const init: Record<string, string[]> = {};
    entries.forEach((entry) => {
      init[entry.id] = resolveEntryImages(entry, imageEdits);
    });
    return init;
  });
  /** 勾选的行（默认全勾，执行只动这些） */
  const [picked, setPicked] = useState<string[]>(() => entries.map((entry) => entry.id));
  const [mode, setMode] = useState<Mode>("delete");
  const [count, setCount] = useState("1");
  const [addUrl, setAddUrl] = useState("");
  /** 搜索：只过滤列表显示，不影响勾选 */
  const [query, setQuery] = useState("");
  const [applied, setApplied] = useState("");
  /** 上一次「执行」改前的图：批量操作一大片，留一条退路 */
  const [lastApply, setLastApply] = useState<
    null | { label: string; previous: Record<string, string[]> }
  >(null);
  /** 哪一行正在填新图地址（null = 没开） */
  const [addingIn, setAddingIn] = useState<string | null>(null);

  const rows = useMemo(
    () =>
      entries.filter((entry) =>
        query.trim() ? (entry.title || entry.subtitle || "").includes(query.trim()) : true,
      ),
    [entries, query],
  );
  const pickedRows = useMemo(
    () => rows.filter((entry) => picked.includes(entry.id)),
    [rows, picked],
  );
  const allPicked = rows.length > 0 && rows.every((entry) => picked.includes(entry.id));

  const imagesOf = (entry: TemuDraftEntry) => draft[entry.id] ?? resolveEntryImages(entry, imageEdits);

  const setImages = (id: string, next: string[]) =>
    setDraft((current) => ({ ...current, [id]: next }));

  const togglePick = (id: string) =>
    setPicked((current) =>
      current.includes(id) ? current.filter((item) => item !== id) : [...current, id],
    );

  /** 改了几条（和源图不一样的行数） */
  const changedCount = entries.filter((entry) => {
    const source = resolveEntryImages(entry, {});
    return !sameImages(imagesOf(entry), source);
  }).length;

  const apply = () => {
    const n = Number(count);
    if (!Number.isInteger(n) || n < 1) {
      setApplied("序号要填 1 以上的整数");
      return;
    }
    if (mode === "add" && !addUrl.trim()) {
      setApplied("先填要添加的图片地址");
      return;
    }
    if (!pickedRows.length) {
      setApplied("先勾选要改的行");
      return;
    }
    const previous: Record<string, string[]> = {};
    const next: Record<string, string[]> = { ...draft };
    let touched = 0;
    let skipped = 0;

    pickedRows.forEach((entry) => {
      const current = imagesOf(entry);
      previous[entry.id] = current;
      let result = current;
      if (mode === "delete") {
        if (n > current.length) {
          skipped += 1;
          return;
        }
        result = current.filter((_, index) => index !== n - 1);
      } else if (mode === "cover") {
        if (n > current.length) {
          skipped += 1;
          return;
        }
        if (n === 1) return;
        result = [current[n - 1], ...current.filter((_, index) => index !== n - 1)];
      } else if (mode === "add") {
        const at = Math.min(n, current.length);
        result = [...current.slice(0, at), addUrl.trim(), ...current.slice(at)];
      } else {
        result = current.slice(0, n);
      }
      next[entry.id] = result;
      touched += 1;
    });

    setDraft(next);
    const label = MODES.find((item) => item.key === mode)?.label ?? "";
    const extra = skipped ? `，${skipped} 条没有第 ${n} 张（跳过）` : "";
    setApplied(`${label}：改了 ${touched} 条${extra}`);
    setLastApply(touched ? { label, previous } : null);
    if (mode === "add") setAddUrl("");
  };

  const undoApply = () => {
    if (!lastApply) return;
    setDraft((current) => ({ ...current, ...lastApply.previous }));
    setApplied(`已撤销「${lastApply.label}」`);
    setLastApply(null);
  };

  const save = () => {
    const next: Record<string, string[]> = { ...imageEdits };
    entries.forEach((entry) => {
      const source = resolveEntryImages(entry, {});
      const current = imagesOf(entry);
      // 和源图一致就把覆盖删掉：留着只会让「明明没改」的行看起来像改过
      if (sameImages(current, source)) delete next[entry.id];
      else next[entry.id] = current;
    });
    onSave(next);
  };

  // Esc 关闭（整页覆盖必须留键盘退路）
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // 切页签时自己收掉（portal 到 body，页签只是 hidden）
  useEffect(() => {
    const onSwitch = () => onClose();
    window.addEventListener("mainpg:tab-switch", onSwitch);
    return () => window.removeEventListener("mainpg:tab-switch", onSwitch);
  }, [onClose]);

  const inputNumberPlaceholder = mode === "trim" ? "只保留前几张" : "第几张";

  return createPortal(
    <div className="temu-batch-edit temu-img-edit" role="dialog" aria-modal="true" aria-label="批量编辑产品图片">
      <header className="temu-batch-edit-head">
        <div className="temu-img-edit-title">
          <button type="button" className="temu-img-edit-back" onClick={onClose} title="返回（不保存）">
            ←
          </button>
          <div>
            <strong>批量编辑产品图片</strong>
            <span>当前位置：产品 &gt; 批量编辑产品图片 · 已选 {entries.length} 条</span>
          </div>
        </div>
        <div className="temu-batch-edit-head-actions">
          <button type="button" className="temu-btn" onClick={onClose}>
            取消
          </button>
          <button type="button" className="temu-btn temu-btn-primary" onClick={save}>
            更新{changedCount ? `（改了 ${changedCount} 条）` : ""}
          </button>
        </div>
      </header>

      <section className="temu-img-edit-panel">
        <label className="temu-img-edit-row">
          <span>搜索类型</span>
          <select className="temu-input" value="title" disabled>
            <option value="title">标题</option>
          </select>
          <span className="temu-img-edit-gap">搜索内容</span>
          <input
            className="temu-input temu-img-edit-search"
            value={query}
            placeholder="按标题筛选（只影响列表显示）"
            onChange={(event) => setQuery(event.target.value)}
          />
          <button
            type="button"
            className="temu-btn temu-btn-primary"
            onClick={() => setApplied(query.trim() ? `筛出 ${rows.length} 条` : `共 ${rows.length} 条`)}
          >
            搜索
          </button>
        </label>

        <div className="temu-img-edit-row">
          <span>图片类型</span>
          <label className="temu-img-edit-radio">
            <input type="radio" checked readOnly /> 产品轮播图
          </label>
          <label className="temu-img-edit-radio is-off" title="当前草稿只有轮播图，没有 SKC 图/素材图/变种预览图">
            <input type="radio" disabled /> 产品SKC图
          </label>
          <label className="temu-img-edit-radio is-off" title="当前草稿只有轮播图">
            <input type="radio" disabled /> 产品素材图
          </label>
          <label className="temu-img-edit-radio is-off" title="当前草稿只有轮播图">
            <input type="radio" disabled /> 变种预览图
          </label>
        </div>

        <div className="temu-img-edit-row">
          <span>修改方式</span>
          {MODES.map((item) => (
            <label key={item.key} className="temu-img-edit-radio" title={item.hint}>
              <input
                type="radio"
                name="temu-img-mode"
                checked={mode === item.key}
                onChange={() => {
                  setMode(item.key);
                  setApplied("");
                }}
              />
              {item.label}
            </label>
          ))}
        </div>

        <div className="temu-img-edit-row">
          <span>修改内容</span>
          {mode === "add" ? (
            <>
              <span className="temu-img-edit-text">在第</span>
              <input
                className="temu-input temu-img-edit-num"
                value={count}
                placeholder={inputNumberPlaceholder}
                onChange={(event) => setCount(event.target.value.replace(/[^\d]/g, ""))}
              />
              <span className="temu-img-edit-text">张后面插入</span>
              <input
                className="temu-input temu-img-edit-url"
                value={addUrl}
                placeholder="粘贴图片地址 https://…"
                onChange={(event) => setAddUrl(event.target.value)}
              />
            </>
          ) : (
            <>
              <span className="temu-img-edit-text">
                {mode === "delete" ? "删除第" : mode === "cover" ? "把第" : "只保留前"}
              </span>
              <input
                className="temu-input temu-img-edit-num"
                value={count}
                placeholder={inputNumberPlaceholder}
                onChange={(event) => setCount(event.target.value.replace(/[^\d]/g, ""))}
              />
              <span className="temu-img-edit-text">
                {mode === "delete" ? "张图片" : mode === "cover" ? "张设为封面" : "张，其余移除"}
              </span>
            </>
          )}
          <button type="button" className="temu-btn temu-btn-primary" onClick={apply}>
            执行
          </button>
          {lastApply ? (
            <button type="button" className="temu-link" onClick={undoApply}>
              撤销「{lastApply.label}」
            </button>
          ) : null}
          {applied ? <em className="temu-img-edit-result">{applied}</em> : null}
        </div>

        <ul className="temu-img-edit-rules">
          <li>只改产品轮播图；执行范围是<b>勾选的行</b>（当前 {pickedRows.length}/{rows.length} 条）。</li>
          <li>轮播图要求 {CAROUSEL_MIN}~{CAROUSEL_MAX} 张，少于 {CAROUSEL_MIN} 张 Temu 会判图片不足。</li>
          <li>图片会原样提交给 Temu，尺寸/格式请先在图像服务里处理好再放进来。</li>
        </ul>
      </section>

      <div className="temu-img-edit-list">
        <div className="temu-img-edit-list-head">
          <label className="temu-img-edit-all">
            <input
              type="checkbox"
              checked={allPicked}
              onChange={(event) =>
                setPicked(event.target.checked ? rows.map((entry) => entry.id) : [])
              }
            />
            全选（{rows.length} 条）
          </label>
          <span className="temu-muted">点缩略图右上角「×」删单张，行尾「+ 添加图片」补图</span>
        </div>

        {rows.map((entry) => {
          const images = imagesOf(entry);
          const tooFew = images.length < CAROUSEL_MIN;
          const tooMany = images.length > CAROUSEL_MAX;
          return (
            <div
              key={entry.id}
              className={`temu-img-edit-item${picked.includes(entry.id) ? "" : " is-unpicked"}`}
            >
              <label className="temu-img-edit-item-pick">
                <input
                  type="checkbox"
                  checked={picked.includes(entry.id)}
                  onChange={() => togglePick(entry.id)}
                  title="勾上的行才会被「执行」改到"
                />
              </label>

              <div className="temu-img-edit-meta">
                <img src={entry.main_image || entry.images[0] || ""} alt="" loading="lazy" />
                <div>
                  <p className="temu-img-edit-name">{entry.title || entry.subtitle || entry.id}</p>
                  <span className="temu-img-edit-badge">
                    {tooFew || tooMany ? (
                      <em className="temu-img-edit-warn">
                        {images.length} 张（要求 {CAROUSEL_MIN}~{CAROUSEL_MAX}）
                      </em>
                    ) : (
                      <>已保存 {images.length} 张</>
                    )}
                  </span>
                </div>
              </div>

              <div className="temu-img-edit-thumbs">
                {images.map((url, index) => (
                  <figure key={`${entry.id}-${index}`} className="temu-img-edit-thumb">
                    <img src={url} alt="" loading="lazy" />
                    <span className={`temu-img-edit-no${index === 0 ? " is-cover" : ""}`}>
                      {index === 0 ? "首图" : index + 1}
                    </span>
                    <button
                      type="button"
                      className="temu-img-edit-del"
                      title="删除这张"
                      onClick={() =>
                        setImages(
                          entry.id,
                          images.filter((_, i) => i !== index),
                        )
                      }
                    >
                      ×
                    </button>
                  </figure>
                ))}

                {addingIn === entry.id ? (
                  <div className="temu-img-edit-add is-open">
                    <input
                      autoFocus
                      className="temu-input"
                      value={addUrl}
                      placeholder="粘贴图片地址，回车添加"
                      onChange={(event) => setAddUrl(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key !== "Enter") return;
                        const url = addUrl.trim();
                        if (!url) return;
                        setImages(entry.id, [...images, url]);
                        setAddUrl("");
                        setAddingIn(null);
                      }}
                    />
                    <div>
                      <button
                        type="button"
                        className="temu-btn temu-btn-primary"
                        onClick={() => {
                          const url = addUrl.trim();
                          if (!url) return;
                          setImages(entry.id, [...images, url]);
                          setAddUrl("");
                          setAddingIn(null);
                        }}
                      >
                        添加
                      </button>
                      <button
                        type="button"
                        className="temu-link"
                        onClick={() => {
                          setAddingIn(null);
                          setAddUrl("");
                        }}
                      >
                        取消
                      </button>
                    </div>
                  </div>
                ) : (
                  <button
                    type="button"
                    className="temu-img-edit-add"
                    onClick={() => {
                      setAddingIn(entry.id);
                      setAddUrl("");
                    }}
                  >
                    + 添加图片
                  </button>
                )}
              </div>
            </div>
          );
        })}

        {!rows.length ? <p className="temu-muted temu-img-edit-empty">没有匹配的行</p> : null}
      </div>
    </div>,
    document.body,
  );
}
