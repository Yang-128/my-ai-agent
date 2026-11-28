import unittest

from calculations import calculate
from tool_loading import select_initial_tools


class CalculationTests(unittest.TestCase):
    def test_decimal_sum_and_conversion(self):
        self.assertEqual(calculate("sum", ["0.1", "0.2"])["value"], "0.3")
        self.assertEqual(calculate("convert", ["1"], "mi", "m")["value"], "1609.344")

    def test_invalid_operations(self):
        for args in [("percentage", ["1", "0"]), ("sum", ["NaN"]), ("convert", ["1"], "mi", "kg")]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                calculate(*args)

    def test_calculation_loads_its_tool(self):
        registry = [{"type": "function", "function": {"name": "mcp_calculate_decimal"}}]
        selected, _ = select_initial_tools("Calculate 0.1 plus 0.2", registry)
        self.assertIn(registry[0], selected)

    def test_grouped_totals_skip_blanks_and_preserve_first_seen_order(self):
        from calculations import summarize_rows

        self.assertEqual(summarize_rows([
            {"group": "B", "amount": "0.1"},
            {"group": "A", "amount": None},
            {"group": "A", "amount": "2"},
            {"group": "B", "amount": "0.2"},
        ]), [{"group": "B", "total": "0.3"}, {"group": "A", "total": "2"}])

    def test_mcp_returns_decimal_result(self):
        from mcp_client import call_mcp_tool

        result = call_mcp_tool("calculate_decimal", {"operation": "sum", "values": ["0.1", "0.2"]})
        self.assertIn("0.3", str(result))
