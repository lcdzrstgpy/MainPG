import { useState } from "react";

import {
  SPEC_CARD_CELL_MAX_LENGTH,
  SPEC_CARD_MAX_COLUMNS,
  SPEC_CARD_MAX_ROWS,
  SPEC_CARD_MIN_COLUMNS,
  SPEC_CARD_MIN_ROWS,
} from "../data/podCustomizationModel";

type Props = {
  /** 受控表格内容：cells[row][column]，纯文本，原样提交。 */
  cells: string[][];
  onChange: (cells: string[][]) => void;
  disabled?: boolean;
};

/** 每列居中是渲染器行为（2026-09-10 用户规格），编辑态只做提示。 */
export const SPEC_CARD_COLUMN_ALIGN_HINT = "每列居中（渲染时）";
export const SPEC_CARD_HEADER_ROW_HINT = "第 1 行为标题行（四号字），其余行小四";
export const SPEC_CARD_CELL_LIMIT_NOTICE = `单个单元格最多 ${SPEC_CARD_CELL_MAX_LENGTH} 个字符，超出部分不会被保存。`;

export function SpecCardTableEditor({ cells, onChange, disabled = false }: Props) {
  const [limitNotice, setLimitNotice] = useState("");
  const columns = cells[0]?.length ?? 1;

  const replaceCells = (next: string[][]) => {
    setLimitNotice("");
    onChange(next);
  };

  const updateCell = (rowIndex: number, columnIndex: number, value: string) => {
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

  const addRow = () => {
    if (cells.length >= SPEC_CARD_MAX_ROWS) return;
    replaceCells([...cells, Array.from({ length: columns }, () => "")]);
  };

  const removeRow = (rowIndex: number) => {
    if (cells.length <= SPEC_CARD_MIN_ROWS) return;
    replaceCells(cells.filter((_, currentRow) => currentRow !== rowIndex));
  };

  const addColumn = () => {
    if (columns >= SPEC_CARD_MAX_COLUMNS) return;
    replaceCells(cells.map((row) => [...row, ""]));
  };

  const removeColumn = (columnIndex: number) => {
    if (columns <= SPEC_CARD_MIN_COLUMNS) return;
    replaceCells(cells.map((row) => row.filter((_, currentColumn) => currentColumn !== columnIndex)));
  };

  return (
    <div className="pod-spec-card-table-editor">
      <div className="pod-spec-card-table-toolbar">
        <span>{cells.length} 行 × {columns} 列</span>
        <button type="button" onClick={addRow} disabled={disabled || cells.length >= SPEC_CARD_MAX_ROWS}>＋ 添加行</button>
        <button type="button" onClick={addColumn} disabled={disabled || columns >= SPEC_CARD_MAX_COLUMNS}>＋ 添加列</button>
      </div>

      {/* 表格本体：列操作放在首行（与列对齐），行操作贴在最右侧，避免按钮散落在表格中间。 */}
      <table className="pod-spec-card-table">
        <thead>
          <tr className="pod-spec-card-column-actions">
            {Array.from({ length: columns }, (_, columnIndex) => (
              <th key={columnIndex} scope="col">
                <button
                  type="button"
                  onClick={() => removeColumn(columnIndex)}
                  disabled={disabled || columns <= SPEC_CARD_MIN_COLUMNS}
                  aria-label={`删除第 ${columnIndex + 1} 列`}
                >✕ 列 {columnIndex + 1}</button>
              </th>
            ))}
            <th className="pod-spec-card-row-action-head" aria-hidden="true" />
          </tr>
        </thead>
        <tbody>
          {cells.map((row, rowIndex) => (
            <tr key={rowIndex}>
              {row.map((cell, columnIndex) => {
                const atLimit = cell.length >= SPEC_CARD_CELL_MAX_LENGTH;
                return (
                  <td key={columnIndex}>
                    <input
                      value={cell}
                      maxLength={SPEC_CARD_CELL_MAX_LENGTH}
                      disabled={disabled}
                      aria-label={`第 ${rowIndex + 1} 行第 ${columnIndex + 1} 列`}
                      aria-invalid={atLimit || undefined}
                      onChange={(event) => updateCell(rowIndex, columnIndex, event.currentTarget.value)}
                    />
                    <small className={`pod-spec-card-cell-count${atLimit ? " is-limit" : ""}`}>
                      {cell.length}/{SPEC_CARD_CELL_MAX_LENGTH}
                    </small>
                  </td>
                );
              })}
              <td className="pod-spec-card-row-action">
                <button
                  type="button"
                  onClick={() => removeRow(rowIndex)}
                  disabled={disabled || cells.length <= SPEC_CARD_MIN_ROWS}
                  aria-label={`删除第 ${rowIndex + 1} 行`}
                  title={`删除第 ${rowIndex + 1} 行`}
                >✕</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <p className="pod-spec-card-table-hint">{SPEC_CARD_HEADER_ROW_HINT}；{SPEC_CARD_COLUMN_ALIGN_HINT}；单元格内容原样印出。</p>
      <p className="pod-spec-card-table-hint">{SPEC_CARD_MIN_ROWS}–{SPEC_CARD_MAX_ROWS} 行、{SPEC_CARD_MIN_COLUMNS}–{SPEC_CARD_MAX_COLUMNS} 列；空行不会印出。</p>
      {limitNotice && <p className="pod-spec-card-table-notice" role="alert">{limitNotice}</p>}
    </div>
  );
}
