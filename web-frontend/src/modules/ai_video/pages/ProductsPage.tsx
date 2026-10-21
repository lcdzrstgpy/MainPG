import { useRef, useState } from "react";

import { ingestProduct, uploadProductImages } from "../api/endpoints/products";
import type { ProductCategory, ProductItem } from "../api/types";
import { aiVideoMediaUrl } from "../lib/media-url";
import { writeStartPrefill } from "../lib/start-prefill";
import { useProductLibraryStore } from "../state/productLibraryStore";

const MAX_PRODUCT_IMAGES = 5;

const CATEGORY_OPTIONS: ReadonlyArray<{ value: ProductCategory; label: string }> = [
  { value: "beauty", label: "美妆护肤" },
  { value: "food", label: "食品零食" },
  { value: "home", label: "家居日用" },
  { value: "fashion", label: "服饰鞋包" },
  { value: "tech", label: "数码3C" },
  { value: "other", label: "其他" },
];

type FormImage = { id: string; url: string; file?: File };

type ProductsPageProps = {
  onNavigate: (path: string) => void;
};

function categoryLabel(category: ProductCategory): string {
  return CATEGORY_OPTIONS.find((option) => option.value === category)?.label ?? category;
}

/** 商品素材：商品库 CRUD + 链接导入 review gate + 图片上传；「做视频」预填交接给 /start。 */
export function ProductsPage({ onNavigate }: ProductsPageProps) {
  const { products, addProduct, updateProduct, removeProduct } = useProductLibraryStore();

  const [query, setQuery] = useState("");
  const [categoryFilter, setCategoryFilter] = useState<"all" | ProductCategory>("all");
  const [notice, setNotice] = useState("");

  // 表单（新增 / 编辑 / 导入确认共用）
  const [formOpen, setFormOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [category, setCategory] = useState<ProductCategory>("other");
  const [description, setDescription] = useState("");
  const [price, setPrice] = useState("");
  const [targetAudience, setTargetAudience] = useState("");
  const [images, setImages] = useState<FormImage[]>([]);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  // 链接导入时图片已按这个 id 落到服务端，保存必须复用它，否则图片与商品对不上。
  const importIdRef = useRef<string | null>(null);

  // 链接导入
  const [importOpen, setImportOpen] = useState(false);
  const [importUrl, setImportUrl] = useState("");
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState("");

  const filtered = products.filter((product) => {
    if (categoryFilter !== "all" && product.category !== categoryFilter) return false;
    const keyword = query.trim().toLowerCase();
    if (!keyword) return true;
    return product.name.toLowerCase().includes(keyword) || (product.description ?? "").toLowerCase().includes(keyword);
  });

  function resetForm() {
    images.forEach((image) => {
      if (image.file) URL.revokeObjectURL(image.url);
    });
    setFormOpen(false);
    setEditingId(null);
    setName("");
    setCategory("other");
    setDescription("");
    setPrice("");
    setTargetAudience("");
    setImages([]);
    setSaveError(null);
    importIdRef.current = null;
  }

  function openCreate() {
    resetForm();
    setFormOpen(true);
  }

  function startEdit(product: ProductItem) {
    setEditingId(product.id);
    setName(product.name);
    setCategory(product.category);
    setDescription(product.description ?? "");
    setPrice(product.price ?? "");
    setTargetAudience(product.targetAudience ?? "");
    setImages(product.images.slice(0, MAX_PRODUCT_IMAGES).map((url) => ({ id: crypto.randomUUID(), url })));
    setSaveError(null);
    importIdRef.current = null;
    setFormOpen(true);
  }

  function addFiles(files: FileList | null) {
    if (!files) return;
    const remaining = MAX_PRODUCT_IMAGES - images.length;
    if (remaining <= 0) return;
    const added = Array.from(files)
      .slice(0, remaining)
      .filter((file) => file.type.startsWith("image/"))
      .map((file) => ({ id: crypto.randomUUID(), url: URL.createObjectURL(file), file }));
    if (added.length) setImages((current) => [...current, ...added]);
  }

  function removeImage(id: string) {
    setImages((current) => {
      const target = current.find((image) => image.id === id);
      if (target?.file) URL.revokeObjectURL(target.url);
      return current.filter((image) => image.id !== id);
    });
  }

  async function handleExtract() {
    const url = importUrl.trim();
    if (!url || importing) return;
    setImporting(true);
    setImportError("");
    try {
      const libraryProductId = crypto.randomUUID();
      const result = await ingestProduct({ url, createProject: false, libraryProductId });
      const product = result.product;
      importIdRef.current = libraryProductId;
      setEditingId(null);
      setName(product.title ?? "");
      setCategory("other");
      setDescription(product.description ?? "");
      setPrice(product.priceText ?? "");
      setTargetAudience("");
      setImages((result.images ?? []).slice(0, MAX_PRODUCT_IMAGES).map((imageUrl) => ({
        id: crypto.randomUUID(),
        url: imageUrl,
      })));
      setSaveError(null);
      setFormOpen(true);
      setImportOpen(false);
      setImportUrl("");
    } catch (cause) {
      setImportError(cause instanceof Error ? cause.message : "商品链接解析失败");
    } finally {
      setImporting(false);
    }
  }

  async function handleSave() {
    if (!name.trim() || saving) return;
    setSaving(true);
    setSaveError(null);
    try {
      const productId = editingId ?? importIdRef.current ?? crypto.randomUUID();
      const pendingFiles = images.flatMap((image) => (image.file ? [image.file] : []));
      let uploadedPaths: string[] = [];
      if (pendingFiles.length > 0) {
        const uploaded = await uploadProductImages(productId, pendingFiles);
        uploadedPaths = uploaded.paths ?? [];
      }
      let cursor = 0;
      const imageUrls = images
        .map((image) => (image.file ? uploadedPaths[cursor++] : image.url))
        .slice(0, MAX_PRODUCT_IMAGES);
      const base = {
        name: name.trim(),
        category,
        images: imageUrls,
        ...(description.trim() ? { description: description.trim() } : {}),
        ...(price.trim() ? { price: price.trim() } : {}),
        ...(targetAudience.trim() ? { targetAudience: targetAudience.trim() } : {}),
      };
      if (editingId) {
        updateProduct(editingId, base);
        setNotice(`已更新「${base.name}」`);
      } else {
        addProduct({ id: productId, ...base, videoCount: 0, createdAt: new Date() });
        setNotice(`已加入商品库：${base.name}`);
      }
      resetForm();
    } catch (cause) {
      setSaveError(cause instanceof Error ? cause.message : "保存失败，请稍后重试");
    } finally {
      setSaving(false);
    }
  }

  function handleDelete(product: ProductItem) {
    if (!window.confirm(`确定删除商品「${product.name}」吗？已创建的视频项目不受影响。`)) return;
    removeProduct(product.id);
  }

  function makeVideo(product: ProductItem) {
    writeStartPrefill(window.localStorage, { productId: product.id });
    onNavigate("/start");
  }

  return (
    <section className="ai-video-page" aria-label="商品素材">
      <header className="ai-video-module-header">
        <div>
          <p className="ai-video-eyebrow">AI 创作工具</p>
          <h2>商品素材</h2>
          <p>把常用商品存进商品库：做视频时直接选，不用每次重填名称、卖点与图片。</p>
        </div>
        <div className="ai-video-header-actions">
          <button type="button" className="is-secondary" onClick={() => setImportOpen((open) => !open)}>
            {importOpen ? "收起链接导入" : "导入商品链接"}
          </button>
          <button type="button" className="ai-video-header-action" onClick={openCreate}>新建商品</button>
        </div>
      </header>

      {notice && (
        <div className="ai-video-notice" role="status">
          <span>{notice}</span>
          <button type="button" aria-label="关闭提示" onClick={() => setNotice("")}>×</button>
        </div>
      )}

      {importOpen && (
        <section className="ai-video-form-card" aria-label="导入商品链接">
          <div className="ai-video-form-head">
            <h3>导入商品链接</h3>
            <p>解析结果会先放进编辑表单，确认无误后才入库；图片最多 5 张。</p>
          </div>
          <div className="ai-video-link-row">
            <input
              type="url"
              value={importUrl}
              placeholder="https://..."
              onChange={(event) => setImportUrl(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void handleExtract();
              }}
            />
            <button type="button" disabled={importing || !importUrl.trim()} onClick={() => void handleExtract()}>
              {importing ? "正在解析…" : "解析并预填"}
            </button>
            {importError && <span className="ai-video-settings-message is-error">{importError}</span>}
          </div>
        </section>
      )}

      <div className="ai-video-toolbar">
        <div className="ai-video-chip-row">
          <button
            type="button"
            className={`ai-video-chip${categoryFilter === "all" ? " is-active" : ""}`}
            aria-pressed={categoryFilter === "all"}
            onClick={() => setCategoryFilter("all")}
          >
            全部
          </button>
          {CATEGORY_OPTIONS.map((option) => (
            <button
              key={option.value}
              type="button"
              className={`ai-video-chip${categoryFilter === option.value ? " is-active" : ""}`}
              aria-pressed={categoryFilter === option.value}
              onClick={() => setCategoryFilter(option.value)}
            >
              {option.label}
            </button>
          ))}
        </div>
        {products.length > 0 && (
          <input
            type="search"
            className="ai-video-search"
            value={query}
            placeholder="搜索商品名 / 卖点"
            onChange={(event) => setQuery(event.target.value)}
          />
        )}
      </div>

      {products.length === 0 ? (
        <div className="ai-video-empty">
          <span className="ai-video-empty-icon">◇</span>
          <strong>商品库还是空的</strong>
          <p>新建一个商品，或粘贴商品链接自动抓取名称、价格与图片。</p>
          <button type="button" onClick={openCreate}>新建第一个商品</button>
        </div>
      ) : filtered.length === 0 ? (
        <p className="ai-video-list-hint">没有匹配的商品。</p>
      ) : (
        <div className="ai-video-products-grid">
          {filtered.map((product) => {
            const cover = aiVideoMediaUrl(product.images[0] ?? null);
            return (
              <article key={product.id} className="ai-video-product-card">
                <div className="ai-video-product-media">
                  {cover ? (
                    <img src={cover} alt="" loading="lazy" />
                  ) : (
                    <span className="ai-video-product-placeholder">◇</span>
                  )}
                  <em>{categoryLabel(product.category)}</em>
                </div>
                <div className="ai-video-product-body">
                  <strong>{product.name}</strong>
                  <span>
                    {product.price ? `${product.price} · ` : ""}
                    已出片 {product.videoCount} 条
                  </span>
                  {product.description && <p>{product.description}</p>}
                </div>
                <div className="ai-video-product-actions">
                  <button type="button" className="is-primary" onClick={() => makeVideo(product)}>做视频</button>
                  <button type="button" onClick={() => startEdit(product)}>编辑</button>
                  <button type="button" className="is-danger" onClick={() => handleDelete(product)}>删除</button>
                </div>
              </article>
            );
          })}
        </div>
      )}

      {formOpen && (
        <div className="ai-video-modal-backdrop" role="presentation" onClick={resetForm}>
          <div
            className="ai-video-modal"
            role="dialog"
            aria-modal="true"
            aria-label={editingId ? "编辑商品" : "新增商品"}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="ai-video-modal-head">
              <h3>{editingId ? "编辑商品" : importIdRef.current ? "确认入库" : "新增商品"}</h3>
              <button type="button" aria-label="关闭" onClick={resetForm}>×</button>
            </div>

            <div className="ai-video-settings-grid">
              <div className="ai-video-field">
                <label htmlFor="ai-video-product-form-name">商品名称 *</label>
                <input
                  id="ai-video-product-form-name"
                  value={name}
                  placeholder="例如：桂花乌龙茶"
                  onChange={(event) => setName(event.target.value)}
                />
              </div>
              <div className="ai-video-field">
                <label htmlFor="ai-video-product-form-category">品类</label>
                <select
                  id="ai-video-product-form-category"
                  value={category}
                  onChange={(event) => setCategory(event.target.value as ProductCategory)}
                >
                  {CATEGORY_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>{option.label}</option>
                  ))}
                </select>
              </div>
              <div className="ai-video-field">
                <label htmlFor="ai-video-product-form-price">价格</label>
                <input
                  id="ai-video-product-form-price"
                  value={price}
                  placeholder="例如：¥39.9"
                  onChange={(event) => setPrice(event.target.value)}
                />
              </div>
              <div className="ai-video-field">
                <label htmlFor="ai-video-product-form-audience">目标人群</label>
                <input
                  id="ai-video-product-form-audience"
                  value={targetAudience}
                  placeholder="例如：上班族"
                  onChange={(event) => setTargetAudience(event.target.value)}
                />
              </div>
            </div>

            <div className="ai-video-field">
              <label htmlFor="ai-video-product-form-description">卖点描述</label>
              <textarea
                id="ai-video-product-form-description"
                rows={3}
                value={description}
                placeholder="例如：0 糖 0 卡，3 秒出泡，冷热皆可"
                onChange={(event) => setDescription(event.target.value)}
              />
            </div>

            <div className="ai-video-upload">
              <input
                ref={fileInputRef}
                type="file"
                accept="image/*"
                multiple
                hidden
                onChange={(event) => {
                  addFiles(event.target.files);
                  event.target.value = "";
                }}
              />
              <button
                type="button"
                className="ai-video-upload-drop"
                disabled={images.length >= MAX_PRODUCT_IMAGES}
                onClick={() => fileInputRef.current?.click()}
              >
                <strong>商品图（{images.length}/{MAX_PRODUCT_IMAGES}）</strong>
                <span>最多 5 张，单张不超过 20MB</span>
              </button>
              {images.length > 0 && (
                <div className="ai-video-thumbs">
                  {images.map((image) => {
                    const url = aiVideoMediaUrl(image.url);
                    return (
                      <figure key={image.id} className="ai-video-thumb">
                        {url && <img src={url} alt="" />}
                        <button type="button" aria-label="移除图片" onClick={() => removeImage(image.id)}>×</button>
                      </figure>
                    );
                  })}
                </div>
              )}
            </div>

            {saveError && <div className="ai-video-form-error" role="alert">{saveError}</div>}

            <div className="ai-video-settings-actions">
              <button type="button" disabled={!name.trim() || saving} onClick={() => void handleSave()}>
                {saving ? "正在保存…" : importIdRef.current && !editingId ? "确认入库" : "保存"}
              </button>
              <button type="button" className="is-secondary" disabled={saving} onClick={resetForm}>取消</button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
