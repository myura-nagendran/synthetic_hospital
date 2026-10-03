"""Offline source regression; SQL expressions are checked by an in-memory adapter."""
import ast
import asyncio
import json
import sqlite3
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]


class Column:
    def __init__(self, name):
        self.name = name
    def __eq__(self, value):
        return lambda row: getattr(row, self.name) == value
    def in_(self, values):
        return lambda row: getattr(row, self.name) in values
    def desc(self):
        return self.name


class Query:
    def __init__(self, table):
        self.table, self.filters, self.order, self.maximum = table, [], [], None
    def where(self, predicate):
        self.filters.append(predicate)
        return self
    def order_by(self, *columns):
        self.order.extend(columns)
        return self
    def limit(self, maximum):
        self.maximum = maximum
        return self


class Result:
    def __init__(self, rows):
        self.rows = rows
    def scalars(self):
        return self
    def all(self):
        return self.rows


class Model(dict):
    pass


def load_source():
    path = ROOT / 'epic_sim/app/services/epic_service.py'
    nodes = [ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)]
    nodes += [n for n in ast.parse(path.read_text()).body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'get_patient_summary']
    encounters = SimpleNamespace(**{k: Column(k) for k in ('patient_id', 'encounter_id', 'encounter_date', 'encounter_order')})
    sections = SimpleNamespace(**{k: Column(k) for k in ('encounter_id', 'section_type')})
    async def problems(*args):
        return []
    namespace = {'select': Query, 'LongitudinalEncounter': encounters, 'EncounterEhrSection': sections, 'LongitudinalPatient': object(),
                 'get_allowed_sections': lambda role: None, 'get_problem_list': problems, 'PatientSummary': Model, 'EncounterSummary': Model}
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), str(path), 'exec'), namespace)
    return namespace


class Database:
    def __init__(self, namespace, patient, encounters, sections):
        self.namespace, self.patient, self.encounters, self.sections = namespace, patient, encounters, sections
    async def get(self, *args):
        return self.patient
    async def execute(self, query):
        rows = self.encounters if query.table is self.namespace['LongitudinalEncounter'] else self.sections
        rows = [row for row in rows if all(predicate(row) for predicate in query.filters)]
        if query.order:
            rows = sorted(rows, key=lambda row: tuple(getattr(row, col) for col in query.order), reverse=True)
        return Result(rows[:query.maximum])


class SnapshotRegression(unittest.TestCase):
    def test_selection_respects_cutoff_on_shipped_patient(self):
        ns = load_source()
        with sqlite3.connect(f'{(ROOT / "benchmark_v1.3.db").as_uri()}?mode=ro', uri=True) as conn:
            conn.row_factory = sqlite3.Row
            pid = conn.execute('SELECT patient_id FROM longitudinal_encounters GROUP BY patient_id HAVING COUNT(*)>2 ORDER BY patient_id LIMIT 1').fetchone()[0]
            patient = dict(conn.execute('SELECT * FROM longitudinal_patients WHERE patient_id=?', (pid,)).fetchone())
            patient.update(profile=json.loads(patient['profile']), pcp_name=None)
            encs = [dict(r) for r in conn.execute('SELECT * FROM longitudinal_encounters WHERE patient_id=? ORDER BY encounter_order', (pid,))]
            secs = [SimpleNamespace(**dict(r)) for r in conn.execute('SELECT s.* FROM encounter_ehr_sections s JOIN longitudinal_encounters e USING(encounter_id) WHERE e.patient_id=?', (pid,))]
        for enc in encs:
            enc['encounter_type'] = SimpleNamespace(value=enc['encounter_type'])
        objects = [SimpleNamespace(**e) for e in encs]
        db = Database(ns, SimpleNamespace(**patient), objects, secs)
        for cutoff in objects:
            allowed = {e.encounter_id for e in objects if e.encounter_order <= cutoff.encounter_order}
            snapshot = asyncio.run(ns['get_patient_summary'](db, pid, 'attending', allowed_encounter_ids=allowed))
            self.assertTrue(all(e['encounter_id'] in allowed for e in snapshot['recent_encounters']))
            latest = max((e for e in objects if e.encounter_id in allowed), key=lambda e: (e.encounter_date, e.encounter_order, e.encounter_id))
            for kind in ('medications', 'allergies'):
                expected = []
                for section in secs:
                    if section.encounter_id == latest.encounter_id and section.section_type == kind:
                        expected = [line.strip() for line in section.section_text.split('\n') if line.strip()]
                self.assertEqual(snapshot[kind], expected)
        empty = asyncio.run(ns['get_patient_summary'](db, pid, 'attending', allowed_encounter_ids=set()))
        self.assertEqual(empty['recent_encounters'], [])
        self.assertEqual(empty['medications'], [])
        # Ordinary raw API calls keep their existing unrestricted behaviour.
        raw = asyncio.run(ns['get_patient_summary'](db, pid, 'attending'))
        self.assertTrue(raw['recent_encounters'])

    def test_episode_and_dispatcher_forward_allowed_encounters(self):
        # These AST contracts cover the otherwise easily missed wiring boundary.
        service = ast.parse((ROOT / 'epic_sim/app/services/env_service.py').read_text())
        step = next(n for n in service.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'step')
        dispatch_calls = [n for n in ast.walk(step) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'dispatch']
        self.assertTrue(any(any(k.arg == 'allowed_encounter_ids' for k in n.keywords) for n in dispatch_calls))
        router = ast.parse((ROOT / 'epic_sim/app/routers/agent.py').read_text())
        dispatch = next(n for n in router.body if isinstance(n, ast.AsyncFunctionDef) and n.name == '_dispatch_tool')
        calls = [n for n in ast.walk(dispatch) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == 'get_patient_summary']
        self.assertTrue(any(any(k.arg == 'allowed_encounter_ids' for k in n.keywords) for n in calls))


if __name__ == '__main__':
    unittest.main()
