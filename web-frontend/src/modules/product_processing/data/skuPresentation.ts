import type { DraftVariant } from '../types';

export type VariantAttributePresentation = {
  name: string;
  value: string;
};

export type VariantPresentation = {
  label: string;
  imageUrl: string;
  priceLabel: string;
  attributes: VariantAttributePresentation[];
};

function safeImageUrl(value: unknown): string {
  const raw = String(value ?? '').trim();
  if (!raw) return '';
  try {
    const parsed = new URL(raw);
    return parsed.protocol === 'http:' || parsed.protocol === 'https:' ? parsed.href : '';
  } catch {
    return '';
  }
}

function priceLabel(variant: DraftVariant, fallbackCurrency = ''): string {
  const sourcePrice = typeof variant.source_price === 'number' ? variant.source_price : Number.NaN;
  const cnyPrice = typeof variant.price_cny === 'number' ? variant.price_cny : Number.NaN;
  const hasSourcePrice = Number.isFinite(sourcePrice);
  const amount = hasSourcePrice ? sourcePrice : cnyPrice;
  if (!Number.isFinite(amount)) return '-';
  const currency = hasSourcePrice
    ? String(variant.source_currency || fallbackCurrency || '').toUpperCase()
    : String(fallbackCurrency || 'CNY').toUpperCase();
  const symbols: Record<string, string> = { CNY: '¥', USD: '$', CAD: 'CA$', EUR: '€', GBP: '£', AUD: 'A$' };
  return `${symbols[currency] || `${currency || 'CNY'} `}${amount}`;
}

export function variantPresentation(
  variant: DraftVariant,
  fallbackCurrency = '',
  fallbackImageUrl = '',
): VariantPresentation {
  const attributes = Object.entries(variant.attributes || {})
    .map(([name, value]) => ({ name: String(name).trim(), value: String(value ?? '').trim() }))
    .filter((attribute) => attribute.name && attribute.value);

  return {
    label: attributes.map((attribute) => attribute.value).join('/'),
    imageUrl: safeImageUrl(variant.image_url ?? variant.imageUrl) || safeImageUrl(fallbackImageUrl),
    priceLabel: priceLabel(variant, fallbackCurrency),
    attributes,
  };
}
