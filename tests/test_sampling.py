"""Tests for the stratified sampling module. Plain asserts so tests/_run_stdlib.py works.

NO TRANSCRIBED EXPECTED VALUES. The allocation expectations are recomputed here from the
square-root rule in closed form, so a bug in `sqrt_allocation` cannot be certified by a
number copied out of its own output. Everything else is a structural property: the
allocation sums to its budget, no stratum is over-drawn, weights multiply back to the
population, a duplicated item appears exactly twice.
"""
from __future__ import annotations

import math

from trial_pos.services.eligibility import KNOWN_PHASES
from trial_pos.services.sampling import (
    DEFAULT_MIN_PER_STRATUM, DEFAULT_SAMPLE_SALT, DEFAULT_SAMPLE_SIZE,
    PHASE_GROUPS, PHASE_GROUP_DOC, PHASE_GROUP_PHASE1, PHASE_GROUP_PIVOTAL,
    PHASE_GROUP_UNKNOWN, allocation_report, choose_duplicates, duplicate_pairs, label_id,
    ordered_by_hash, phase_group, presentation_rows, select, sqrt_allocation,
    stable_hash, stratum_weights,
)


# ---- phase groups --------------------------------------------------------
def test_every_phase_eligibility_knows_is_placed_in_a_group():
    # if eligibility learns a new phase, this module must place it rather than silently
    # dropping it into 'unknown', which would put real trials in the absent-phase stratum
    for phase in KNOWN_PHASES:
        assert phase_group(phase) != PHASE_GROUP_UNKNOWN, phase


def test_phase_group_is_total_over_arbitrary_input():
    for raw in (None, "", "   ", "NA", "na", "PHASE7", "something else", 3):
        assert phase_group(raw) in PHASE_GROUPS


def test_absent_phase_is_unknown_not_early():
    # absent is not a claim about the phase
    for raw in (None, "", "NA"):
        assert phase_group(raw) == PHASE_GROUP_UNKNOWN


def test_phase_one_group_holds_the_spanning_registration():
    # PHASE1/PHASE2 is grouped with phase 1 here because its endpoint text is the
    # early-phase kind, which is a different question from fdaaa.py's statutory reading
    assert phase_group("PHASE1/PHASE2") == PHASE_GROUP_PHASE1
    assert phase_group("PHASE1") == PHASE_GROUP_PHASE1
    assert phase_group("PHASE3") == PHASE_GROUP_PIVOTAL


def test_every_group_is_documented():
    for group in PHASE_GROUPS:
        assert PHASE_GROUP_DOC[group].strip()


# ---- deterministic ordering ---------------------------------------------
def test_stable_hash_is_stable_and_salt_sensitive():
    assert stable_hash("NCT1#1", "a") == stable_hash("NCT1#1", "a")
    assert stable_hash("NCT1#1", "a") != stable_hash("NCT1#1", "b")


def test_ordering_is_a_permutation():
    keys = [f"NCT{i:08d}#1" for i in range(50)]
    assert sorted(ordered_by_hash(keys, "s")) == sorted(keys)


def test_ordering_is_reproducible_and_depends_on_the_salt():
    keys = [f"NCT{i:08d}#1" for i in range(50)]
    assert ordered_by_hash(keys, "s") == ordered_by_hash(keys, "s")
    # with 50 keys the chance of two salts agreeing on the whole order is 1/50!
    assert ordered_by_hash(keys, "s") != ordered_by_hash(keys, "t")


def test_ordering_does_not_depend_on_input_order():
    keys = [f"NCT{i:08d}#1" for i in range(30)]
    assert ordered_by_hash(keys, "s") == ordered_by_hash(list(reversed(keys)), "s")


def test_label_ids_differ_between_the_two_presentations_of_one_item():
    # the twins must not be spottable by eye, or the self-agreement measure is worthless
    assert label_id("NCT1#1", 0, "s") != label_id("NCT1#1", 1, "s")


def test_label_id_is_reproducible_from_the_printed_salt():
    assert label_id("NCT1#1", 0, "s") == label_id("NCT1#1", 0, "s")


# ---- allocation ----------------------------------------------------------
def _sizes():
    return {"a": 10, "b": 100, "c": 400, "d": 2000}


def test_allocation_sums_to_the_budget():
    alloc = sqrt_allocation(_sizes(), total=200, min_per_stratum=6)
    assert sum(alloc.values()) == 200


def test_no_stratum_is_allocated_more_than_it_holds():
    sizes = _sizes()
    alloc = sqrt_allocation(sizes, total=200, min_per_stratum=6)
    for stratum, n in alloc.items():
        assert n <= sizes[stratum]


def test_every_non_empty_stratum_reaches_its_floor():
    sizes = _sizes()
    floor = 6
    alloc = sqrt_allocation(sizes, total=200, min_per_stratum=floor)
    for stratum, n in alloc.items():
        assert n >= min(floor, sizes[stratum])


def test_empty_strata_are_dropped_rather_than_allocated_zero():
    sizes = {"a": 0, "b": 50}
    alloc = sqrt_allocation(sizes, total=20, min_per_stratum=2)
    assert "a" not in alloc


def test_allocation_follows_the_square_root_rule_in_closed_form():
    # two strata whose sizes differ by a factor of four should differ by a factor of two
    # once the floor is out of the way. Recomputed from math.sqrt rather than transcribed.
    sizes = {"small": 100, "big": 400}
    total = 90
    alloc = sqrt_allocation(sizes, total=total, min_per_stratum=0)
    weights = {k: math.sqrt(v) for k, v in sizes.items()}
    denominator = sum(weights.values())
    for stratum in sizes:
        expected = int(total * weights[stratum] / denominator)
        assert alloc[stratum] == expected


def test_allocation_is_monotone_in_stratum_size():
    sizes = {"small": 10, "big": 1000}
    alloc = sqrt_allocation(sizes, total=100, min_per_stratum=6)
    assert alloc["big"] >= alloc["small"]


def test_a_stratum_smaller_than_its_share_is_capped_and_the_rest_reallocated():
    sizes = {"small": 10, "big": 1000}
    total = 100
    alloc = sqrt_allocation(sizes, total=total, min_per_stratum=6)
    assert alloc["small"] == sizes["small"]      # capped at everything it has
    assert sum(alloc.values()) == total          # and the remainder went elsewhere


def test_a_budget_larger_than_the_population_is_a_census_not_an_error():
    sizes = {"a": 3, "b": 4}
    alloc = sqrt_allocation(sizes, total=100, min_per_stratum=6)
    assert alloc == sizes


def test_floors_that_cannot_be_met_raise_rather_than_dropping_strata():
    # an allocation that cannot satisfy its own floor is a sample-size decision for the
    # operator, not something to resolve silently
    sizes = {name: 100 for name in "abcdefghij"}
    raised = False
    try:
        sqrt_allocation(sizes, total=20, min_per_stratum=6)
    except ValueError:
        raised = True
    assert raised


def test_a_zero_budget_allocates_nothing():
    alloc = sqrt_allocation(_sizes(), total=0, min_per_stratum=0)
    assert sum(alloc.values()) == 0


def test_negative_arguments_raise():
    for kwargs in ({"total": -1}, {"min_per_stratum": -1}):
        raised = False
        try:
            sqrt_allocation(_sizes(), **kwargs)
        except ValueError:
            raised = True
        assert raised


def test_the_defaults_are_a_feasible_allocation():
    # the shipped defaults must not be a configuration that raises on a realistic stratum
    # count: five phase groups times six classes is thirty cells
    sizes = {f"cell{i}": 50 + i for i in range(30)}
    alloc = sqrt_allocation(sizes, total=DEFAULT_SAMPLE_SIZE,
                            min_per_stratum=DEFAULT_MIN_PER_STRATUM)
    assert sum(alloc.values()) == DEFAULT_SAMPLE_SIZE


# ---- weights -------------------------------------------------------------
def test_weights_scale_a_stratum_back_to_its_population():
    sizes = _sizes()
    alloc = sqrt_allocation(sizes, total=200, min_per_stratum=6)
    weights = stratum_weights(sizes, alloc)
    for stratum, weight in weights.items():
        assert abs(weight * alloc[stratum] - sizes[stratum]) < 1e-9


def test_no_weight_is_invented_for_a_stratum_with_no_observations():
    # a stratum that contributed nothing has no matrix to scale, and a weight for it would
    # imply otherwise
    assert stratum_weights({"a": 100}, {"a": 0}) == {}


def test_weights_are_at_least_one():
    sizes = _sizes()
    alloc = sqrt_allocation(sizes, total=200, min_per_stratum=6)
    for weight in stratum_weights(sizes, alloc).values():
        assert weight >= 1.0


def test_proportional_allocation_gives_equal_weights():
    # the condition under which weighting cannot change a kappa, which is what
    # agreement.weighted_summary's own test relies on
    sizes = {"a": 100, "b": 200}
    weights = stratum_weights(sizes, {"a": 10, "b": 20})
    assert len(set(weights.values())) == 1


# ---- selection -----------------------------------------------------------
def _members():
    return {"a": [f"A{i}" for i in range(10)],
            "b": [f"B{i}" for i in range(100)],
            "c": [f"C{i}" for i in range(400)]}


def test_selection_returns_exactly_the_allocated_counts():
    members = _members()
    sizes = {k: len(v) for k, v in members.items()}
    alloc = sqrt_allocation(sizes, total=60, min_per_stratum=6)
    selected = select(members, alloc)
    for stratum, keys in selected.items():
        assert len(keys) == alloc[stratum]
        assert len(set(keys)) == len(keys)


def test_selected_items_come_from_their_own_stratum():
    members = _members()
    sizes = {k: len(v) for k, v in members.items()}
    alloc = sqrt_allocation(sizes, total=60, min_per_stratum=6)
    for stratum, keys in select(members, alloc).items():
        assert set(keys) <= set(members[stratum])


def test_selection_is_reproducible_and_salt_dependent():
    members = _members()
    alloc = {"a": 5, "b": 20, "c": 30}
    assert select(members, alloc, "s") == select(members, alloc, "s")
    assert select(members, alloc, "s") != select(members, alloc, "t")


def test_selection_refuses_to_over_draw_a_stratum():
    # allocation and membership computed from different populations is the mismatch that
    # otherwise surfaces later as a weight below 1
    raised = False
    try:
        select({"a": ["A1", "A2"]}, {"a": 3})
    except ValueError:
        raised = True
    assert raised


# ---- duplicates ----------------------------------------------------------
def test_duplicate_count_is_exact():
    selected = {"a": [f"A{i}" for i in range(20)], "b": [f"B{i}" for i in range(30)]}
    assert len(choose_duplicates(selected, 10)) == 10


def test_duplicates_are_drawn_from_the_selected_items_only():
    selected = {"a": [f"A{i}" for i in range(20)], "b": [f"B{i}" for i in range(30)]}
    pool = {key for keys in selected.values() for key in keys}
    assert set(choose_duplicates(selected, 10)) <= pool


def test_duplicates_are_spread_across_strata():
    # a self-agreement figure measured entirely inside one stratum would describe that
    # stratum's difficulty, not the task's
    selected = {"a": [f"A{i}" for i in range(20)], "b": [f"B{i}" for i in range(30)]}
    chosen = choose_duplicates(selected, 10)
    assert any(k.startswith("A") for k in chosen)
    assert any(k.startswith("B") for k in chosen)


def test_asking_for_more_duplicates_than_exist_raises():
    raised = False
    try:
        choose_duplicates({"a": ["A1"]}, 5)
    except ValueError:
        raised = True
    assert raised


def test_zero_duplicates_is_allowed_and_empty():
    assert choose_duplicates({"a": ["A1", "A2"]}, 0) == []


# ---- presentation --------------------------------------------------------
def _presentation():
    selected = {"a": [f"A{i}" for i in range(10)], "b": [f"B{i}" for i in range(20)]}
    duplicates = choose_duplicates(selected, 6)
    return selected, duplicates, presentation_rows(selected, duplicates)


def test_one_row_per_presentation():
    selected, duplicates, rows = _presentation()
    n_selected = sum(len(keys) for keys in selected.values())
    assert len(rows) == n_selected + len(duplicates)


def test_a_duplicated_item_appears_exactly_twice_and_others_once():
    selected, duplicates, rows = _presentation()
    counts: dict = {}
    for row in rows:
        counts[row["key"]] = counts.get(row["key"], 0) + 1
    for key, count in counts.items():
        assert count == (2 if key in set(duplicates) else 1)


def test_label_ids_are_unique_across_presentations():
    _selected, _duplicates, rows = _presentation()
    ids = [row["label_id"] for row in rows]
    assert len(set(ids)) == len(ids)


def test_an_id_collision_raises_rather_than_merging_two_labels():
    # the same key twice in one stratum would produce two copy-0 rows with one id, and
    # merging two hand labels into one is silent data loss
    raised = False
    try:
        presentation_rows({"a": ["A1", "A1"]}, [])
    except ValueError:
        raised = True
    assert raised


def test_presentation_order_does_not_leak_the_selection_order():
    # if the order tracked the input, every duplicate would sit beside its twin
    selected = {"a": [f"A{i}" for i in range(10)], "b": [f"B{i}" for i in range(20)]}
    reversed_selected = {k: list(reversed(v)) for k, v in selected.items()}
    duplicates = choose_duplicates(selected, 6)
    first = [row["label_id"] for row in presentation_rows(selected, duplicates)]
    second = [row["label_id"] for row in
              presentation_rows(reversed_selected, duplicates)]
    assert first == second


def test_only_duplicated_rows_carry_a_group():
    _selected, duplicates, rows = _presentation()
    for row in rows:
        assert bool(row["duplicate_group"]) is (row["key"] in set(duplicates))


def test_duplicate_pairs_are_one_per_duplicated_item_and_ordered_by_copy():
    _selected, duplicates, rows = _presentation()
    pairs = duplicate_pairs(rows)
    assert len(pairs) == len(duplicates)
    by_id = {row["label_id"]: row for row in rows}
    for first, second in pairs:
        assert by_id[first]["copy_index"] == 0
        assert by_id[second]["copy_index"] == 1
        assert by_id[first]["key"] == by_id[second]["key"]


def test_duplicate_pairs_rejects_a_group_that_is_not_a_pair():
    raised = False
    try:
        duplicate_pairs([{"label_id": "L1", "key": "A1", "copy_index": 0,
                          "duplicate_group": "A1"}])
    except ValueError:
        raised = True
    assert raised


# ---- the printed table ---------------------------------------------------
def test_allocation_report_shares_sum_to_one():
    sizes = _sizes()
    alloc = sqrt_allocation(sizes, total=200, min_per_stratum=6)
    rows = allocation_report(sizes, alloc)
    assert abs(sum(row["population_share"] for row in rows) - 1.0) < 1e-9
    assert abs(sum(row["sample_share"] for row in rows) - 1.0) < 1e-9


def test_allocation_report_weight_matches_the_weight_function():
    sizes = _sizes()
    alloc = sqrt_allocation(sizes, total=200, min_per_stratum=6)
    weights = stratum_weights(sizes, alloc)
    for row in allocation_report(sizes, alloc):
        assert row["weight"] == weights.get(row["stratum"])


def test_allocation_report_covers_every_stratum_including_unsampled_ones():
    sizes = {"a": 10, "b": 0}
    alloc = sqrt_allocation(sizes, total=5, min_per_stratum=1)
    rows = allocation_report(sizes, alloc)
    assert {row["stratum"] for row in rows} == set(sizes)


def test_default_salt_is_a_printable_non_empty_string():
    # the sample is reproducible from the printed value alone, so it has to be printable
    assert isinstance(DEFAULT_SAMPLE_SALT, str) and DEFAULT_SAMPLE_SALT.strip()