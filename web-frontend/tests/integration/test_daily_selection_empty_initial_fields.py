import unittest
from pathlib import Path


PAGE = (
    Path(__file__).resolve().parents[2]
    / "src/modules/daily_selection/pages/DailySelectionPage.tsx"
)


class DailySelectionEmptyInitialFieldsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = PAGE.read_text(encoding="utf-8")

    def test_initial_collection_fields_are_empty(self) -> None:
        expected_initializers = [
            'useState<CollectionPlatform | "">("")',
            'useState<SelectionScope | "">("")',
            'useState<TargetSite | "">("")',
            'const [keywords, setKeywords] = useState("")',
            'const [referenceImageUrl, setReferenceImageUrl] = useState("")',
            'const [minPrice, setMinPrice] = useState("")',
            'const [maxPrice, setMaxPrice] = useState("")',
            'const [minMoq, setMinMoq] = useState("")',
            'const [minSkuCount, setMinSkuCount] = useState("")',
            'const [maxSkuCount, setMaxSkuCount] = useState("")',
            'const [minSkuPrice, setMinSkuPrice] = useState("")',
            'const [maxSkuPrice, setMaxSkuPrice] = useState("")',
            'const [minSkuStock, setMinSkuStock] = useState("")',
            'const [maxSkuStock, setMaxSkuStock] = useState("")',
            'const [targetCount, setTargetCount] = useState("")',
            'const [excludeRisks, setExcludeRisks] = useState(false)',
            'const [maxParallelCollect, setMaxParallelCollect] = useState(8)',
        ]
        for initializer in expected_initializers:
            with self.subTest(initializer=initializer):
                self.assertIn(initializer, self.source)
        self.assertGreaterEqual(
            self.source.count('<option value="" disabled>请选择</option>'),
            3,
        )

    def test_selecting_a_direction_restores_template_collection_values(self) -> None:
        choose_direction = self.source.split(
            "function chooseDirection(direction: Direction) {", 1
        )[1].split("\n  }", 1)[0]
        expected_updates = [
            'setPlatform("1688")',
            'setScope("divergent")',
            'setMinMoq("2")',
            'setExcludeRisks(true)',
            'setMaxParallelCollect(8)',
            'setKeywords(direction.keywords.join("，"))',
            'setSite(direction.site ?? "US")',
            'setMinPrice(String(direction.price[0]))',
            'setMaxPrice(String(direction.price[1]))',
            'setTargetCount(String(direction.target))',
        ]
        for update in expected_updates:
            with self.subTest(update=update):
                self.assertIn(update, choose_direction)

    def test_switching_collection_mode_does_not_refill_fields(self) -> None:
        mode_tabs = self.source.split(
            '<div className="mode-tabs"', 1
        )[1].split(
            '<div className={`collection-primary-fields', 1
        )[0]
        self.assertIn('onClick={() => setMode(value)}', mode_tabs)
        for setter in (
            "setPlatform(",
            "setSite(",
            "setScope(",
            "setTargetCount(",
            "setKeywords(",
            "setReferenceImageUrl(",
        ):
            with self.subTest(setter=setter):
                self.assertNotIn(setter, mode_tabs)


if __name__ == "__main__":
    unittest.main()
