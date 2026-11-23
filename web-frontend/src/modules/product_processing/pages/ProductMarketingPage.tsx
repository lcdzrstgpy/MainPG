import { useEffect, useState } from 'react';

import { productProcessingApiContext } from '../api/context';
import { generateMarketingImage, MARKETING_TEMPLATES, type MarketingResult, type MarketingTemplate } from '../api/marketingApi';
import { listProductDrafts, type ProductDraft } from '../api/productProcessingApi';

/**
 * 精选处理（独立板块）：选商品 → 选版式 → 生成营销图。
 *
 * 双层合成：AI 出照片层（Seedream，产品保真、禁文字），本地排版层渲染英文
 * 标题/副文案（绝不拼错）。每张 = 1 次生图计费（复用图片子项）。
 */
export function ProductMarketingPage() {
  const ctx = productProcessingApiContext();
  const [drafts, setDrafts] = useState<ProductDraft[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [templates, setTemplates] = useState<MarketingTemplate[]>(['headline_hero', 'action_proof']);
  const [results, setResults] = useState<MarketingResult[]>([]);
  const [running, setRunning] = useState(false);
  const [note, setNote] = useState('');

  useEffect(() => {
    void listProductDrafts()
      .then((list) => setDrafts(list))
      .catch((cause) => setNote(cause instanceof Error ? cause.message : '草稿列表加载失败'));
  }, []);

  const toggleTemplate = (key: MarketingTemplate) => {
    setTemplates((current) =>
      current.includes(key) ? current.filter((item) => item !== key) : [...current, key]
    );
  };

  const run = async () => {
    if (!selected || !templates.length || running) return;
    setRunning(true);
    setNote('');
    const collected: MarketingResult[] = [];
    for (const template of templates) {
      try {
        collected.push(await generateMarketingImage(ctx, selected, template));
      } catch (cause) {
        setNote(cause instanceof Error ? cause.message : `${template} 生成失败`);
      }
    }
    if (collected.length) setResults((current) => [...collected, ...current]);
    setRunning(false);
  };

  return (
    <div className="pp-marketing-page">
      <header className="pp-marketing-head">
        <h1>精选处理</h1>
        <p>AI 出照片层、本地排版合成营销图（英文标题/副文案由排版层渲染，不会拼错）。每张计 1 次生图。</p>
      </header>

      <section className="pp-marketing-controls">
        <div className="pp-marketing-drafts">
          <h2>选择商品</h2>
          {drafts.length ? (
            <ul className="pp-marketing-draft-list">
              {drafts.map((draft) => (
                <li key={draft.id}>
                  <button
                    type="button"
                    className={`pp-marketing-draft${selected === draft.id ? ' is-active' : ''}`}
                    onClick={() => setSelected(draft.id)}
                  >
                    <img src={draft.image_url || ''} alt="" />
                    <span title={draft.title}>{draft.title || `草稿 #${draft.id}`}</span>
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="pp-marketing-empty">暂无草稿：先到「AI 处理」采集或每日选品入池。</p>
          )}
        </div>

        <div className="pp-marketing-side">
          <h2>版式</h2>
          {MARKETING_TEMPLATES.map((item) => (
            <label key={item.key} className="pp-marketing-template" title={item.hint}>
              <input
                type="checkbox"
                checked={templates.includes(item.key)}
                onChange={() => toggleTemplate(item.key)}
              />
              <strong>{item.label}</strong>
              <em>{item.hint}</em>
            </label>
          ))}
          {note ? <p className="pp-marketing-note">{note}</p> : null}
          <button
            type="button"
            className="pp-marketing-run"
            disabled={!selected || !templates.length || running}
            onClick={() => void run()}
          >
            {running ? '生成中…（每张约 30 秒）' : `生成营销图（${templates.length} 张）`}
          </button>
        </div>
      </section>

      {results.length ? (
        <section className="pp-marketing-results">
          <h2>生成结果</h2>
          <ul className="pp-marketing-grid">
            {results.map((result, index) => (
              <li key={`${result.draft_id}-${result.template}-${index}`}>
                <img src={result.image_path} alt={result.headline} />
                <strong>{result.headline.replace(/\n/g, ' ')}</strong>
                <em>{result.subline}</em>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}
