import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
API = ROOT / "src/modules/basic_settings/api/systemConfigApi.ts"
PAGE = ROOT / "src/modules/basic_settings/pages/BasicSettingsPage.tsx"
TYPES = ROOT / "src/modules/basic_settings/types/systemConfig.ts"


class BasicSettingsRemoveCosConfigurationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.api = API.read_text(encoding="utf-8")
        cls.page = PAGE.read_text(encoding="utf-8")
        cls.types = TYPES.read_text(encoding="utf-8")

    def test_save_update_payload_does_not_include_cos(self) -> None:
        update = self.api.split(
            "export function createSystemConfigUpdatePayload", 1
        )[1].split("export async function saveBasicSettingsDraft", 1)[0]
        self.assertNotIn("cos:", update)
        self.assertNotIn("cosSecretId", self.api)
        self.assertNotIn("cosSecretKey", self.api)

    def test_settings_page_has_no_cos_configuration_controls(self) -> None:
        for text in (
            "导出图床（腾讯 COS）",
            "cosBucket",
            "cosRegion",
            "cosSecretId",
            "cosSecretKey",
            "publicMediaBaseUrl",
        ):
            with self.subTest(text=text):
                self.assertNotIn(text, self.page)

    def test_settings_page_has_no_configuration_action_buttons(self) -> None:
        self.assertNotIn("保存配置", self.page)
        self.assertNotIn("重新读取", self.page)
        self.assertNotIn("settings-action-buttons", self.page)

    def test_editable_form_types_do_not_expose_cos_fields(self) -> None:
        editable = self.types.split("export type BasicSettingsForm", 1)[1].split(
            "export type BasicSettingsFieldErrors", 1
        )[0]
        for field in (
            "cosBucket",
            "cosRegion",
            "cosSecretId",
            "cosSecretKey",
            "publicMediaBaseUrl",
        ):
            with self.subTest(field=field):
                self.assertNotIn(field, editable)


if __name__ == "__main__":
    unittest.main()
