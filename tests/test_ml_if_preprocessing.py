"""IF 기준 전처리의 Train/Inference 의미와 실패 경계를 검증한다."""

import numpy as np
import pytest

from sos_platform.ml_if_preprocessing import FittedIFMedianImputer, fit_if_median_imputer


def test_training_only_medians_apply_to_unseen_missing_values():
    train = np.array([[1.0, 10.0], [np.nan, 20.0], [5.0, 30.0]])
    original = train.copy()
    fitted = fit_if_median_imputer(train, feature_names=["signal", "rate"])
    assert fitted.feature_names == ("signal", "rate")
    assert fitted.medians == (3.0, 20.0)
    np.testing.assert_array_equal(
        fitted.transform(train, feature_names=["signal", "rate"]),
        [[1.0, 10.0], [3.0, 20.0], [5.0, 30.0]],
    )
    np.testing.assert_array_equal(
        fitted.transform([[np.nan, np.nan], [1000.0, 2000.0]],
                         feature_names=["signal", "rate"]),
        [[3.0, 20.0], [1000.0, 2000.0]],
    )
    np.testing.assert_array_equal(train, original)


def test_training_reference_is_not_changed_by_calibration_or_test_values():
    fitted = fit_if_median_imputer([[0.0], [2.0], [4.0]], feature_names=["feature"])
    fitted.transform([[1000000.0], [np.nan]], feature_names=["feature"])
    assert fitted.medians == (2.0,)


def test_unseen_null_is_allowed_even_when_training_had_no_nulls():
    fitted = fit_if_median_imputer([[1.0], [3.0]], feature_names=["feature"])
    assert fitted.transform([[np.nan]], feature_names=["feature"])[0, 0] == 2.0


def test_feature_order_is_an_explicit_signature():
    fitted = fit_if_median_imputer([[1.0, 2.0], [3.0, 4.0]],
                                   feature_names=["first", "second"])
    with pytest.raises(ValueError, match="signature"):
        fitted.transform([[2.0, 1.0]], feature_names=["second", "first"])


@pytest.mark.parametrize("feature_names", [[], ["a", "a"], ["", "b"]])
def test_invalid_training_feature_names_rejected(feature_names):
    with pytest.raises(ValueError):
        fit_if_median_imputer([[1.0, 2.0], [3.0, 4.0]], feature_names=feature_names)


@pytest.mark.parametrize("input_matrix", [
    [[np.nan, 1.0], [np.nan, 2.0]],
    [[1.0, np.inf], [2.0, 3.0]],
    [[1.0, -np.inf], [2.0, 3.0]],
    [[True, False], [False, True]],
])
def test_invalid_training_values_rejected(input_matrix):
    with pytest.raises(ValueError):
        fit_if_median_imputer(input_matrix, feature_names=["a", "b"])


def test_invalid_inference_values_rejected():
    fitted = fit_if_median_imputer([[1.0], [3.0]], feature_names=["a"])
    for row in [[[np.inf]], [[-np.inf]], [["oops"]], [[0.0, 1.0]], []]:
        with pytest.raises(ValueError):
            fitted.transform(row, feature_names=["a"])


def test_large_integer_precision_not_silently_lost():
    fitted = fit_if_median_imputer([[1.0], [3.0]], feature_names=["a"])
    with pytest.raises(ValueError, match="exact float64"):
        fitted.transform(np.array([[2**53 + 1]], dtype=np.uint64), feature_names=["a"])


def test_numeric_strings_and_object_values_are_not_silently_coerced():
    fitted = fit_if_median_imputer([[1.0], [3.0]], feature_names=["a"])
    for input_rows in ([["2"]], [[None]], [[True]]):
        with pytest.raises(ValueError, match="numeric"):
            fitted.transform(input_rows, feature_names=["a"])


def test_constant_training_features_are_not_implicitly_dropped():
    fitted = fit_if_median_imputer([[2.0, 5.0], [2.0, 7.0]],
                                   feature_names=["constant", "changing"])
    assert fitted.feature_names == ("constant", "changing")
    assert fitted.medians == (2.0, 6.0)


@pytest.mark.parametrize("names", ["ab", None, 123])
def test_feature_names_not_implicitly_split_or_unpacked(names):
    with pytest.raises(ValueError):
        fit_if_median_imputer([[1.0, 2.0], [3.0, 4.0]], feature_names=names)


@pytest.mark.parametrize("names, medians", [
    (("a", "b"), (1.0,)),
    (("a",), (np.nan,)),
    (("a",), (np.inf,)),
    (("a", "a"), (1.0, 2.0)),
    (("a",), [1.0]),
    (["a"], (1.0,)),
])
def test_invalid_fitted_state_cannot_be_instantiated(names, medians):
    with pytest.raises(ValueError):
        FittedIFMedianImputer(feature_names=names, medians=medians)


def test_large_int_in_mixed_python_list_does_not_round_silently():
    with pytest.raises(ValueError, match="exact float64"):
        fit_if_median_imputer([[2**53 + 3, 2.0], [1, 3.0]], feature_names=["a", "b"])


def test_masked_array_not_silently_treated_as_non_missing():
    masked = np.ma.array([[1.0], [2.0]], mask=[[True], [False]])
    with pytest.raises(ValueError, match="Masked"):
        fit_if_median_imputer(masked, feature_names=["a"])


def test_extreme_finite_training_values_yield_finite_medians():
    huge = np.finfo(np.float64).max
    fitted = fit_if_median_imputer([[huge], [huge]], feature_names=["a"])
    assert fitted.medians == (huge,)
    assert np.isfinite(fitted.transform([[np.nan]], feature_names=["a"])).all()


def test_mixed_boolean_and_float_list_is_not_implicitly_cast():
    with pytest.raises(ValueError, match="Boolean"):
        fit_if_median_imputer([[True, 1.0], [False, 3.0]], feature_names=["a", "b"])
