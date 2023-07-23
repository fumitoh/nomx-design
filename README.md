# nomx-design

This repository is for proto-typing the feature 
of exporting *modelx* models to pure-python models (*nomx* models).

A *nomx* model is a Python package, and does not depend on modelx.

## Example

The code below creates *FixedMortgage_nomx* from *FixedMortgage*.
The *FixedMortgage* model is included in the *test/samples* directory
in this repo with other sample modelx models to test this feature.

```python
import modelx as mx
m = mx.read_model('FixedMortgage')

from nomx import exporter
exporter.Exporter(m, "FixedMortgage_nomx").export()
```

The nomx model is then available as `mx_model`.

```pycon
>>> from FixedMortgage_nomx import mx_model

>>> mx_model.Summary.Payments()
[{'Term': 20, 'Rate': 0.03, 'Payment': 6721.570759685908},
 {'Term': 20, 'Rate': 0.04, 'Payment': 7358.175032862885},
 {'Term': 30, 'Rate': 0.03, 'Payment': 5101.925932025255},
 {'Term': 30, 'Rate': 0.04, 'Payment': 5783.009913366131}]
```