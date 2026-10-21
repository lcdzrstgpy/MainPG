import { useState } from 'react';

import {
  COMPOSITION_TEMPLATES,
  type CompositionTemplateId,
} from '../data/compositionTemplates';
import {
  renderBatchComposition,
  type BatchSpecRow,
} from '../data/batchComposition';
import './BatchCompositionDialog.css';

export type BatchCompositionItem = {
  draftId: number;
  headline: string;
  subtitle: string;
  productImageUrl: string;
  specRows: BatchSpecRow[];
};

type Props = {
  open: boolean;
  items: BatchCompositionItem[];
  onClose: () => void;
  onUpload: (draftId: number, file: File) => Promise<void> | void;
};

export function BatchCompositionDialog({
  open,
  items,
  onClose,
  onUpload,
}: Props) {
  const [templateId, setTemplateId] = useState<CompositionTemplateId>('headline');
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(0);
  const [message, setMessage] = useState('');

  if (!open) return null;

  const run = async () => {
    if (!items.length || busy) return;
    setBusy(true);
    setProgress(0);
    setMessage('');
    let success = 0;
    const failures: string[] = [];
    for (let index = 0; index < items.length; index += 1) {
      const item = items[index];
      setProgress(index + 1);
      try {
        if (!item.productImageUrl) throw new Error('缺少产品主图');
        const blob = await renderBatchComposition({
          templateId,
          productImageUrl: item.productImageUrl,
          headline: item.headline,
          subtitle: item.subtitle,
          specRows: item.specRows,
        });
        const file = new File(
          [blob],
          `composition-${item.draftId}-${Date.now()}.png`,
          { type: 'image/png' },
        );
        await onUpload(item.draftId, file);
        success += 1;
      } catch (error) {
        failures.push(
          `${item.headline || `#${item.draftId}`}: ${
            error instanceof Error ? error.message : '拼装失败'
          }`,
        );
      }
    }
    setBusy(false);
    setMessage(
      failures.length
        ? `已完成 ${success} 个，失败 ${failures.length} 个。${failures.slice(0, 3).join('；')}`
        : `已完成 ${success} 个商品的自动拼装，并加入详情图。`,
    );
  };

  return (
    <div className="batch-compose-overlay" role="dialog" aria-modal="true" aria-label="批量本地拼装">
      <div className="batch-compose-modal">
        <header className="batch-compose-head">
          <div>
            <h2>批量本地拼装</h2>
            <p>按模板批量生成产品详情图，不调用 AI；生成后自动加入详情图。</p>
          </div>
          <button
            type="button"
            className="batch-compose-close"
            onClick={onClose}
            disabled={busy}
            aria-label="关闭"
          >
            ×
          </button>
        </header>

        <div className="batch-compose-templates">
          {COMPOSITION_TEMPLATES.map((template) => (
            <button
              key={template.id}
              type="button"
              className={`batch-compose-template${templateId === template.id ? ' is-active' : ''}`}
              onClick={() => setTemplateId(template.id)}
              disabled={busy}
            >
              <strong>{template.label}</strong>
              <small>{template.description}</small>
            </button>
          ))}
        </div>

        <div className="batch-compose-summary">
          <span>待拼装商品</span>
          <strong>{items.length} 个</strong>
          <em>使用每个商品的主图、短标题和尺寸/重量/SKU 自动排版。</em>
        </div>

        {busy && (
          <div className="batch-compose-progress">
            <div>
              <span>拼装中 {progress} / {items.length}</span>
              <span>{Math.round((progress / Math.max(1, items.length)) * 100)}%</span>
            </div>
            <progress value={progress} max={Math.max(1, items.length)} />
          </div>
        )}

        {message && <p className="batch-compose-message">{message}</p>}

        <div className="batch-compose-actions">
          <button type="button" onClick={onClose} disabled={busy}>
            取消
          </button>
          <button
            type="button"
            className="primary"
            onClick={() => void run()}
            disabled={busy || items.length === 0}
          >
            {busy ? '拼装中…' : `开始拼装 ${items.length} 个`}
          </button>
        </div>
      </div>
    </div>
  );
}

export default BatchCompositionDialog;
