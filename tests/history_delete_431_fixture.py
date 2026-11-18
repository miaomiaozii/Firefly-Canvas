"""Produce frontend regression snapshots directly from the real project store."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_history_delete_431 import OriginalHistory431

case = OriginalHistory431()
case.setUp()
try:
    before = case.canvas()
    occurrence = case.store.delete_generated_original(case.pid, case.a, base_revision=before['revision'],
        history_id='one', output_id=case.a1['output_id'], output_index=0)
    restored = case.store.restore_deleted_original(case.pid, case.a, base_revision=occurrence['canvas']['revision'],
        deletion_id=occurrence['deleted']['deletion_id'])
    wide = case.store.delete_generated_original_project_wide(case.pid, case.a, base_revision=restored['canvas']['revision'])
    wide_restored = case.store.restore_deleted_original_project_wide(case.pid, case.a, base_revision=wide['canvas']['revision'])
    history_deleted = case.store.delete_history_record(case.pid, 'one', base_revision=wide_restored['canvas']['revision'])
    print(json.dumps({'before': before, 'occurrence': occurrence, 'restored': restored,
                      'wide': wide, 'wide_restored': wide_restored,
                      'history_deleted': {'ok': True, 'canvas': history_deleted}, 'asset': case.a}))
finally:
    case.doCleanups()
