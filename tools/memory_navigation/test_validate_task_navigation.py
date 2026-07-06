from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name('validate_task_navigation.py')
SPEC = importlib.util.spec_from_file_location('validate_task_navigation', SCRIPT)
NAV = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = NAV
SPEC.loader.exec_module(NAV)


class NavigationValidatorTests(unittest.TestCase):
    def make_tree(self, root: Path, *, broken: bool = False, conflict: bool = False) -> None:
        personal = root / 'Personal Anton'
        (personal / '00-start').mkdir(parents=True)
        (personal / 'views').mkdir(parents=True)
        (personal / 'Systems').mkdir(parents=True)
        (personal / 'Systems' / 'Hermes.md').write_text('# Hermes\n', encoding='utf-8')
        view_links = []
        for name in NAV.VIEW_NAMES:
            target = 'Personal Anton/Systems/Missing' if broken and name == NAV.VIEW_NAMES[0] else 'Personal Anton/Systems/Hermes'
            key = 'test.shared' if conflict and name in NAV.VIEW_NAMES[:2] else 'test.' + name[:-3]
            if conflict and name == NAV.VIEW_NAMES[1]:
                (personal / 'Systems' / 'Other.md').write_text('# Other\n', encoding='utf-8')
                target = 'Personal Anton/Systems/Other'
            (personal / 'views' / name).write_text(self.view_text(target, key), encoding='utf-8')
            view_links.append(f'[[Personal Anton/views/{name[:-3]}]]')
        (personal / '00-start' / 'INDEX.md').write_text(self.index_text(view_links), encoding='utf-8')

    @staticmethod
    def fm(entity: str) -> str:
        return f'---\nentity_type: "{entity}"\nlast_verified: "2026-07-06"\n---\n\n'

    def view_text(self, target: str, key: str) -> str:
        sections = [
            '# View',
            '## Purpose\nRoute.',
            f'## Start here\n- [[{target}]]',
            f'## Canonical files\n- [[{target}]] <!-- canonical:{key} -->',
            '## Common task routes\nRoute.',
            '## Related components\nComponents.',
            '## Archived or non-canonical materials\nHistory only.',
            '## Last verified\n2026-07-06.',
        ]
        return self.fm('task_view') + '\n\n'.join(sections) + '\n'

    def index_text(self, links: list[str]) -> str:
        sections = [
            '# Index',
            '## Current work\n- [[Personal Anton/Systems/Hermes]]',
            '## Start here\n' + '\n'.join('- ' + item for item in links),
            '## Canonical files\n- [[Personal Anton/Systems/Hermes]] <!-- canonical:test.index -->',
            '## Common task routes\nRoute.',
            '## History and archive\nHistory.',
            '## Last verified\n2026-07-06.',
        ]
        return self.fm('navigation_index') + '\n\n'.join(sections) + '\n'

    def test_valid_navigation_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_tree(root)
            result = NAV.validate(root)
            self.assertEqual(result['status'], 'ok')
            self.assertEqual(result['errors'], [])

    def test_broken_link_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_tree(root, broken=True)
            result = NAV.validate(root)
            self.assertEqual(result['status'], 'failed')
            self.assertTrue(any(item['code'] == 'broken_link' for item in result['errors']))

    def test_canonical_conflict_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_tree(root, conflict=True)
            result = NAV.validate(root)
            self.assertEqual(result['status'], 'failed')
            self.assertTrue(any(item['code'] == 'canonical_conflict' for item in result['errors']))


if __name__ == '__main__':
    unittest.main(verbosity=2)
