"""Encoder vector magnitude must not masquerade as semantic similarity."""
import pytest

from meemee.semantic_memory import cosine


def test_cosine_normalizes_arbitrary_finite_vectors():
    assert cosine([2.0, 0.0], [20.0, 0.0]) == pytest.approx(1.0)


def test_cosine_preserves_direction_ranking_over_magnitude():
    target = [1.0, 0.0]
    assert cosine(target, [1.0, 0.0]) > cosine(target, [100.0, 100.0])


def test_cosine_handles_large_finite_and_zero_vectors():
    assert cosine([1e308, 1e308], [1e308, 1e308]) == pytest.approx(1.0)
    assert cosine([0.0, 0.0], [1.0, 0.0]) == 0.0
