import { useEffect, useMemo, useRef, useState } from "react";

import { TemuPropertyForm } from "./TemuPropertyForm";
import { temuPublishApi } from "../api/temuPublishApi";
import {
  missingRequiredNames,
  visibleProperties,
  type AttrPickMap,
} from "../data/attributeModel";
import { saveAttrPicksHistory, storageScope } from "../data/attrPicksStore";
import type { ProductTemplate } from "../data/localStore";
import type {
  TemuAttributes,
  TemuCategory,
  TemuCategoryPath,
  TemuFreightTemplate,
  TemuParentSpec,
  TemuWarehouseGroup,
} from "../types";
import { categoryPathFromDto, categoryPathLabel } from "../utils/categoryPath";

const SITE_ID = 100;

type Props = {
  /** 配置完整时回传一份可直接用于批量发布的产品模板；不完整时回传 null */
  onChange: (template: ProductTemplate | null) => void;
  /**
   * 预填类目：整批草稿都被自动匹配到同一个叶子类目时传进来，
   * 用户只需要填属性 + 选运费/仓库，不用再搜一次。
   */
  initialPath?: TemuCategory[];
  /**
   * 草稿库入池时按类目估好的属性：整批同一个叶子类目时传进来直接铺上，
   * 用户只需核对 + 选运费模板/仓库，不用再手点一遍下拉。
   */
  initialPicks?: AttrPickMap;
};

function firstWarehouse(groups: TemuWarehouseGroup[]): string {
  for (const group of groups) {
    const hit = (group.validWarehouseList ?? []).find((item) => !item.warehouseDisable);
    if (hit) return hit.warehouseId;
  }
  return "";
}

/** 只要货架上的第一个可用仓库（批量发布只需要一个默认仓）。 */
function flatWarehouses(groups: TemuWarehouseGroup[]): Array<{ id: string; name: string }> {
  return groups.flatMap((group) =>
    (group.validWarehouseList ?? [])
      .filter((item) => !item.warehouseDisable)
      .map((item) => ({ id: item.warehouseId, name: item.warehouseName || item.warehouseId })),
  );
}

/**
 * 批量发布里的「现场配置」。
 *
 * 产品模板本质是「类目 + 该类目的属性值 + 运费模板 + 仓库」的快照。以前没有模板
 * 就没法批量发布，用户得先跑去发布页手动填一遍再存模板。这里把同样的事做进弹窗：
 * 搜类目 → 拉全量属性（必填+选填，含联动）→ 选运费模板和仓库 → 直接发。
 *
 * 填完整就回传一份临时 ProductTemplate（不落盘，除非用户点「存成模板」）。
 */
export function BatchManualConfig({ onChange, initialPath, initialPicks }: Props) {
  const [specs, setSpecs] = useState<TemuParentSpec[]>([]);
  const [freights, setFreights] = useState<TemuFreightTemplate[]>([]);
  const [warehouseGroups, setWarehouseGroups] = useState<TemuWarehouseGroup[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [query, setQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [paths, setPaths] = useState<TemuCategoryPath[]>([]);
  const [path, setPath] = useState<TemuCategory[]>([]);
  const [attributes, setAttributes] = useState<TemuAttributes | null>(null);
  const [picks, setPicks] = useState<AttrPickMap>({});
  /**
   * 估好的属性只在「预填那一次」铺：之后用户手动换类目时 pid 全变了，
   * 再铺就是往新类目里塞一堆不存在的 pid。
   */
  const seededPicksRef = useRef<AttrPickMap | undefined>(initialPicks);
  /** 当前选中的叶子类目：用户改属性时按它写「同类目历史」 */
  const catIdRef = useRef(0);
  const [freightId, setFreightId] = useState("");
  const [warehouseId, setWarehouseId] = useState("");
  const [shipmentDays, setShipmentDays] = useState("7");

  useEffect(() => {
    let alive = true;
    void Promise.all([
      temuPublishApi.parentSpecs().catch(() => null),
      temuPublishApi.freightTemplates([SITE_ID]).catch(() => null),
      temuPublishApi.warehouses([SITE_ID]).catch(() => null),
    ]).then(([specsR, freightsR, whR]) => {
      if (!alive) return;
      setSpecs(specsR?.parentSpecDTOS ?? []);
      const list = freightsR?.freightTemplates ?? [];
      setFreights(list);
      setFreightId((current) => current || list[0]?.freightTemplateId || "");
      const groups = whR?.warehouseDTOList ?? [];
      setWarehouseGroups(groups);
      setWarehouseId((current) => current || firstWarehouse(groups));
      const missed = [specsR, freightsR, whR].filter((item) => !item).length;
      if (missed) setError(`有 ${missed} 项基础数据没取到（父规格/运费模板/仓库），可以重开弹窗再试`);
      setLoading(false);
    });
    return () => {
      alive = false;
    };
  }, []);

  const search = async () => {
    const text = query.trim();
    if (!text) return;
    setSearching(true);
    setError("");
    try {
      const result = await temuPublishApi.matchCategory(text, SITE_ID);
      setPaths(result.categoryPathDTOS ?? []);
      if (!result.categoryPathDTOS?.length) setError("没匹配到类目：换个更通用的关键词试试");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "类目匹配失败");
    } finally {
      setSearching(false);
    }
  };

  /** 选中一条候选路径：只需要叶子类目，但路径留着展示。 */
  const pickPath = async (dto: TemuCategoryPath) => {
    const next = categoryPathFromDto(dto);
    if (!next.length) return;
    // 「估好的属性」只消费一次：先取走再请求。放在成功后取的话，这次请求失败
    // 会把旧类目的值留到下一次手动换类目时误铺。
    const seeded = seededPicksRef.current;
    seededPicksRef.current = undefined;
    setPath(next);
    setPaths([]);
    setError("");
    setPicks({});
    try {
      const leaf = next[next.length - 1];
      const loaded = await temuPublishApi.attributes(leaf.catId);
      setAttributes(loaded);
      catIdRef.current = Number(leaf.catId) || 0;
      // 草稿库估好的属性：铺上，用户仍可逐项改
      if (seeded && Object.keys(seeded).length) setPicks(seeded);
    } catch (cause) {
      setAttributes(null);
      setError(cause instanceof Error ? cause.message : "属性表没取到");
    }
  };

  /**
   * 用户改属性：写「同类目历史」。
   *
   * 批量发布是主场景 —— 用户在这儿把 8 项改对，下次同类目估填就该按这版来；
   * 不写的话他每次批量发都得重改一遍。
   * 程序铺的值（seeded）不走这里，所以不会污染历史。
   */
  const handlePicksChange = (next: AttrPickMap) => {
    setPicks(next);
    if (catIdRef.current) saveAttrPicksHistory(storageScope(), catIdRef.current, next);
  };

  // 整批草稿自动匹配到同一类目时直接预填，省一次搜索（用户仍可改）
  const prefilledRef = useRef(false);
  useEffect(() => {
    if (prefilledRef.current || !initialPath?.length) return;
    prefilledRef.current = true;
    void pickPath({ cat1DTO: initialPath[0], cat2DTO: initialPath[1], cat3DTO: initialPath[2], cat4DTO: initialPath[3], cat5DTO: initialPath[4] } as TemuCategoryPath);
    // pickPath 在下面定义，闭包引用没问题（effect 在渲染后执行）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialPath]);

  const warehouses = useMemo(() => flatWarehouses(warehouseGroups), [warehouseGroups]);
  const missing = attributes ? missingRequiredNames(visibleProperties(attributes, picks), picks) : [];

  // 配置齐了就回传模板；不齐回传 null（父组件据此判断能不能发）
  useEffect(() => {
    if (!path.length || !freightId || !warehouseId) {
      onChange(null);
      return;
    }
    // 属性只要用户填过的（空值不带，否则会被判「属性不合法」）
    const attrs: Record<string, { vid: number; text: string }> = {};
    Object.entries(picks).forEach(([pid, pick]) => {
      if (pick && (pick.vid > 0 || pick.text.trim())) attrs[pid] = { vid: pick.vid, text: pick.text };
    });
    const colorSpec = specs.find((item) => item.parentSpecName.includes("颜色")) ?? specs[0];
    const sizeSpec = specs.find((item) => item.parentSpecName.includes("尺码")) ?? specs[1];
    onChange({
      name: "__batch_manual__",
      categoryPath: path,
      attrs,
      freightId,
      warehouseId,
      shipmentDays,
      origin: "CN",
      sizeTemplates: "",
      isCustom: false,
      isSensitive: false,
      colorParentId: colorSpec?.parentSpecId,
      colorParentName: colorSpec?.parentSpecName,
      sizeParentId: sizeSpec?.parentSpecId,
      sizeParentName: sizeSpec?.parentSpecName,
      savedAt: new Date().toLocaleString("zh-CN"),
    });
  }, [path, picks, freightId, warehouseId, shipmentDays, specs, onChange]);

  return (
    <div className="temu-batch-manual">
      <div className="temu-batch-manual-head">
        <strong>或：现场配置一个类目（不用产品模板）</strong>
        <em className="temu-muted">整批共用这一套类目和属性，填完就按它发；以后想复用可存成产品模板</em>
      </div>

      {loading ? <p className="temu-muted">正在取父规格 / 运费模板 / 仓库…</p> : null}

      <div className="temu-batch-search">
        <input
          className="temu-input"
          value={query}
          placeholder="搜 Temu 类目关键词，如 cosmetic bag / 化妆包"
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              void search();
            }
          }}
        />
        <button type="button" className="temu-btn" onClick={() => void search()} disabled={searching}>
          {searching ? "搜索中…" : "搜索类目"}
        </button>
      </div>

      {paths.length > 0 && (
        <ul className="temu-batch-paths">
          {paths.slice(0, 8).map((dto, index) => {
            const item = categoryPathFromDto(dto);
            return (
              <li key={`${index}-${item.map((step) => step.catId).join("-")}`}>
                <button type="button" onClick={() => void pickPath(dto)}>
                  {categoryPathLabel(item)}
                </button>
              </li>
            );
          })}
        </ul>
      )}

      {path.length > 0 && (
        <p className="temu-batch-picked">已选类目：{categoryPathLabel(path)}</p>
      )}

      {path.length > 0 && (
        <div className="temu-batch-attrs">
          <TemuPropertyForm
            attributes={attributes}
            picks={picks}
            onChange={handlePicksChange}
            emptyHint="正在取这个类目的属性…"
            optionalHint="不填也能发布"
          />
          {missing.length > 0 && (
            <p className="temu-confirm-warn">
              还差 {missing.length} 个必填属性：{missing.slice(0, 6).join("、")}
              {missing.length > 6 ? "…" : ""}（没填完提交会被 Temu 拒）
            </p>
          )}
        </div>
      )}

      <div className="temu-batch-manual-row">
        <label className="temu-field">
          <span>运费模板</span>
          <select
            className="temu-input"
            value={freightId}
            onChange={(event) => setFreightId(event.target.value)}
          >
            <option value="">{freights.length ? "请选择" : "这个店铺还没有运费模板"}</option>
            {freights.map((item) => (
              <option key={item.freightTemplateId} value={item.freightTemplateId}>
                {item.templateName || item.freightTemplateId}
              </option>
            ))}
          </select>
        </label>
        <label className="temu-field">
          <span>发货仓库</span>
          <select
            className="temu-input"
            value={warehouseId}
            onChange={(event) => setWarehouseId(event.target.value)}
          >
            <option value="">{warehouses.length ? "请选择" : "这个店铺还没有可用仓库"}</option>
            {warehouses.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
        <label className="temu-field">
          <span>发货时效（天）</span>
          <input
            className="temu-input"
            value={shipmentDays}
            onChange={(event) => setShipmentDays(event.target.value)}
          />
        </label>
      </div>

      {error ? <p className="temu-confirm-warn">{error}</p> : null}
    </div>
  );
}

