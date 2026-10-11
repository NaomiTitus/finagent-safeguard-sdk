"""Tests for the classifier's features.

The feature module is advisory research code, not a guard: it is measured, not
trusted, and nothing in the linter's write path consults it. What these tests
pin is that each feature computes what its name claims -- because the project
has already been wrong once about what a feature measured.
"""

from __future__ import annotations

import ast

from finagent_safeguard.cli.features import FEATURE_NAMES, extract


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

    def test_which_is_why_a_data_flow_feature_is_needed(self) -> None:
        """Documented as a failing property rather than a passing one: the
        difference is an assignment of a non-constant value into a sensitive
        field, and no current feature inspects assignments."""
        writes, blank = _one(self.WRITES), _one(self.BLANK)
        differing = [n for n in FEATURE_NAMES if writes[n] != blank[n]]
        assert differing in ([], ["body_lines"], ["string_literals"],
                             ["body_lines", "string_literals"]), differing


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
