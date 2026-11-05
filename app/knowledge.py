from __future__ import annotations
import re
from dataclasses import dataclass
from pathlib import Path
@dataclass(frozen=True)
class Citation:
    citation_id: str
    knowledge_id: str
    source_path: str
    title: str
    excerpt: str
    def to_dict(self): return self.__dict__.copy()
def search_knowledge(root: Path, knowledge_ids: tuple[str,...], query: str)->list[Citation]:
    terms = {x.lower() for x in re.findall(r'[\w\u4e00-\u9fff]+', query) if len(x) > 1}
    results = []
    for kid in knowledge_ids:
        path = root / 'knowledge' / 'demo_project' / f'{kid}.md'
        if not path.is_file():
            continue
        text = path.read_text(encoding='utf-8')
        lowered = text.lower()
        counts = [lowered.count(term) for term in terms]
        score = (sum(count > 0 for count in counts), sum(counts))
        if score[0] == 0:
            continue
        lines = text.splitlines()
        citation = Citation(f'knowledge-{kid}', kid, path.relative_to(root).as_posix(),
                            lines[0].lstrip('# ') if lines else kid, '\n'.join(lines[:12])[:1200])
        results.append((score, citation))
    # Stable sort preserves registration order when both scores tie.
    results.sort(key=lambda result: result[0], reverse=True)
    return [citation for _, citation in results]
