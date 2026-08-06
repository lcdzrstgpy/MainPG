type Props = { title: string };

/** 板块标题旁的感叹号提示：悬停/聚焦时弹窗显示说明文字。 */
export function SectionHelp({ title }: Props) {
  return (
    <span className="price-verification-section-help" tabIndex={0} role="button" aria-label="板块说明">
      <span className="price-verification-section-help-icon">!</span>
      <span className="price-verification-section-help-tip">{title}</span>
    </span>
  );
}
