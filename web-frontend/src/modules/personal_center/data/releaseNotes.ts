/**
 * 「系统版本」面板的兜底更新说明与官网下载地址。
 *
 * 在线更新说明由服务端（`app_update` 接口的 `release_notes`）下发；只有当服务端没有
 * 返回说明时，面板才渲染本文件的兜底内容（见 `SystemVersionPanel` 的 `version-notes-rich` 分支）。
 *
 * `APP_VERSION` 仅用于兜底展示，必须与后端版本号保持一致：
 * `local-runtime/wh_local/config.py` 的 `APP_VERSION`。
 */

export type ReleaseNoteItem = {
  text: string;
  note?: string;
  children?: string[];
};

export type ReleaseNoteSection = {
  title: string;
  items: ReleaseNoteItem[];
};

export type ReleaseNotes = {
  version: string;
  publishedAt?: string;
  summary?: string;
  sections: ReleaseNoteSection[];
};

export const APP_VERSION = "1.5.9";

/** 官网安装包下载地址（与发布脚本 `_publish_release_*.py` 的安装包路径一致）。 */
export const OFFICIAL_DOWNLOAD_URL = `https://workbench.haocoming.top/mainpg/windows/MainPG-Setup-${APP_VERSION}.exe`;

export const RELEASE_NOTES: ReleaseNotes = {
  version: APP_VERSION,
  summary: "在线更新说明由服务端下发；未获取到时显示以下兜底信息。",
  sections: [
    {
      title: "更新方式",
      items: [
        { text: "优先使用本页的在线更新：自动下载并校验安装包，完成后重启生效。" },
        {
          text: "无法在线更新时，可前往官网下载最新安装包手动覆盖安装。",
          note: "账号登录与本地数据不会丢失。",
        },
      ],
    },
  ],
};
