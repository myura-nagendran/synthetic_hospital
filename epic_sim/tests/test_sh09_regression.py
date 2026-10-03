"""Offline pre-read policy regression; no inference about study-level linkage."""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def policy():
    path = ROOT / 'epic_sim/app/services/env_service.py'
    nodes = [n for n in ast.parse(path.read_text()).body if isinstance(n, ast.FunctionDef) and n.name == 'filter_ordering_encounter_imaging']
    namespace = {}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), namespace)
    return namespace['filter_ordering_encounter_imaging']


class PreReadPolicyRegression(unittest.TestCase):
    def test_every_retrieval_shape_respects_encounter_policy(self):
        filter_result = policy()
        current = {'encounter_id': 20, 'section_type': 'imaging', 'section_text': 'ordering encounter report'}
        earlier = {'encounter_id': 10, 'section_type': 'imaging', 'section_text': 'earlier report'}
        lab = {'encounter_id': 20, 'section_type': 'labs', 'section_text': 'lab context'}
        self.assertIn('error', filter_result(current, 20))
        self.assertEqual(filter_result({'encounter_id': 20, 'sections': [current, earlier, lab]}, 20), {'encounter_id': 20, 'sections': [earlier, lab]})
        self.assertEqual(filter_result([current, earlier, lab], 20), [earlier, lab])
        untyped = {'encounter_id': 20, 'section_text': 'result text', 'has_order': True}
        self.assertEqual(filter_result([untyped], 20, untyped_imaging=True), [])
        self.assertEqual(filter_result([untyped], 20), [untyped])
        self.assertEqual(filter_result(earlier, 20), earlier)

    def test_flag_defaults_to_excluded_and_is_snapshotted(self):
        config = ast.parse((ROOT / 'epic_sim/app/config.py').read_text())
        setting = next(n for n in ast.walk(config) if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.target.id == 'pre_read_include_ordering_encounter_imaging')
        self.assertEqual(ast.literal_eval(setting.value), False)
        service = (ROOT / 'epic_sim/app/services/env_service.py').read_text()
        self.assertIn('"include_ordering_encounter_imaging": settings.pre_read_include_ordering_encounter_imaging', service)
        self.assertIn('not ep.get("include_ordering_encounter_imaging", False)', service)
        self.assertIn('(arguments or {}).get("result_type") == "imaging"', service)


if __name__ == '__main__':
    unittest.main()
