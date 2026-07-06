#!/usr/bin/env python3
from collections import defaultdict
from datetime import date
from pathlib import Path
import re
import subprocess

ROOT = Path('/srv/hermes-memory/vault/Personal Anton')
TRAVEL = ROOT / 'Knowledge' / 'travel' / 'research'
DIR_ACL = 'u:hermes:r-x,u:hermes-backup:r-x,u:hermes-promoter:rwx'
FILE_ACL = 'u:hermes:r--,u:hermes-backup:r--,u:hermes-promoter:rw-'


def heading(text, fallback):
    for line in text.splitlines():
        if line.startswith('# '):
            return line[2:].strip()
    return fallback


def place_location(text):
    for line in text.splitlines():
        if line.startswith('📍 **') and line.endswith('**'):
            return line[4:-2].strip('* ').strip()
    return ''


def place_relations(text):
    if '## Связано' not in text:
        return []
    block = text.split('## Связано', 1)[1]
    if '\n## ' in block:
        block = block.split('\n## ', 1)[0]
    return re.findall(r'^- \[\[([^]]+)\]\]$', block, flags=re.M)


def apply_directory_acl(path):
    path.chmod(0o2770)
    subprocess.run(['setfacl', '-m', DIR_ACL, str(path)], check=True)
    subprocess.run(['setfacl', '-d', '-m', DIR_ACL, str(path)], check=True)


def apply_file_acl(path):
    path.chmod(0o660)
    subprocess.run(['setfacl', '-m', FILE_ACL, str(path)], check=True)


def write_page(relative, text):
    path = ROOT / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    apply_directory_acl(path.parent)
    path.write_text(text.rstrip() + '\n', encoding='utf-8')
    apply_file_acl(path)


def build_travel():
    groups = defaultdict(list)
    skip = {'Поездки и места', 'Москва', 'Московская область', 'Отдых с детьми'}
    for path in sorted(TRAVEL.glob('*.md')):
        text = path.read_text(encoding='utf-8', errors='ignore')
        if 'entity_type: "travel_place"' not in text:
            continue
        name = heading(text, path.stem)
        location = place_location(text)
        links = place_relations(text)
        category = next((item for item in links if item not in skip), 'Другие места')
        target = path.relative_to(ROOT).with_suffix('').as_posix()
        groups[category].append((name, location, target))
    lines = [
        '---', 'type: "index"', 'knowledge_project: "travel"',
        'entity_type: "travel_index"', f'updated: "{date.today().isoformat()}"',
        'graph: true', '---', '', '# Все места', '',
        'Здесь собраны актуальные карточки мест. Новые источники дополняют карточку, а не создают копию.', '',
    ]
    count = 0
    for category in sorted(groups):
        lines.extend([f'## {category}', ''])
        for name, location, target in sorted(groups[category]):
            suffix = f' — {location}' if location else ''
            lines.append(f'- [[{target}|{name}]]{suffix}')
            count += 1
        lines.append('')
    lines.extend(['## Быстрые подборки', '', '- [[Московская область]]', '- [[Москва]]', '- [[Отдых с детьми]]', '- [[Пляжи]]', '- [[Верёвочные парки]]'])
    write_page('Travel/Все места.md', '\n'.join(lines))
    return count


def file_index(folder, title, intro, output):
    base = ROOT / folder
    lines = ['---', 'type: "index"', f'entity_type: "{folder.lower()}_index"', f'updated: "{date.today().isoformat()}"', 'graph: true', '---', '', f'# {title}', '', intro, '']
    for path in sorted(base.glob('*.md')):
        if path.name == Path(output).name:
            continue
        text = path.read_text(encoding='utf-8', errors='ignore')
        name = heading(text, path.stem)
        target = path.relative_to(ROOT).with_suffix('').as_posix()
        lines.append(f'- [[{target}|{name}]]')
    write_page(output, '\n'.join(lines))


def build_main(place_count):
    text = f'''---
type: "index"
entity_type: "home_index"
updated: "{date.today().isoformat()}"
graph: true
---

# Главная

Быстрый вход в личную базу. В обычной работе начинайте отсюда, а технический раздел `Knowledge` используйте только для истории, доказательств и служебных материалов Hermes.

## Основные разделы

- [[Travel/Все места|Поездки и места]] — {place_count} карточек
- [[Projects/Все проекты|Проекты]]
- [[Systems/Все системы|Hermes и системы]]
- [[Work/Работа и карьера|Работа и карьера]]
- [[Family/Family|Семья]]
- [[Anton/Profile|Профиль]]
- [[Anton/Preferences|Предпочтения]]
- [[Inbox/Needs-review|На разбор]]
- [[00-start/INDEX|Карта задач Hermes]]

## Как устроена база

- Человеческие страницы и индексы находятся в глобальных разделах выше
- Перед рабочей задачей Hermes читает `00-start/INDEX`, затем один файл из `views`
- Единственные актуальные записи Hermes хранятся в `Knowledge` и открываются по canonical-ссылкам
- Решения и доказательства не копируются в проектные страницы, а связываются ссылками
- Изменяемые сущности, например места и проекты, обновляются; исторические решения и отчёты добавляются отдельными записями
'''
    write_page('00 Главная.md', text)


def main():
    count = build_travel()
    file_index('Projects', 'Все проекты', 'Активные направления и их текущий статус.', 'Projects/Все проекты.md')
    file_index('Systems', 'Все системы', 'Текущее устройство Hermes, памяти и интеграций.', 'Systems/Все системы.md')
    work = '''---
type: "index"
entity_type: "work_index"
graph: true
---

# Работа и карьера

## Текущее

- [[Anton/Career|Карьера и опыт]]
- [[Projects/Job Search 2026|Поиск работы 2026]]
- [[Head of Recruitment|Head of Recruitment]]

## История

- [[Archive/Job-Search-June-2026-Next-Steps|Предыдущий этап поиска]]
'''
    write_page('Work/Работа и карьера.md', work)
    build_main(count)
    print({'status': 'ok', 'places': count, 'pages': 5})


if __name__ == '__main__':
    main()
