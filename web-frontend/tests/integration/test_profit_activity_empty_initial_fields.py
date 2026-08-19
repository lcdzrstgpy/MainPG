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
        extract = self._between(
            "function extractSiteSettings(",
            "function parseActivityThresholds(",
        )
        self.assertIn("settings.activity_threshold_configured !== true", extract)
        self.assertIn('result[key] = ""', extract)

    def test_save_and_restore_handlers_persist_explicit_configuration(self) -> None:
        save = self._between(
            "const saveActivityThreshold =", "const restoreActivityThreshold ="
        )
        restore = self._between(
            "const restoreActivityThreshold =", "const queryProducts ="
        )
        self.assertIn("const activityThresholds = parseActivityThresholds(siteSettings);", self.source)
        self.assertIn("if (!activityThresholds)", save)
        self.assertIn("activity_threshold_configured: true", save)
        self.assertIn("activity_min_net_profit: activityThresholds.minNetProfit", save)
        self.assertIn(
            "activity_profit_rate_threshold: activityThresholds.minProfitRatePercent / 100",
            save,
        )
        self.assertIn("activity_threshold_configured: false", restore)
        self.assertIn("activity_min_net_profit: 0", restore)
        self.assertIn("activity_profit_rate_threshold: 0", restore)

    def test_editing_thresholds_invalidates_saved_configuration(self) -> None:
        self.assertIn("const updateActivityThreshold =", self.source)
        handler = self._between(
            "const updateActivityThreshold =", "const saveActivityThreshold ="
        )
        self.assertIn("setActivityThresholdConfigured(false)", handler)
        self.assertIn(
            'onChange={(event) => updateActivityThreshold("activity_min_net_profit", event.target.value)}',
            self.source,
        )
        self.assertIn(
            'onChange={(event) => updateActivityThreshold("activity_profit_rate_threshold", event.target.value)}',
            self.source,
        )

    def test_filter_and_generate_require_valid_saved_thresholds(self) -> None:
        run_filter = self._between(
            "const runActivityFilter =", "const generateFiltered ="
        )
        generate = self._between("const generateFiltered =", "const pauseFilter =")
        for handler in (run_filter, generate):
            with self.subTest(handler=handler[:40]):
                self.assertIn(
                    "if (!activityThresholds || !activityThresholdConfigured)",
                    handler,
                )
                self.assertIn("请先填写并保存活动最低实际利润和最低利润率。", handler)

    def _between(self, start: str, end: str) -> str:
        return self.source.split(start, 1)[1].split(end, 1)[0]


if __name__ == "__main__":
    unittest.main()
