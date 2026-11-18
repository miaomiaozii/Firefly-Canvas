"""Real store snapshots for a copied reference saved immediately before wide deletion."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_history_delete_431 import OriginalHistory431

case = OriginalHistory431()
case.setUp()
try:
    before = case.canvas()
    node = next(n for n in before['nodes'] if n['id'] == 'other-node').copy()
    node['images'] = [*node['images'], case.a1]
    saved = case.store.apply_mutations(case.pid, [{'op': 'upsert_node', 'node': node}],
                                       base_revision=before['revision'])
    after_save = case.canvas()
    deleted = case.store.delete_generated_original_project_wide(case.pid, case.a,
                                                                base_revision=after_save['revision'])
    print(json.dumps({'before': before, 'after_save': after_save, 'deleted': deleted,
                      'asset': case.a, 'copied': case.a1, 'saved': saved}))
finally:
    case.doCleanups()
