"""is_a parser tests: multi-parent DAG edges, obsolete-term voiding, non-MONDO parents ignored;
plus the upward ancestor walk used by the disease-type rollup."""
from trial_pos.services.obo_edges import parse_is_a, build_parent_map, ancestors

_OBO = """format-version: 1.2

[Term]
id: MONDO:0000001
name: disease

[Term]
id: MONDO:0007254
name: breast carcinoma
is_a: MONDO:0004993 ! carcinoma
is_a: MONDO:0006517 ! malignant breast neoplasm

[Term]
id: MONDO:0099999
name: obsolete thing
is_a: MONDO:0000001 ! disease
is_obsolete: true

[Term]
id: MONDO:0005148
name: type 2 diabetes mellitus
is_a: MONDO:0005015 ! diabetes mellitus

[Typedef]
id: part_of
""".splitlines()


def test_parses_multiparent_and_skips_obsolete():
    edges = list(parse_is_a(_OBO))
    assert ("MONDO:0007254", "MONDO:0004993") in edges     # multi-parent DAG: both kept
    assert ("MONDO:0007254", "MONDO:0006517") in edges
    assert ("MONDO:0005148", "MONDO:0005015") in edges
    # obsolete term's edge is dropped
    assert ("MONDO:0099999", "MONDO:0000001") not in edges
    # root term with no is_a contributes no edge
    assert not any(c == "MONDO:0000001" for c, _ in edges)
    assert len(edges) == 3


def test_ancestors_includes_self_and_all_parents():
    pm = build_parent_map([("C", "B"), ("B", "A"), ("C", "X")])  # C->B->A and C->X (DAG)
    anc = ancestors(["C"], pm)
    assert anc == {"C", "B", "A", "X"}                 # self + both branches
    assert ancestors(["A"], pm) == {"A"}               # root: only itself


def test_ancestors_cycle_safe():
    pm = build_parent_map([("A", "B"), ("B", "A")])     # pathological cycle
    assert ancestors(["A"], pm) == {"A", "B"}           # terminates, no infinite loop