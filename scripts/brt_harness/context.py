"""Build bounded, base-revision context without importing benchmark code."""

from collections import defaultdict, deque
from dataclasses import asdict, dataclass
import ast
import re

from scripts.test_retrieval.static_dependencies import DependencyIndex


@dataclass(frozen=True)
class Host:
    id: str
    file: str
    reference: str
    class_name: str | None


class TestContext:
    def __init__(self, sources, references, *, max_chars=24000, max_symbols=40,
                 test_patterns=None, ignore_patterns=(), provenance='retrieved'):
        self.sources = sources
        self.max_chars, self.max_symbols = max_chars, max_symbols
        self.index = DependencyIndex(sources, test_patterns=test_patterns,
                                     ignore_patterns=ignore_patterns).build()
        self.diagnostics = self.index.report()
        self.references = []
        self.rejected = []
        self.provenance = provenance
        for ref in references:
            sid = f"{ref['file']}::{ref['name']}"
            if sid in self.index.tests:
                self.references.append(sid)
            else:
                self.rejected.append(sid)
        self.outgoing = defaultdict(list)
        for edge in self.index.edges.values():
            self.outgoing[edge.source].append(edge)

    def hosts(self, issue, limit=2):
        ranked = list(dict.fromkeys(self.references))
        # Explicit fallback when retrieval misses: base tests, never patched tests.
        if not ranked:
            words = set(re.findall(r'[A-Za-z_]\w{2,}', issue.lower()))
            ranked = sorted(self.index.tests, key=lambda sid: (
                -len(words & set(re.findall(r'[A-Za-z_]\w{2,}',
                    (sid + ' ' + self.index.tests[sid]['code_content']).lower()))), sid))
        hosts, seen = [], set()
        for sid in ranked:
            symbol = self.index.symbols[sid]
            parent = self.index.symbols[symbol.parent]
            class_name = parent.name if parent.kind == 'class' else None
            # Nested classes require a runner-specific selector; reject explicitly.
            if class_name and '.' in class_name:
                continue
            key = (symbol.file, class_name)
            if key in seen:
                continue
            seen.add(key)
            hosts.append(Host(sid, symbol.file, symbol.name, class_name))
            if len(hosts) == limit:
                break
        if not hosts:
            raise ValueError('No existing base-revision test can serve as a host')
        return hosts

    def _snippet(self, sid):
        symbol = self.index.symbols[sid]
        lines = self.sources[symbol.file].splitlines()
        if symbol.kind == 'class':
            # Show the declaration without duplicating every sibling test.
            end = symbol.node.body[0].lineno - 1 if symbol.node.body else symbol.end
        else:
            end = symbol.end
        return '\n'.join(lines[symbol.start - 1:end])

    def package(self, host, extra=()):
        if len(self._snippet(host.id)) > self.max_chars:
            raise ValueError('Host reference exceeds --max-context-chars; increase the context budget')
        assertion_examples = [sid for sid in self.references if any(
            isinstance(node, ast.Assert) or isinstance(node, ast.Call) and (
                isinstance(node.func, ast.Attribute) and
                (node.func.attr.startswith('assert') or node.func.attr in {'raises', 'warns'}))
            for node in ast.walk(self.index.symbols[sid].node))][:2]
        queue = deque((sid, 0) for sid in dict.fromkeys(
            [host.id, *extra, *self.references[:3], *assertion_examples]))
        visited, records, omitted = set(), [], []
        used = 0

        def add(label, file, code, role):
            nonlocal used
            if not code or any(record['id'] == label for record in records):
                return
            if used + len(code) > self.max_chars or len(records) >= self.max_symbols:
                omitted.append(label)
                return
            records.append(dict(id=label, file=file, role=role, code=code))
            used += len(code)

        while queue:
            sid, depth = queue.popleft()
            if sid in visited or sid not in self.index.symbols:
                continue
            visited.add(sid)
            symbol = self.index.symbols[sid]
            if symbol.kind in {'module', 'dispatch'}:
                continue
            add(sid, symbol.file, self._snippet(sid),
                'behavior_example' if sid in self.references or sid == host.id else 'execution_dependency')
            root = self.index.symbols[f'{symbol.file}::<module>'].node
            source = self.sources[symbol.file]
            imports = [ast.get_source_segment(source, node) for node in root.body
                       if isinstance(node, (ast.Import, ast.ImportFrom))]
            add(f'{symbol.file}:imports', symbol.file, '\n'.join(imports), 'imports')
            if symbol.context and symbol.context != sid:
                queue.append((symbol.context, depth))
            if depth < 3:
                priority = {'fixture': 0, 'lifecycle': 1, 'reference': 2, 'call': 3}
                for edge in sorted(self.outgoing[sid], key=lambda e: (priority.get(e.kind, 4), e.target)):
                    if edge.confidence == 1.0:
                        queue.append((edge.target, depth + 1))
        return dict(host=asdict(host), roles=dict(execution_host=host.id,
                    behavior_examples=self.references[:3], assertion_examples=assertion_examples),
                    records=records, omitted=omitted,
                    unresolved_calls=[item for item in self.diagnostics['unresolved_attribute_calls']
                                      if item['file'] == host.file][:20],
                    parse_errors=[item for item in self.index.diagnostics if item['file'] == host.file],
                    unavailable_references=self.rejected,
                    retrieval_fallback=not self.references, provenance=self.provenance)

    def feedback_symbols(self, host, feedback, limit=3):
        """Resolve exact missing names; ambiguous definitions remain alternatives."""
        text = feedback.get('diagnostic', '')
        names = set()
        for pattern in (r"name '([^']+)' is not defined", r"fixture '([^']+)' not found",
                        r"cannot import name '([^']+)'", r"has no attribute '([^']+)'"):
            names.update(re.findall(pattern, text))
        found = []
        for name in sorted(names):
            fixture = self.index.fixture_for(host.id, name)
            if fixture:
                found.append(fixture)
                continue
            matches = [sid for sid, symbol in self.index.symbols.items()
                       if symbol.name.split('.')[-1] == name and symbol.kind != 'module']
            matches.sort(key=lambda sid: (self.index.symbols[sid].file != host.file, sid))
            found.extend(matches[:2])
        return list(dict.fromkeys(found))[:limit]
