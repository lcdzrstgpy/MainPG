import { useEffect, useState } from "react";

import { showToast } from "../../../shared/components/toastStore";
import { orderProfitApi } from "../../order_profit/api/orderProfitApi";
import { beijingTime } from "../../order_profit/format";
import type { PluginDevice, ProfitShop } from "../../order_profit/types";

/**
 * 插件设置：从「店铺数据 → 数据采集」拆出来的插件配对逻辑。
 * 只负责配对码、已配对设备与设备允许采集的店铺，不参与采集进程。
 */
export function PluginSettingsPanel() {
  const [devices, setDevices] = useState<PluginDevice[]>([]);
  const [shops, setShops] = useState<ProfitShop[]>([]);
  const [pairCode, setPairCode] = useState("");
  const [pairRemain, setPairRemain] = useState(0);
  const [pairBusy, setPairBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [editingDevice, setEditingDevice] = useState("");
  const [draftShops, setDraftShops] = useState<string[]>([]);
  const [deviceBusy, setDeviceBusy] = useState("");

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const [devicePayload, shopPayload] = await Promise.all([
        orderProfitApi.pluginDevices(),
        orderProfitApi.listShops(),
      ]);
      setDevices(devicePayload.devices || []);
      setShops(shopPayload.shops || []);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  /** 配对码倒计时：到 0 后提示重新生成。 */
  useEffect(() => {
    if (!pairCode) return;
    const timer = window.setInterval(() => {
      setPairRemain((value) => Math.max(0, value - 1));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [pairCode]);

  /** 生成一次性配对码，供插件换长期设备凭证。 */
  const createPairCode = async () => {
    setPairBusy(true);
    try {
      const payload = await orderProfitApi.pluginPairCode();
      setPairCode(payload.code);
      const expireAt = Date.parse(payload.expires_at);
      setPairRemain(Number.isFinite(expireAt) ? Math.max(0, Math.round((expireAt - Date.now()) / 1000)) : 600);
      showToast(`配对码 ${payload.code}，请在插件弹窗里输入`, "success");
    } catch (cause) {
      showToast(cause instanceof Error ? cause.message : String(cause), "error");
    } finally {
      setPairBusy(false);
    }
  };

  const saveDeviceShops = async (deviceId: string) => {
    setDeviceBusy(deviceId);
    try {
      const payload = await orderProfitApi.updatePluginDevice(deviceId, draftShops);
      setDevices((current) => current.map((item) => (item.device_id === deviceId ? payload.device : item)));
      setEditingDevice("");
      showToast(draftShops.length ? `已限制为 ${draftShops.length} 家店铺` : "已允许该设备采集全部店铺", "success");
    } catch (cause) {
      showToast(cause instanceof Error ? cause.message : String(cause), "error");
    } finally {
      setDeviceBusy("");
    }
  };

  const revokeDevice = async (device: PluginDevice) => {
    if (!window.confirm(`解绑设备「${device.name || device.device_id}」？解绑后该设备需要重新配对才能采集。`)) return;
    setDeviceBusy(device.device_id);
    try {
      await orderProfitApi.deletePluginDevice(device.device_id);
      setDevices((current) => current.filter((item) => item.device_id !== device.device_id));
      if (editingDevice === device.device_id) setEditingDevice("");
      showToast("设备已解绑", "info");
    } catch (cause) {
      showToast(cause instanceof Error ? cause.message : String(cause), "error");
    } finally {
      setDeviceBusy("");
    }
  };

  return (
    <article className="personal-card plugin-card">
      <div className="personal-card-title">
        <span className="iconfont icon-link" aria-hidden="true" />
        <div>
          <h2>插件设置</h2>
          <small>配对浏览器采集插件，管理设备与允许采集的店铺。</small>
        </div>
        <button type="button" onClick={() => void load()} disabled={loading}>
          {loading ? "刷新中…" : "刷新"}
        </button>
      </div>

      {error ? <p className="plugin-message is-error">{error}</p> : null}

      <div className="plugin-pair">
        <p>在插件弹窗输入配对码即可绑定这台电脑；绑定后不依赖工作台页面常驻。</p>
        <div className="plugin-pair-actions">
          <button type="button" className="plugin-primary" disabled={pairBusy} onClick={() => void createPairCode()}>
            {pairBusy
              ? "生成中…"
              : pairCode
                ? (pairRemain > 0 ? `配对码 ${pairCode}（${pairRemain}s）` : "配对码已过期，重新生成")
                : "生成配对码"}
          </button>
          <span className="plugin-muted">已配对 {devices.length} 台设备</span>
        </div>
      </div>

      <div className="plugin-devices">
        {devices.length ? (
          devices.map((device) => (
            <div className="plugin-device" key={device.device_id}>
              <div className="plugin-device-head">
                <div className="plugin-device-main">
                  <strong>{device.name || device.device_id}</strong>
                  <small>
                    {device.browser || "浏览器"} · v{device.extension_version || "?"} ·{" "}
                    {device.last_seen_at ? `最近在线 ${beijingTime(device.last_seen_at)}` : "从未连接"}
                  </small>
                  <small>允许店铺：{device.allowed_shops.length ? device.allowed_shops.join("、") : "全部店铺"}</small>
                </div>
                <div className="plugin-device-actions">
                  <button
                    type="button"
                    className="plugin-btn"
                    onClick={() => {
                      if (editingDevice === device.device_id) {
                        setEditingDevice("");
                        return;
                      }
                      setEditingDevice(device.device_id);
                      setDraftShops(device.allowed_shops || []);
                    }}
                  >
                    店铺
                  </button>
                  <button
                    type="button"
                    className="plugin-btn"
                    disabled={deviceBusy === device.device_id}
                    onClick={() => void revokeDevice(device)}
                  >
                    解绑
                  </button>
                </div>
              </div>
              {editingDevice === device.device_id ? (
                <div className="plugin-device-shops">
                  <label className="plugin-device-check">
                    <input type="checkbox" checked={!draftShops.length} onChange={() => setDraftShops([])} />
                    全部店铺
                  </label>
                  {shops.map((shop) => (
                    <label className="plugin-device-check" key={shop.id || shop.name}>
                      <input
                        type="checkbox"
                        checked={draftShops.includes(shop.name)}
                        onChange={(event) =>
                          setDraftShops((current) =>
                            event.target.checked
                              ? [...new Set([...current, shop.name])]
                              : current.filter((name) => name !== shop.name),
                          )
                        }
                      />
                      {shop.name}
                    </label>
                  ))}
                  <div className="plugin-device-actions">
                    <button
                      type="button"
                      className="plugin-btn is-primary"
                      disabled={deviceBusy === device.device_id}
                      onClick={() => void saveDeviceShops(device.device_id)}
                    >
                      保存
                    </button>
                    <button type="button" className="plugin-btn" onClick={() => setEditingDevice("")}>
                      取消
                    </button>
                  </div>
                </div>
              ) : null}
            </div>
          ))
        ) : (
          <p className="plugin-muted">{loading ? "正在读取已配对设备…" : "还没有配对设备，生成配对码后在插件里输入即可。"}</p>
        )}
      </div>
    </article>
  );
}
