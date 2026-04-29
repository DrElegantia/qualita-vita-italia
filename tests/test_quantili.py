"""Test del calcolo percentili da fasce MEF.

Verifica che `quantile_from_brackets` produca risultati consistenti su
distribuzioni note. Eseguire con: python3 -m pytest tests/
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "script"))

# Import il modulo ETL come libreria (no main())
import importlib.util
spec = importlib.util.spec_from_file_location(
    "etl_redditi",
    Path(__file__).resolve().parent.parent / "script" / "02_etl_redditi.py",
)
etl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(etl)


def test_distribuzione_uniforme_singola_fascia():
    """Comune con tutti i contribuenti nella fascia 0-10k:
    mediana cade al centro = 5000, P10 = 1000, P90 = 9000."""
    # ordine BRACKETS_POSITIVE: 0_10k, 10_15k, 15_26k, 26_55k, 55_75k, 75_120k, over_120k
    freqs = [1000, 0, 0, 0, 0, 0, 0]
    assert etl.quantile_from_brackets(freqs, 0.50) == 5000
    assert etl.quantile_from_brackets(freqs, 0.10) == 1000
    assert etl.quantile_from_brackets(freqs, 0.90) == 9000


def test_distribuzione_solo_alta():
    """Comune con tutti nella fascia 26-55k: mediana = (26+55)/2 = 40.5k."""
    freqs = [0, 0, 0, 1000, 0, 0, 0]
    val = etl.quantile_from_brackets(freqs, 0.50)
    assert val == 40500


def test_due_fasce_50_50():
    """500 contribuenti in 0-10k, 500 in 10-15k.
    Mediana cade al confine 10k (target=500, cumulativo prima fascia=500).
    L'algoritmo: arriva alla fine della prima fascia, cum diventa 500 == target,
    quindi va alla seconda iterazione e calcola lo + 0/500 * (15k-10k) = 10000."""
    freqs = [500, 500, 0, 0, 0, 0, 0]
    val = etl.quantile_from_brackets(freqs, 0.50)
    # cum dopo fascia 0_10k = 500, target = 500
    # condizione: cum + f >= target → 0 + 500 >= 500 OK al primo bin
    # in_bin = 500 - 0 = 500, frac = 500/500 = 1, P = 0 + 1*(10000-0) = 10000
    assert val == 10000


def test_zero_freq():
    """Comune vuoto → None."""
    assert etl.quantile_from_brackets([0, 0, 0, 0, 0, 0, 0], 0.50) is None


def test_distribuzione_realistica_italia():
    """Distribuzione approssimativa Italia 2024: ~30% sotto 15k, ~50% in 15-26k.
    La mediana deve cadere nella fascia 15-26k."""
    freqs = [
        2_000_000,  # 0-10k
        4_000_000,  # 10-15k
        15_000_000,  # 15-26k
        12_000_000,  # 26-55k
        3_000_000,  # 55-75k
        2_000_000,  # 75-120k
        500_000,  # over 120k
    ]
    # cum: 2M (0_10k), 6M (10_15k), 21M (15_26k), 33M (26_55k), 36M (55_75k), 38M (75_120k), 38.5M (over)
    # target mediana = 19.25M → cade in 15_26k → ~24.7k
    mediana = etl.quantile_from_brackets(freqs, 0.50)
    assert 15_000 <= mediana <= 26_000, f"mediana fuori range: {mediana}"
    # target P10 = 3.85M → cade in 10_15k → ~12.3k
    p10 = etl.quantile_from_brackets(freqs, 0.10)
    assert 10_000 <= p10 <= 15_000
    # target P90 = 34.65M → cade in 55_75k → ~66k
    p90 = etl.quantile_from_brackets(freqs, 0.90)
    assert 55_000 <= p90 <= 75_000


def test_quantile_monotono():
    """P10 < Q1 < mediana < Q3 < P90 sempre."""
    freqs = [1000, 2000, 5000, 4000, 1000, 500, 100]
    p10 = etl.quantile_from_brackets(freqs, 0.10)
    q1 = etl.quantile_from_brackets(freqs, 0.25)
    median = etl.quantile_from_brackets(freqs, 0.50)
    q3 = etl.quantile_from_brackets(freqs, 0.75)
    p90 = etl.quantile_from_brackets(freqs, 0.90)
    assert p10 < q1 < median < q3 < p90


if __name__ == "__main__":
    # Esecuzione standalone senza pytest
    for name in dir():
        if name.startswith("test_"):
            try:
                globals()[name]()
                print(f"OK {name}")
            except AssertionError as e:
                print(f"FAIL {name}: {e}")
                sys.exit(1)
    print("\nTutti i test passati.")
