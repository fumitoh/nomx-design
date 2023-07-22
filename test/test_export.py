import sys
import pathlib

import pandas as pd
import modelx as mx
import pytest
from nomx.exporter import Exporter


sample_dir = pathlib.Path('__file__').parent / 'samples'


def test_nested_space_ref(tmp_path):
    nomx_path = tmp_path / 'model'
    m = mx.read_model(sample_dir / 'NestedSpace')
    Exporter(m, nomx_path / 'NestedSpace').export()

    try:
        sys.path.insert(0, str(nomx_path))
        from NestedSpace import mx_model
        assert mx_model.Pibling.sibling.Child.GrandChild.foo() == 'Hello!'
        assert mx_model.Parent.Child.GrandChild.grandpibling.bar() == 'Hello! World.'
    finally:
        sys.path.pop(0)


def test_pandasio(tmp_path):
    nomx_path = tmp_path / 'model'
    m = mx.read_model(sample_dir / 'PandasData')
    Exporter(m, nomx_path / 'PandasData').export()

    try:
        sys.path.insert(0, str(nomx_path))
        from PandasData import mx_model
        pd.testing.assert_frame_equal(mx_model.Foo.df, m.Foo.df)
    finally:
        sys.path.pop(0)
        m.close()


def test_pickle(tmp_path):
    nomx_path = tmp_path / 'model'
    m = mx.read_model(sample_dir / 'PickleSample')
    Exporter(m, nomx_path / 'PickleSample').export()

    try:
        sys.path.insert(0, str(nomx_path))
        from PickleSample import mx_model
        pd.testing.assert_frame_equal(mx_model.Space1.df, m.Space1.df)
    finally:
        sys.path.pop(0)
        m.close()


def test_subscript(tmp_path):
    nomx_path = tmp_path / 'model'
    m = mx.read_model(sample_dir / 'SampleSubscript')
    Exporter(m, nomx_path / 'SampleSubscript').export()

    try:
        sys.path.insert(0, str(nomx_path))
        from SampleSubscript import mx_model
        assert mx_model.Space1.foo(1, 2, 3) == 10
    finally:
        sys.path.pop(0)
        m.close()


@pytest.fixture(scope="module")
def mortgage_model(tmp_path_factory):
    nomx_path = tmp_path_factory.mktemp('model')
    m = mx.read_model(sample_dir / "FixedMortgage")
    Exporter(m, nomx_path / 'FixedMortgage').export()

    try:
        sys.path.insert(0, str(nomx_path))
        from FixedMortgage import mx_model
        yield m, mx_model
    finally:
        sys.path.pop(0)
        m._impl._check_sanity()
        m.close()


def test_literal_ref(mortgage_model):
    source, target = mortgage_model
    assert source.Fixed.Principal == target.Fixed.Principal == 100_000
    assert source.Fixed.Term == target.Fixed.Term == 30
    assert source.Fixed.Rate == target.Fixed.Rate == 0.03


def test_module_ref(mortgage_model):
    source, target = mortgage_model
    assert source.Summary.itertools is sys.modules['itertools']

