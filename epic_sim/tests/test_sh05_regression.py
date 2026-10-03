"""Offline regression against the exact service function and shipped sections."""
import ast
import sqlite3
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def strip_function():
    path = ROOT / 'epic_sim/app/services/env_service.py'
    tree = ast.parse(path.read_text())
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'strip_outcome_sections']
    namespace = {'OUTCOME_SECTIONS': {'assessment', 'plan'}}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), namespace)
    return namespace['strip_outcome_sections']


class SingletonOutcomeRegression(unittest.TestCase):
    def test_shipped_outcome_sections_are_not_readable_as_singletons(self):
        strip = strip_function()
        with sqlite3.connect(f'{(ROOT / "benchmark_v1.3.db").as_uri()}?mode=ro', uri=True) as db:
            rows = db.execute("SELECT id,encounter_id,section_type,section_text FROM encounter_ehr_sections WHERE section_type IN ('assessment','plan') ORDER BY id").fetchall()
        self.assertTrue(rows)
        for sid, eid, kind, text in rows:
            with self.subTest(section_id=sid):
                observed = strip({'section_id': sid, 'encounter_id': eid, 'section_type': kind, 'section_text': text})
                self.assertNotIn('section_text', observed)
                self.assertIn('error', observed)

    def test_ordinary_sections_and_existing_list_rule_are_preserved(self):
        strip = strip_function()
        ordinary = {'section_type': 'hpi', 'section_text': 'history'}
        hidden = {'section_type': 'PLAN', 'section_text': 'answer'}
        self.assertEqual(strip({'sections': [ordinary, hidden]}), {'sections': [ordinary]})
        self.assertEqual(strip(ordinary), ordinary)
        self.assertEqual(strip({'assessment': 'answer', 'other': 1}), {'other': 1})


if __name__ == '__main__':
    unittest.main()
