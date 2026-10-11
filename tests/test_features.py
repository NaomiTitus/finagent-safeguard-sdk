"""Tests for the classifier's features.

The feature module is advisory research code, not a guard: it is measured, not
trusted, and nothing in the linter's write path consults it. What these tests
pin is that each feature computes what its name claims -- because the project
has already been wrong once about what a feature measured.
"""

from __future__ import annotations

import ast

from finagent_safeguard.cli.features import (
    EXCLUDED_FEATURES,
    EXCLUDED_GROUPS,
    FEATURE_GROUPS,
    FEATURE_NAMES,
    MODEL_FEATURES,
    extract,
)


def _one(source: str) -> dict[str, float]:
    tree = ast.parse(source)
    node = next(
        n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    )
    return extract(node, source)


class TestTheCanonicalHardPair:
    """The two functions whose distinction is the label's whole difficulty.

    `_create_header` writes a party's name into a field; `_create_PmtInf_node`
    creates the same empty fields and assigns nothing. The human labels them
    yes and no. **The current features cannot separate them** -- asserted here
    so that a feature claiming to fix it has something to beat.
    """

    WRITES = (
        "def _create_header(self):\n"
        "    Nm_node = ET.Element('Nm')\n"
        "    Nm_node.text = self._config['name']\n"
        "    GrpHdr_node.append(Nm_node)\n"
    )
    BLANK = (
        "def _create_PmtInf_node(self):\n"
        "    ED = dict()\n"
        "    ED['Nm_Dbtr_Node'] = ET.Element('Nm')\n"
        "    ED['IBAN_DbtrAcct_Node'] = ET.Element('IBAN')\n"
        "    return ED\n"
    )

    def test_both_look_the_same_to_the_current_features(self) -> None:
        writes, blank = _one(self.WRITES), _one(self.BLANK)
        for name in ("action_verb", "party_key", "no_params", "private"):
            assert writes[name] == blank[name], name

    def test_the_data_flow_feature_now_separates_them(self) -> None:
        """This test used to assert the *gap* -- that nothing could tell the
        pair apart. It is kept, inverted, because closing that gap is the only
        job the dataflow group was added to do, so a regression here means the
        group has stopped earning its place.

        The separation is asserted on `sensitive_subscripts` specifically.
        `body_lines` and `string_literals` also differ between these two, and
        they differ for the wrong reason -- one function is longer -- which is
        the confounder this whole exercise exists to remove.
        """
        writes, blank = _one(self.WRITES), _one(self.BLANK)
        assert writes["sensitive_subscripts"] > blank["sensitive_subscripts"]

    def test_and_the_docstrings_separate_them_independently(self) -> None:
        """A second signal on the same pair, reading different evidence.

        Two features that fire on the same evidence are one feature. These do
        not: one reads the body's subscripts, the other reads prose the body
        does not contain.
        """
        writes = _one(
            "def _create_header(self):\n"
            "    'Create the GrpHdr node with the initiating party.'\n"
            "    Nm_node.text = self._config['name']\n"
        )
        blank = _one(
            "def _create_PmtInf_node(self):\n"
            "    'Create the blank payment information nodes.'\n"
            "    return dict()\n"
        )
        assert writes["docstring_disclaims"] == 0.0
        assert blank["docstring_disclaims"] == 1.0


class TestVerbClass:
    def test_an_action_verb_is_recognised(self) -> None:
        assert _one("def transfer_funds(amount):\n    pass\n")["action_verb"] == 1.0

    def test_a_query_verb_is_recognised(self) -> None:
        assert _one("def get_balance(account):\n    pass\n")["query_verb"] == 1.0

    def test_a_check_verb_is_recognised(self) -> None:
        assert _one("def validate_iban(code):\n    pass\n")["check_verb"] == 1.0

    def test_a_private_class_prefixed_verb_is_still_found(self) -> None:
        """`_cls_cancel_async` leads with a private marker and a class prefix.
        Taking literally the first word would make every such method
        verbless."""
        got = _one("def _cls_cancel_async(cls, outbound_transfer):\n    pass\n")
        assert got["action_verb"] == 1.0
        assert got["private"] == 1.0

    def test_the_classes_are_mutually_exclusive(self) -> None:
        got = _one("def save_payment(amount):\n    pass\n")
        assert got["action_verb"] + got["query_verb"] + got["check_verb"] == 1.0


class TestVocabulary:
    def test_money_and_person_words_are_counted_separately(self) -> None:
        got = _one("def capture(payment, amount, customer_id):\n    pass\n")
        assert got["money_hits"] >= 1.0
        assert got["pii_hits"] >= 1.0
        assert got["both_vocabularies"] == 1.0

    def test_self_and_cls_are_not_counted_as_parameters(self) -> None:
        """Otherwise every method would score one parameter higher than a free
        function doing the same thing."""
        assert _one("def f(self, amount):\n    pass\n")["param_count"] == 1.0
        assert _one("def g(cls, amount):\n    pass\n")["param_count"] == 1.0

    def test_a_method_taking_only_self_has_no_parameters(self) -> None:
        assert _one("def f(self):\n    x = 1\n")["no_params"] == 1.0


class TestSinkAndMock:
    def test_a_write_call_is_a_sink(self) -> None:
        assert _one("def f(df, path):\n    df.to_excel(path)\n")["sink_call"] == 1.0

    def test_a_mocked_transport_is_detected(self) -> None:
        got = _one(
            "def test_x(self, http_client_mock):\n"
            "    http_client_mock.assert_requested('get')\n"
        )
        assert got["mocked"] == 1.0


class TestPartyKeyLiteral:
    """The `literal` group is excluded from the fitted model -- it was measured
    at -0.069 LORO AUC, i.e. actively harmful, because it tracks house style.
    It is still produced and still tested: it is reported to a human in the
    ranked queue, and an untested feature is one nobody can argue with.
    """

    def test_a_party_key_literal_is_detected(self) -> None:
        got = _one("def f(self):\n    node.text = row['name']\n")
        assert got["party_key"] == 1.0

    def test_a_body_with_no_party_literal_does_not_fire(self) -> None:
        got = _one("def f(self):\n    node.text = row['total']\n")
        assert got["party_key"] == 0.0


class TestTheFittedSet:
    """`extract` produces 21 features; the model is fitted on 17.

    The gap is a measured decision, not an oversight, so it is pinned. The
    `literal` group scored **-0.069 LORO AUC** -- actively harmful, because it
    counts length and punctuation and so learns which repository a function
    came from. Under a stratified split it looked like the strongest group of
    all (+0.172). Silently refitting it would undo the finding that the whole
    leave-one-repository-out exercise exists to establish.
    """

    def test_the_harmful_group_is_not_fitted(self) -> None:
        assert set(MODEL_FEATURES).isdisjoint(FEATURE_GROUPS["literal"])

    def test_every_fitted_feature_is_still_produced(self) -> None:
        """A fitted name `extract` does not produce would raise at scan time
        on every single function, so this is the cheap end of that failure."""
        assert set(MODEL_FEATURES) <= set(FEATURE_NAMES)

    def test_the_redundant_signature_feature_is_not_fitted(self) -> None:
        """`pii_hits` is excluded for redundancy, not for being wrong.

        `both_vocabularies` is `1 if money_hits and pii_hits` over the same scan
        of the same names, so it can only fire where `pii_hits` already has
        (r = 0.547). Fitting both split one feature's credit across two
        expressions of it; dropping `pii_hits` is worth +0.019 LORO AUC,
        95% CI [+0.009, +0.032].
        """
        assert "pii_hits" not in MODEL_FEATURES

    def test_the_excluded_feature_is_still_produced(self) -> None:
        """Kept visible to a human reading the ranked queue, and kept as the
        evidence for its own exclusion. The same reason the `literal` group
        survives in the codebase."""
        assert "pii_hits" in FEATURE_NAMES
        assert "pii_hits" in _one("def f(email):\n    pass\n")

    def test_the_harmful_annotation_feature_is_not_fitted(self) -> None:
        """`risky_annotation` is the only feature measured as actively harmful.

        It fires on **one row in 996** and the fit hands that single observation
        a weight of -5.875, the largest magnitude in the model. Removing it
        moves LORO macro AUC 0.793 -> 0.798. Independent evidence agrees: only
        2 678 of 70 826 public Python repositories carry PEP 484 annotations
        (Rak-amnouykit, DLS 2020), so the feature measures a project's
        conventions rather than what a function does.
        """
        assert "risky_annotation" not in MODEL_FEATURES

    def test_the_feature_exclusion_is_declared_rather_than_implied(self) -> None:
        for name in EXCLUDED_FEATURES:
            assert name in FEATURE_NAMES, name

    def test_the_exclusion_is_declared_rather_than_implied(self) -> None:
        """Naming the excluded group keeps the reason discoverable. An
        exclusion expressed only as a hard-coded tuple of 17 names would be
        indistinguishable from someone having forgotten four.
        """
        assert EXCLUDED_GROUPS
        for group in EXCLUDED_GROUPS:
            assert group in FEATURE_GROUPS


class TestEveryFeatureIsProduced:
    def test_extract_returns_the_declared_set(self) -> None:
        """A feature named in FEATURE_NAMES but not produced would be read as
        a missing key at scan time, and one produced but not named would be
        silently ignored by the model."""
        got = _one("def f(x):\n    return x\n")
        assert set(got) == set(FEATURE_NAMES)

    def test_all_values_are_finite_floats(self) -> None:
        import math

        got = _one("def f(x):\n    return x\n")
        for name, value in got.items():
            assert isinstance(value, float), name
            assert math.isfinite(value), name


class TestDataFlow:
    """The group that carried the largest transferable gain: +0.092 LORO AUC.

    Every assertion here is about *which key is read for its value*, because
    that is the claim the group's name makes and the one it was measured on.
    """

    def test_reading_a_party_name_out_of_a_mapping_is_sensitive(self) -> None:
        got = _one("def f(self):\n    return self._config['name']\n")
        assert got["sensitive_subscripts"] == 1.0

    def test_writing_into_a_slot_named_for_a_party_is_not(self) -> None:
        """The bug this feature shipped with, pinned so it cannot return.

        ``ED['name'] = ET.Element('Nm')`` names a slot in a local dictionary;
        nothing sensitive is read. Counting stores made the blank-node builder
        outscore the function that reads a real name -- the feature firing
        backwards while still looking plausible in aggregate.
        """
        got = _one(
            "def f(self):\n"
            "    ED = dict()\n"
            "    ED['name'] = ET.Element('Nm')\n"
            "    return ED\n"
        )
        assert got["sensitive_subscripts"] == 0.0

    def test_a_mapping_read_through_get_also_counts(self) -> None:
        """Much of the corpus reads mappings with ``.get``. A feature seeing
        only ``[...]`` would be measuring house style again."""
        got = _one("def f(self):\n    return self._row.get('iban')\n")
        assert got["sensitive_subscripts"] == 1.0

    def test_an_insensitive_key_is_counted_only_in_the_total(self) -> None:
        got = _one("def f(self):\n    return self._config.get('domestic', False)\n")
        assert got["sensitive_subscripts"] == 0.0
        assert got["subscript_total"] == 1.0

    def test_distinct_keys_are_counted_not_repeated_reads(self) -> None:
        """Reading one key three times is one fact about the function, so a
        loop over a single key must not outrank a body touching three."""
        repeated = _one(
            "def f(self):\n"
            "    a = self._c['iban']\n"
            "    b = self._c['iban']\n"
            "    c = self._c['iban']\n"
        )
        varied = _one(
            "def g(self):\n"
            "    a = self._c['iban']\n"
            "    b = self._c['debtor']\n"
            "    c = self._c['amount']\n"
        )
        assert repeated["sensitive_subscripts"] == 1.0
        assert varied["sensitive_subscripts"] == 3.0

    def test_a_non_string_subscript_is_ignored(self) -> None:
        """``rows[0]`` and ``rows[i]`` are indexing, not key access."""
        got = _one("def f(rows, i):\n    return rows[0], rows[i]\n")
        assert got["subscript_total"] == 0.0

    def test_a_callee_named_in_domain_vocabulary_fires(self) -> None:
        got = _one("def f(self):\n    self.add_payment(x)\n")
        assert got["callee_vocabulary"] == 1.0

    def test_a_callee_named_generically_does_not(self) -> None:
        """The feature must stay a vocabulary check. A specific library's
        method name as a literal target would encode repository identity,
        which is what leave-one-repository-out exists to punish."""
        got = _one("def f(self):\n    self.append(x)\n    helper(y)\n")
        assert got["callee_vocabulary"] == 0.0

    def test_the_counts_are_capped(self) -> None:
        """Unbounded counts let one outlier dominate a standardised feature."""
        body = "".join(f"    x{i} = self._c['iban']\n" for i in range(40))
        got = _one("def f(self):\n" + body)
        assert got["subscript_total"] == 10.0


class TestDocstringIntent:
    def test_intent_words_are_counted(self) -> None:
        got = _one("def f():\n    'Transfer the payment amount.'\n")
        assert got["docstring_intent"] >= 2.0

    def test_a_missing_docstring_scores_zero_rather_than_disclaiming(self) -> None:
        """Absent and denied are different states. Reading no docstring as a
        disclaimer would mark most of the corpus as safe by default."""
        got = _one("def f():\n    pass\n")
        assert got["docstring_intent"] == 0.0
        assert got["docstring_disclaims"] == 0.0

    def test_a_disclaimer_is_detected(self) -> None:
        got = _one("def f():\n    'Create the blank nodes.'\n")
        assert got["docstring_disclaims"] == 1.0

    def test_intent_and_disclaiming_are_independent(self) -> None:
        """A docstring can be full of domain words and still say it does
        nothing with them. That is precisely the hard negative."""
        got = _one("def f():\n    'Create blank payment account nodes.'\n")
        assert got["docstring_intent"] >= 2.0
        assert got["docstring_disclaims"] == 1.0
