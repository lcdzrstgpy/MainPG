import { useEffect, useState } from "react";

import { bridgeApi, type BridgeClaimShop } from "../api/bridgeApi";
import {
  EmptyBlock,
  Metric,
  MetricBand,
  PageHero,
  SectionCard,
} from "../components/ShopDataPrimitives";
import { beijingTime, numberText } from "../format";
import "../styles/orderProfit.css";

export function OrderProfitClaimsPage({ isActive = true }: { isActive?: boolean }) {
  const [configured, setConfigured] = useState(true);
  const [clientId, setClientId] = useState("");
  const [shops, setShops] = useState<BridgeClaimShop[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const [statusPayload, claimsPayload] = await Promise.all([
        bridgeApi.status(),
        bridgeApi.claims(),
      ]);
      setConfigured(statusPayload.configured);
      setClientId(statusPayload.client_id);
      setShops(claimsPayload.accounts || []);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!isActive) return;
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isActive]);

  const claimCount = shops.reduce((sum, shop) => sum + shop.claims.length, 0);
  const localClaims = shops.filter((shop) =>
    shop.claims.some((claim) => claim.client_id === clientId),
  ).length;

  return (
    <div className="op-stage">
      <PageHero
        eyebrow="店铺数据 · 认领审计"
        title="认领记录"
        desc="每个店铺被哪些工作台账号认领过、什么时候认领，都在这里留档；解绑不会删除历史记录之外的绑定关系。"
        art="accounts"
        aside={
          <>
            <span className="op-chip">{numberText(shops.length)} 个店铺</span>
            <span className="op-chip">{numberText(claimCount)} 条认领</span>
          </>
        }
      />

      {!configured ? (
        <SectionCard title="桥接未配置" desc="本地没有配置采集服务器地址或共享密钥。" icon="warning">
          <EmptyBlock
            title="无法连接采集服务器"
            desc="请在本地 runtime 配置 WH_LOCAL_BRIDGE_KEY 后重启工作台。"
            art="empty"
            compact
          />
        </SectionCard>
      ) : null}

      {error ? (
        <SectionCard title="加载失败" desc={error} icon="warning">
          <EmptyBlock
            title="认领记录暂时取不到"
            desc="检查采集服务是否在线，或稍后点「刷新」重试。"
            art="empty"
            compact
          />
        </SectionCard>
      ) : null}

      <MetricBand>
        <Metric label="有认领的店铺" value={numberText(shops.length)} glyph="cloud" />
        <Metric label="认领记录" value={numberText(claimCount)} glyph="link" />
        <Metric label="本机认领店铺" value={numberText(localClaims)} glyph="key" />
        <Metric label="本机标识" value={<span className="op-mono">{clientId || "-"}</span>} glyph="key" />
      </MetricBand>

      {shops.length ? (
        shops.map((shop) => (
          <SectionCard
            key={shop.account_id}
            title={shop.shop_name || shop.account_id}
            desc={`认领账号尾号 ${shop.phone_tail || "-"} · 站点 ${shop.site || "-"}`}
            icon="cloud"
            meta={<span className="op-chip">{shop.claims.length} 个账号认领</span>}
            action={
              <span className="op-chip">{shop.account_id}</span>
            }
          >
            <div className="op-table-wrap">
              <table className="op-table">
                <thead>
                  <tr>
                    <th>认领账号</th>
                    <th>设备标识</th>
                    <th className="op-num">认领时间</th>
                  </tr>
                </thead>
                <tbody>
                  {shop.claims.map((claim) => (
                    <tr key={`${shop.account_id}|${claim.client_id}`}>
                      <td>
                        <strong>{claim.label || claim.client_id}</strong>
                        {claim.client_id === clientId ? (
                          <span className="op-chip is-ok" style={{ marginLeft: 8 }}>
                            本机
                          </span>
                        ) : null}
                      </td>
                      <td>
                        <span className="op-mono">{claim.client_id}</span>
                      </td>
                      <td className="op-num">{beijingTime(claim.bound_at) || "-"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </SectionCard>
        ))
      ) : (
        <SectionCard title="还没有认领记录" desc="任何账号完成店铺绑定后都会在这里留档。" icon="link" scene="result">
          <EmptyBlock
            title="暂无认领"
            desc="先到「店铺绑定」输入 Temu 账号密码完成一次绑定。"
            art="empty"
            compact
          />
        </SectionCard>
      )}

      <SectionCard
        title="记录说明"
        desc="认领记录只读，绑定与解绑都在「店铺绑定」里操作。"
        icon="warning"
        scene="team"
        action={
          <button className="op-btn op-btn-sm" type="button" disabled={loading} onClick={() => void load()}>
            {loading ? "刷新中…" : "刷新"}
          </button>
        }
      >
        <div className="op-hint">
          每条记录包含：哪台设备的哪个账号（用户名@计算机名）、认领了哪个店铺、认领时间（北京时间）。
          换机或重装后设备标识会变化，旧记录仍会保留，便于追溯数据来源。
        </div>
      </SectionCard>
    </div>
  );
}
