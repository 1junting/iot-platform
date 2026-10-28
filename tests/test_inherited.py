import json
import unittest

import cbor2
import numpy as np

from exchange.lstm import LSTM
from exchange.model import decode, fingerprint, normalize
from exchange.predictor import floor_for


class MappingTests(unittest.TestCase):
    def setUp(self):
        self.obj = {'message_id': 'unit-test', 'device_id': 'fixture', 'observed_at': '2026-10-19T00:00:00Z', 'values': {'input': {'value': 95, 'unit': 'F'}, 'unknown': {'value': 'opaque', 'unit': 'custom'}}}
        self.raw = json.dumps(self.obj).encode()

    def test_explicit_conversion_and_raw_preservation(self):
        rules = [{'source': 'input', 'target': 'output', 'source_unit': 'F', 'target_unit': 'C', 'scale': 5/9, 'offset': -32*5/9}]
        result = normalize(decode(self.raw, 'json'), rules, 'mqtt', self.raw, 'json')
        self.assertAlmostEqual(result['values']['output']['value'], 35)
        self.assertEqual(result['values']['unknown'], {'value': 'opaque', 'unit': 'custom', 'mapping': 'unchanged'})
        self.assertTrue(result['raw']['base64'])

    def test_units_are_not_guessed(self):
        with self.assertRaisesRegex(ValueError, 'Unit mismatch'):
            normalize(self.obj, [{'source': 'input', 'target': 'output', 'source_unit': 'C', 'target_unit': 'C'}], 'http', self.raw, 'json')

    def test_collision_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'duplicate target'):
            normalize(self.obj, [{'source': 'input', 'target': 'unknown', 'source_unit': 'F', 'target_unit': 'F'}], 'http', self.raw, 'json')

    def test_three_codecs_have_equivalent_values(self):
        xml = b'<message><message_id>xml-test</message_id><device_id>fixture</device_id><observed_at>2026-10-19T00:00:00Z</observed_at><values><field name="input" type="number" unit="F">95</field><field name="unknown" unit="custom">opaque</field></values></message>'
        self.assertEqual(decode(xml, 'xml')['values'], self.obj['values'])
        self.assertEqual(decode(cbor2.dumps(self.obj), 'cbor'), self.obj)

    def test_timezone_and_nonfinite_rejected(self):
        self.obj['observed_at'] = '2026-10-19T00:00:00'
        with self.assertRaises(ValueError):
            decode(json.dumps(self.obj).encode(), 'json')
        self.obj['observed_at'] += 'Z'
        self.obj['values']['input']['value'] = float('nan')
        with self.assertRaises(ValueError):
            decode(json.dumps(self.obj).encode(), 'json')

    def test_identity_fingerprint_order_independent(self):
        self.assertEqual(fingerprint(self.obj), fingerprint(dict(reversed(list(self.obj.items())))))
        self.obj['values']['input']['value'] = 96
        self.assertNotEqual(fingerprint(self.obj), fingerprint(json.loads(self.raw)))


class PredictorTests(unittest.TestCase):
    def test_lstm_learns_observed_sequence(self):
        model = LSTM(hidden=6)
        data = .5 + .3 * np.sin(np.arange(140) * .12)
        samples = [(data[i-8:i], data[i]) for i in range(8, 105)]
        test = [(data[i-8:i], data[i]) for i in range(105, 140)]
        before = np.mean([(model.predict(x)-y)**2 for x,y in test])
        original = model.w.copy()
        model.train(samples, epochs=20)
        after = np.mean([(model.predict(x)-y)**2 for x,y in test])
        self.assertFalse(np.allclose(model.w, original))
        self.assertLess(after, before * .5)

    def test_scaling_limits_and_capacity(self):
        self.assertEqual(floor_for(0, 20), 1)
        self.assertEqual(floor_for(45, 20), 3)
        self.assertEqual(floor_for(10000, 20), 4)


if __name__ == '__main__':
    unittest.main()
