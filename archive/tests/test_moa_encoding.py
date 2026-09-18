"""Engine tests: the time-respecting cutoff, the own-outcome mask (R7), the mandatory
leakage inflation, and dt/ta restriction+degradation. History is always built over the
FULL unit set (every index unit is a member of its own class groups), which is what makes
the leave-one-out subtraction correct."""
import math

from trial_pos.services import moa_encoding as M
from trial_pos.services.moa_encoding import UnitRow, History, encode, SAFE, LEAK_FUTURE, LEAK_OWN


def test_smoothed_logit_prior_only():
    # Derived from the engine's own PRIOR_A/PRIOR_B rather than retyping 1/3 and 2.7:
    # if the prior is ever changed, this test follows it instead of failing on a
    # transcribed constant. The structural claim (prior mean < 0.5, so logit < 0) is
    # what actually matters and is asserted separately.
    v = M.smoothed_logit(0, 0)
    rate = M.PRIOR_A / (M.PRIOR_A + M.PRIOR_B)
    assert v == math.log(rate) - math.log(1 - rate)
    assert rate < 0.5 and v < 0


def test_cutoff_mask_and_leak_counts_exact():
    # class A history: (year,label) = (1,0)(1,1)(2,0)(3,1); index = the year-2 unit, label 0
    units = [
        UnitRow("e0", 1, 0, frozenset({"A"})),
        UnitRow("e1", 1, 1, frozenset({"A"})),
        UnitRow("ix", 2, 0, frozenset({"A"})),
        UnitRow("f3", 3, 1, frozenset({"A"})),
    ]
    h = History(units)
    ix = units[2]
    safe = encode(ix, h, gran="any", mode=SAFE)
    # year<=2 -> members (1,0)(1,1)(2,0)=3, ap=1; minus own(1 count, label 0) -> cnt2 ap1
    assert (safe.cnt_sum, safe.ap_sum) == (2, 1)
    lf = encode(ix, h, gran="any", mode=LEAK_FUTURE)
    # all years -> 4 members ap2; minus own -> cnt3 ap2 (future unit f3 now leaks in)
    assert (lf.cnt_sum, lf.ap_sum) == (3, 2)
    assert lf.cnt_sum > safe.cnt_sum          # future inclusion inflates the count
    lo = encode(ix, h, gran="any", mode=LEAK_OWN)
    # year<=2, own NOT masked -> cnt3 ap1 (own outcome leaks in)
    assert (lo.cnt_sum, lo.ap_sum) == (3, 1)
    lb = encode(ix, h, gran="any", mode=M.LEAK_BOTH)
    # no cutoff AND own not masked -> all 4 members (audit's holdout-outcome prior)
    assert (lb.cnt_sum, lb.ap_sum) == (4, 2)


def _pair_dataset(n_classes=6):
    """Per class ci: an early failure (y1,l0) and a late unit (y2, label alternating).
    Under safe the late unit's only prior member is the early failure -> constant prior
    (AUROC 0.5). Under leak_own its own label leaks in -> perfectly separates (AUROC 1)."""
    units, late = [], []
    for i in range(n_classes):
        c = frozenset({f"C{i}"})
        units.append(UnitRow(f"e{i}", 1, 0, c))
        lbl = 1 if i % 2 == 0 else 0
        u = UnitRow(f"l{i}", 2, lbl, c)
        units.append(u)
        late.append(u)
    return units, late


def test_leakage_auroc_must_inflate():
    units, late = _pair_dataset()
    h = History(units)
    y = [u.label for u in late]

    def score(mode):
        return [M.sigmoid(encode(u, h, "any", mode).mean_logit) for u in late]

    a_safe = M.auroc(y, score(SAFE))
    a_leak = M.auroc(y, score(LEAK_OWN))
    assert a_safe == 0.5                       # masked own -> no signal in this construction
    assert a_leak == 1.0                       # own outcome leaks -> perfect ranking
    assert a_leak > a_safe                      # the R7 check: wrong way MUST inflate


def test_drug_vs_unit_mask_removes_siblings():
    # drug D has TWO indications in class A; drug E is a different drug in class A
    units = [
        UnitRow("D::i0", 1, 1, frozenset({"A"}), drug="D"),
        UnitRow("D::i1", 2, 1, frozenset({"A"}), drug="D"),
        UnitRow("E::i0", 1, 0, frozenset({"A"}), drug="E"),
    ]
    h = History(units)
    ix = units[1]  # score D's second indication (year 2)
    # unit mask: removes only D::i1 -> D::i0 sibling + E remain -> cnt2 ap1
    e_unit = encode(ix, h, gran="any", mode=SAFE, own_mask="unit")
    assert (e_unit.cnt_sum, e_unit.ap_sum) == (2, 1)
    # drug mask: removes ALL of drug D (both indications) -> only OTHER drug E remains -> cnt1 ap0
    e_drug = encode(ix, h, gran="any", mode=SAFE, own_mask="drug")
    assert (e_drug.cnt_sum, e_drug.ap_sum) == (1, 0)


def test_dt_restriction_and_degradation():
    # two disease-types; history for class A must not cross dt boundaries
    fx, fy = frozenset({"x"}), frozenset({"y"})
    units = [
        UnitRow("a", 1, 1, frozenset({"A"}), dt=fx),
        UnitRow("b", 2, 0, frozenset({"A"}), dt=fx),
        UnitRow("c", 1, 0, frozenset({"A"}), dt=fy),
        UnitRow("ix", 3, 0, frozenset({"A"}), dt=fx),
    ]
    h = History(units)
    ix = units[3]
    dt = encode(ix, h, gran="dt", mode=SAFE, own_mask="unit")
    # dt='x' history at year<=3: a(1) and b(0) -> cnt2 ap1 (dt='y' unit c excluded)
    assert (dt.cnt_sum, dt.ap_sum) == (2, 1)
    # a unit with no dt tag degrades to not-covered at dt granularity
    nokey = UnitRow("z", 3, 0, frozenset({"A"}))
    assert encode(nokey, h, gran="dt", mode=SAFE).covered is False


def test_ta_multihot_overlap_counts_once():
    # index shares TA with several history units; a unit overlapping on TWO tags counts ONCE
    ix_ta = frozenset({"ta2", "ta7"})
    units = [
        UnitRow("m2", 1, 1, frozenset({"A"}), drug="d1", ta=frozenset({"ta2"})),        # shares ta2
        UnitRow("m7", 1, 0, frozenset({"A"}), drug="d2", ta=frozenset({"ta7"})),        # shares ta7
        UnitRow("both", 2, 1, frozenset({"A"}), drug="d3", ta=frozenset({"ta2", "ta7"})),  # shares both -> once
        UnitRow("none", 1, 1, frozenset({"A"}), drug="d4", ta=frozenset({"ta3"})),      # no overlap
        UnitRow("ix", 3, 0, frozenset({"A"}), drug="d5", ta=ix_ta),
    ]
    h = History(units)
    ix = units[-1]
    e = encode(ix, h, gran="ta", mode=SAFE, own_mask="drug")
    # qualifying: m2, m7, both -> cnt3 (NOT 4 double-counting 'both'); ap = 1+0+1 = 2
    assert (e.cnt_sum, e.ap_sum) == (3, 2)