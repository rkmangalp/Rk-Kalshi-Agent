import unittest

from rk_kalshi.fees import quadratic_fee_cents, quadratic_fee_dollars


class QuadraticFeeTests(unittest.TestCase):
    def test_peak_at_fifty_cents_one_contract_rounds_to_two_cents(self):
        # 0.07 * 1 * 0.5 * 0.5 = 0.0175 → ceil to $0.02
        self.assertEqual(quadratic_fee_dollars(0.50, 1), 0.02)
        self.assertEqual(quadratic_fee_cents(0.50, 1), 2.0)

    def test_scales_with_contracts_before_rounding(self):
        # 0.07 * 100 * 0.5 * 0.5 = 1.75
        self.assertEqual(quadratic_fee_dollars(0.50, 100), 1.75)

    def test_tails_are_cheaper_than_coin_flip(self):
        mid = quadratic_fee_dollars(0.50, 1)
        tail = quadratic_fee_dollars(0.10, 1)
        self.assertLess(tail, mid)

    def test_zero_contracts_is_zero(self):
        self.assertEqual(quadratic_fee_dollars(0.50, 0), 0.0)


if __name__ == "__main__":
    unittest.main()
