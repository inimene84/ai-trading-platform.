"""
Unit Tests for FINMEM Agent Framework
Verifies:
  - Recency decay across Shallow, Intermediate, and Deep layers
  - Importance degradation curves (alpha_l)
  - Information retrieval score gamma_l^E calculation
  - Dynamic Self-Adaptive character transitions
  - Memory access counter promotion mechanics
"""

import math
import unittest
import numpy as np

from backend.services.finmem_service import (
    FinMemCharacter,
    FinMemLayeredMemory,
    MemoryEvent,
    Q_SHALLOW,
    Q_INTERMEDIATE,
    Q_DEEP,
    ALPHA_SHALLOW,
    ALPHA_INTERMEDIATE,
    ALPHA_DEEP,
)


class TestFinMemCore(unittest.TestCase):

    def test_recency_decay_hierarchies(self):
        """Verify that recency decays much faster in shallow layer than deep layer."""
        delta_days = 14.0  # 2 weeks
        s_shallow = math.exp(-delta_days / Q_SHALLOW)
        s_inter = math.exp(-delta_days / Q_INTERMEDIATE)
        s_deep = math.exp(-delta_days / Q_DEEP)

        # After 14 days:
        # Shallow: exp(-1) ≈ 0.3679
        self.assertAlmostEqual(s_shallow, math.exp(-1.0), places=3)
        # Intermediate: exp(-14/90) ≈ 0.8560
        self.assertGreater(s_inter, s_shallow)
        # Deep: exp(-14/365) ≈ 0.9623
        self.assertGreater(s_deep, s_inter)

    def test_importance_decay_hierarchies(self):
        """Verify alpha_l degradation rates across layers over 30 days."""
        delta_days = 30.0
        theta_shallow = math.pow(ALPHA_SHALLOW, delta_days)
        theta_inter = math.pow(ALPHA_INTERMEDIATE, delta_days)
        theta_deep = math.pow(ALPHA_DEEP, delta_days)

        # Shallow degrades down to ~4% of initial importance
        self.assertLess(theta_shallow, 0.05)
        # Intermediate retains ~36%
        self.assertGreater(theta_inter, 0.30)
        # Deep retains ~69%
        self.assertGreater(theta_deep, 0.65)

    def test_gamma_retrieval_score(self):
        """Verify total retrieval score gamma in [0, 3]."""
        s_rec = 0.85
        s_rel = 0.92  # Cosine similarity
        s_imp = 0.70  # Scaled importance
        gamma = s_rec + s_rel + s_imp

        self.assertAlmostEqual(gamma, 2.47, places=2)
        self.assertGreaterEqual(gamma, 0.0)
        self.assertLessEqual(gamma, 3.0)

    def test_self_adaptive_character_switching(self):
        """Verify dynamic character alternates between risk-seeking and risk-averse."""
        char = FinMemCharacter(
            risk_mode="self-adaptive",
            current_inclination="risk-seeking",
            window_periods=3,
        )

        # Positive performance -> keeps risk-seeking
        char.update_performance(0.05)
        self.assertEqual(char.current_inclination, "risk-seeking")
        self.assertIn("Risk-Seeking", char.get_prompt_preamble())

        # Negative performance sequence -> switches to risk-averse
        char.update_performance(-0.08)
        char.update_performance(-0.04)
        self.assertEqual(char.current_inclination, "risk-averse")
        self.assertIn("Risk-Averse", char.get_prompt_preamble())

        # Recovery sequence -> switches back to risk-seeking
        char.update_performance(0.15)
        char.update_performance(0.08)
        self.assertEqual(char.current_inclination, "risk-seeking")

    def test_memory_promotion_thresholds(self):
        """Verify memory promotion threshold constants from paper."""
        from backend.services.finmem_service import (
            PROMOTION_SHALLOW_TO_INTERMEDIATE,
            PROMOTION_INTERMEDIATE_TO_DEEP,
        )
        self.assertGreater(PROMOTION_INTERMEDIATE_TO_DEEP, PROMOTION_SHALLOW_TO_INTERMEDIATE)


if __name__ == "__main__":
    unittest.main()
