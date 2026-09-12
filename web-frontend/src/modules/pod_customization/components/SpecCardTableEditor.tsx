import { useState } from "react";

import {
  SPEC_CARD_CELL_MAX_LENGTH,
  SPEC_CARD_COLUMNS,
} from "../data/podCustomizationModel";

type Props = {
  /**
   * 受控表格内容：cells[row][column]，纯文本，原样提交。
   * 结构（行数、表头、第 1 列）由 SKU 预设决定，本组件只让用户编辑数据格。
   */
  cells: string[][];
  onChange: (cells: string[][]) => void;
  disabled?: boolean;
};

/** 每列居中是渲染器行为（2026-09-10 用户规格），编辑态只做提示。 */
export const SPEC_CARD_COLUMN_ALIGN_HINT = "每列居中（渲染时）";
export const SPEC_CARD_HEADER_ROW_HINT = "第 1 行为标题行（四号字），其余行小四";
export const SPEC_CARD_STRUCTURE_HINT = "第 1 行与第 1 列为强制内容，由 SKU 预设自动映射，不可编辑；SKU 增减时行数自动跟随。";
export const SPEC_CARD_CELL_LIMIT_NOTICE = `单个单元格最多 ${SPEC_CARD_CELL_MAX_LENGTH} 个字符，超出部分不会被保存。`;

/** 强制单元格：第 1 行（表头）与第 1 列（SKU 名）只读，其余为可编辑数据格。 */
function isForcedCell(rowIndex: number, columnIndex: number): boolean {
  return rowIndex === 0 || columnIndex === 0;
}

export function SpecCardTableEditor({ cells, onChange, disabled = false }: Props) {
  const [limitNotice, setLimitNotice] = useState("");
  const columns = cells[0]?.length ?? SPEC_CARD_COLUMNS;

  const updateCell = (rowIndex: number, columnIndex: number, value: string) => {
    if (isForcedCell(rowIndex, columnIndex)) return;
    // 超出单格上限直接阻断：不写入 state，也不提交给后端。
    if (value.length > SPEC_CARD_CELL_MAX_LENGTH) {
      setLimitNotice(SPEC_CARD_CELL_LIMIT_NOTICE);
      return;
    }
    setLimitNotice("");
    onChange(cells.map((row, currentRow) => currentRow === rowIndex
      ? row.map((cell, currentColumn) => currentColumn === columnIndex ? value : cell)
      : row));
  };

  return (
    <div className="pod-spec-card-table-editor">
      <div className="pod-spec-card-table-toolbar">
        <span>{cells.length} 行 × {columns} 列</span>
      </div>

      <table className="pod-spec-card-table">
        <tbody>
          {cells.map((row, rowIndex) => (
            <tr key={rowIndex}>
              {row.map((cell, columnIndex) => {
                const forced = isForcedCell(rowIndex, columnIndex);
                const atLimit = cell.length >= SPEC_CARD_CELL_MAX_LENGTH;
                return (
                  <td key={columnIndex} className={forced ? "pod-spec-card-forced-cell" : undefined}>
                    <input
                      value={cell}
                      maxLength={forced ? undefined : SPEC_CARD_CELL_MAX_LENGTH}
                      disabled={disabled || forced}
                      readOnly={forced}
                      aria-label={`第 ${rowIndex + 1} 行第 ${columnIndex + 1} 列`}
                      aria-invalid={atLimit || undefined}
                      onChange={(event) => updateCell(rowIndex, columnIndex, event.currentTarget.value)}
                    />
                    {!forced && <small className={`pod-spec-card-cell-count${atLimit ? " is-limit" : ""}`}>
                      {cell.length}/{SPEC_CARD_CELL_MAX_LENGTH}
                    </small>}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>

      <p className="pod-spec-card-table-hint">{SPEC_CARD_HEADER_ROW_HINT}；{SPEC_CARD_COLUMN_ALIGN_HINT}；单元格内容原样印出。</p>
      <p className="pod-spec-card-table-hint">{SPEC_CARD_STRUCTURE_HINT}</p>
      {limitNotice && <p className="pod-spec-card-table-notice" role="alert">{limitNotice}</p>}
    </div>
  );
}
