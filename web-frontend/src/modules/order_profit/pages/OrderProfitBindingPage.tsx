import { useEffect, useState } from "react";

import { showToast } from "../../../shared/components/toastStore";
import { bridgeApi, type BridgeBinding } from "../api/bridgeApi";
import {
  EmptyBlock,
  Metric,
  MetricBand,
  PageHero,
  SectionCard,
} from "../components/ShopDataPrimitives";
import { beijingTime } from "../format";
import "../styles/orderProfit.css";

export function OrderProfitBindingPage({ isActive = true }: { isActive?: boolean }) {
  const [phone, setPhone] = useState("");
  const [password, setPassword] = useState("");
  const [configured, setConfigured] = useState(true);
  const [bindings, setBindings] = useState<BridgeBinding[]>([]);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const [statusPayload, bindingPayload] = await Promise.all([
        bridgeApi.status(),
        bridgeApi.bindings(),
      ]);
      setConfigured(statusPayload.configured);
      setBindings(bindingPayload.accounts || []);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (isActive) void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isActive]);

  const submit = async () => {
    const phoneText = phone.trim();
    if (!phoneText || !password) {
      showToast("请输入 Temu 账号和密码", "error");
      return;
    }
    setSubmitting(true);
    try {
      const result = await bridgeApi.bind(phoneText, password);
      showToast(
        result.already_bound
          ? `店铺「${result.shop_name}」已绑定，无需重复操作`
          : `已绑定店铺「${result.shop_name}」`,
        "success",
      );
      setPassword("");
      await load();
    } catch (cause) {
      showToast(cause instanceof Error ? cause.message : String(cause), "error");
    } finally {
      setSubmitting(false);
    }
  };

  const unbind = async (accountId: string, shopName: string) => {
    if (!window.confirm(`确认解绑「${shopName}」？解绑后将无法同步该店铺数据。`)) return;
    try {
      await bridgeApi.unbind(accountId);
      showToast("已解绑该店铺", "success");
      await load();
    } catch (cause) {
      showToast(cause instanceof Error ? cause.message : String(cause), "error");
    }
  };

  return (
    <div className="op-stage">
      <PageHero
        eyebrow="店铺数据 · 账号绑定"
        title="店铺绑定"
        desc="输入 Temu 店铺的账号密码，与采集服务器上已有的店铺记录核对通过后完成绑定。绑定成功后才能到「链接成本」手动同步数据。"
        art="accounts"
        aside={<span className="op-chip">{bindings.length} 个已绑定</span>}
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

      <MetricBand>
        <Metric label="已绑定店铺" value={bindings.length} glyph="cloud" />
        <Metric label="绑定方式" value="账号密码校验" glyph="key" />
      </MetricBand>

      <SectionCard
        title="校验并绑定"
        desc="账号必须是采集服务器上已登录过的 Temu 店铺；客户端不会保存你的密码。"
        icon="cloud"
      >
        <div className="op-filter-card">
          <div className="op-filter-row">
            <label className="op-field op-field-wide">
              <span>Temu 账号（手机号）</span>
              <input
                type="text"
                value={phone}
                placeholder="输入店铺登录手机号"
                autoComplete="off"
                onChange={(event) => setPhone(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") void submit();
                }}
              />
            </label>
            <label className="op-field op-field-wide">
              <span>密码</span>
              <input
                type="password"
                value={password}
                placeholder="输入 Temu 登录密码"
                autoComplete="off"
                onChange={(event) => setPassword(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") void submit();
                }}
              />
            </label>
            <div className="op-filter-actions">
              <button
                className="op-btn op-btn-primary"
                type="button"
                disabled={submitting}
                onClick={() => void submit()}
              >
                {submitting ? "校验中…" : "校验并绑定"}
              </button>
            </div>
          </div>
          <p className="op-hint">
            绑定成功后，请到侧栏「链接成本」点击「同步」拉取该店铺的商品数据；服务器上没有的账号会提示先在服务器端录入。
          </p>
          {error ? <p className="op-hint is-error">{error}</p> : null}
        </div>
      </SectionCard>

      <SectionCard
        title="已绑定店铺"
        desc="解绑后该店铺数据不再对本客户端可见。"
        icon="link"
        meta={<span className="op-chip">{bindings.length} 个</span>}
      >
        {loading ? null : bindings.length ? (
          <div className="op-table-wrap">
            <table className="op-table">
              <thead>
                <tr>
                  <th>店铺</th>
                  <th>站点</th>
                  <th>账号尾号</th>
                  <th className="op-num">绑定时间（北京）</th>
                  <th className="op-num">操作</th>
                </tr>
              </thead>
              <tbody>
                {bindings.map((binding) => (
                  <tr key={binding.account_id}>
                    <td>
                      <strong>{binding.shop_name || binding.account_id}</strong>
                    </td>
                    <td>{binding.site || "-"}</td>
                    <td>{binding.phone_tail || "-"}</td>
                    <td className="op-num">{beijingTime(binding.bound_at) || "-"}</td>
                    <td className="op-num">
                      <button
                        className="op-btn op-btn-sm"
                        type="button"
                        onClick={() =>
                          void unbind(
                            binding.account_id,
                            binding.shop_name || binding.account_id,
                          )
                        }
                      >
                        解绑
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyBlock
            title="还没有绑定店铺"
            desc="填写上方 Temu 账号密码完成绑定后，这里会显示已绑定店铺。"
            art="empty"
            compact
          />
        )}
      </SectionCard>
    </div>
  );
}
