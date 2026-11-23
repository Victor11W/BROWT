import pytest

from BROWT.research.statistics import bootstrap_mean_ci


def test_bootstrap_is_seeded_and_reports_independent_unit_count():
    result = bootstrap_mean_ci([1, 2, 3, 4, 5], seed=9)
    assert result == bootstrap_mean_ci([1, 2, 3, 4, 5], seed=9)
    assert result.mean == 3 and result.n == 5
    assert result.lower <= result.mean <= result.upper


@pytest.mark.parametrize("values", [[1], [1, float("nan")], [[1, 2], [3, 4]], []])
def test_refuses_insufficient_or_invalid_units(values):
    with pytest.raises(ValueError):
        bootstrap_mean_ci(values)
