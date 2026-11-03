from app.knowledge import search_knowledge
from app.runner import code_version
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def test_knowledge_returns_citation():
    results=search_knowledge(ROOT,('business_rules','testing'),'闰年 测试')
    assert results and results[0].citation_id.startswith('knowledge-')
def test_code_version_is_stable():
    assert code_version(ROOT,('task_assets/leap_legacy/code/leap_year.py',)).startswith('sha256:')
