import unittest
from pathlib import Path


PAGE = (
    Path(__file__).resolve().parents[2]
    / "src/modules/profit_activity/pages/ProfitActivityTestPage.tsx"
)


class ProfitActivityEmptyInitialFieldsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = PAGE.read_text(encoding="utf-8")

    def test_single_product_fields_start_empty(self) -> None:
        empty_product = self.source.split(
            "const emptyProduct: ProductForm = {", 1
        )[1].split("\n};", 1)[0]
        for field in ("skc", "selling_price", "cost_price", "weight_kg"):
            with self.subTest(field=field):
                self.assertIn(f'{field}: ""', empty_product)
        self.assertNotIn('selling_price: "19.99"', empty_product)
        self.assertNotIn('cost_price: "5.00"', empty_product)
        self.assertNotIn('weight_kg: "0.35"', empty_product)

    def test_threshold_display_depends_on_explicit_configuration(self) -> None:
        extract = self.source.split(
            "function extractSiteSettings(", 1
        )[1].split("\n}\n\nfunction numericProductPayload", 1)[0]
        self.assertIn("settings.activity_threshold_configured !== true", extract)
        self.assertIn('result[key] = ""', extract)

    def test_save_restore_and_filter_paths_use_threshold_state(self) -> None:
        self.assertIn("function parseActivityThresholds", self.source)
        self.assertIn("activity_threshold_configured: true", self.source)
        self.assertIn("activity_threshold_configured: false", self.source)
        self.assertGreaterEqual(
            self.source.count("if (!activityThresholds)"),
            3,
        )


if __name__ == "__main__":
    unittest.main()
