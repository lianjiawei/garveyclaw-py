from __future__ import annotations



import logging

import re

from dataclasses import dataclass

from pathlib import Path



from weclaw.config import SKILLS_DIR



logger = logging.getLogger(__name__)





@dataclass(frozen=True)

class SkillDefinition:

    """描述一个可以注入到 Agent 的本地 skill。"""



    name: str

    title: str

    description: str

    file_name: str

    keywords: tuple[str, ...]

    aliases: tuple[str, ...] = ()



    @property

    def file_path(self) -> Path:

        return SKILLS_DIR / self.file_name





# ---------------------------------------------------------------------------

# Frontmatter parser

# ---------------------------------------------------------------------------



_FRONTMATTER_RE = re.compile(r'^---\s*\n(.*?)\n---', re.DOTALL)





def _parse_frontmatter(content: str) -> dict[str, object]:

    match = _FRONTMATTER_RE.match(content)

    if not match:

        return {}



    raw = match.group(1)

    result: dict[str, object] = {}

    lines = raw.split('\n')

    i = 0

    while i < len(lines):

        line = lines[i].strip()

        if not line or ':' not in line:

            i += 1

            continue

        key, _, value = line.partition(':')

        key = key.strip()

        value = value.strip()



        # Multi-line block scalar (| or >)

        if value in ('|', '>', '|-', '>-', '|+', '>+'):

            body_lines: list[str] = []

            i += 1

            while i < len(lines):

                next_line = lines[i]

                if next_line and not next_line[0].isspace():

                    break

                body_lines.append(next_line.strip())

                i += 1

            result[key] = '\n'.join(body_lines).strip()

            continue



        if value.startswith('[') and value.endswith(']'):

            inner = value[1:-1]

            items = [

                item.strip().strip("'").strip('"')

                for item in inner.split(',')

                if item.strip()

            ]

            result[key] = items

        elif ((value.startswith('"') and value.endswith('"')) or
             (value.startswith("'") and value.endswith("'"))):
            result[key] = value[1:-1]

        elif value.lower() in ('true', 'yes'):

            result[key] = True

        elif value.lower() in ('false', 'no'):

            result[key] = False

        elif value.lower() in ('null', '~'):

            result[key] = None

        else:

            result[key] = value



        i += 1



    return result





def _get_body(content: str) -> str:

    match = _FRONTMATTER_RE.match(content)

    if not match:

        return content.strip()

    return content[match.end():].strip()





# ---------------------------------------------------------------------------

# Skill loader with mtime-based hot-reload cache

# ---------------------------------------------------------------------------



class SkillLoader:

    """扫描 SKILLS_DIR 目录，自动发现 skill 定义并缓存。"""



    def __init__(self, skills_dir: Path):

        self._skills_dir = skills_dir

        self._cache: dict[str, tuple[float, SkillDefinition, str]] = {}



    # ---- internal helpers ------------------------------------------------



    def _scan_files(self) -> list[Path]:

        try:

            return sorted(

                [p for p in self._skills_dir.glob('**/*.md') if p.is_file()],

                key=lambda p: p.name,

            )

        except OSError:

            return []



    @staticmethod

    def _first_str(value: object, default: str = '') -> str:

        if isinstance(value, list) and value:

            return str(value[0])

        if isinstance(value, str):

            return value

        return default



    @staticmethod

    def _as_tuple(value: object) -> tuple[str, ...]:

        if isinstance(value, (list, tuple)):

            return tuple(str(v) for v in value)

        if isinstance(value, str):

            items = [v.strip() for v in value.split(',') if v.strip()]

            return tuple(items)

        return ()



    def _cache_key(self, file_path: Path) -> str:

        """Use relative path as cache key to avoid filename collisions across subdirectories."""

        try:

            return str(file_path.relative_to(self._skills_dir))

        except ValueError:

            return file_path.name



    def _load_file(self, file_path: Path) -> tuple[SkillDefinition, str] | None:

        try:

            raw = file_path.read_text(encoding='utf-8')

        except Exception:

            return None



        fm = _parse_frontmatter(raw)



        # 没有 frontmatter name 的不是 skill 定义（如模板文件）

        if 'name' not in fm:

            return None



        body = _get_body(raw)



        name = self._first_str(fm.get('name', ''), file_path.stem)

        title = self._first_str(fm.get('title', ''), file_path.stem)

        description = self._first_str(fm.get('description', ''), '')

        keywords = self._as_tuple(fm.get('keywords', ()))

        aliases = self._as_tuple(fm.get('aliases', ()))



        return SkillDefinition(

            name=name,

            title=title,

            description=description,

            file_name=str(file_path.relative_to(self._skills_dir)),

            keywords=keywords,

            aliases=aliases,

        ), body



    # ---- public API ------------------------------------------------------



    def get_all(self) -> list[SkillDefinition]:

        definitions: list[SkillDefinition] = []

        for file_path in self._scan_files():

            mtime = file_path.stat().st_mtime

            key = self._cache_key(file_path)

            if key in self._cache and self._cache[key][0] >= mtime:

                definitions.append(self._cache[key][1])

                continue

            loaded = self._load_file(file_path)

            if loaded is not None:

                definition, body = loaded

                self._cache[key] = (mtime, definition, body)

                definitions.append(definition)

        return definitions



    def get_skill(self, name: str) -> SkillDefinition | None:

        normalized = name.strip().lower()

        for skill in self.get_all():

            if normalized == skill.name.lower() or normalized in (a.lower() for a in skill.aliases):

                return skill

        return None



    def get_body(self, skill: SkillDefinition) -> str:

        key = self._cache_key(skill.file_path)

        if key in self._cache:

            return self._cache[key][2]

        raw = skill.file_path.read_text(encoding='utf-8')

        return _get_body(raw)





# ---------------------------------------------------------------------------

# 全局单例

# ---------------------------------------------------------------------------



_loader = SkillLoader(SKILLS_DIR)

_last_matched_skills: list[SkillDefinition] = []





# ---------------------------------------------------------------------------

# 对外 API（保持原有签名不变）

# ---------------------------------------------------------------------------



def list_skills() -> list[SkillDefinition]:

    return _loader.get_all()





def get_skill(name: str) -> SkillDefinition | None:

    return _loader.get_skill(name)





def get_body(skill: SkillDefinition) -> str:

    return _loader.get_body(skill)





def get_skills_by_names(names: list[str] | tuple[str, ...]) -> list[SkillDefinition]:

    selected: list[SkillDefinition] = []

    seen: set[str] = set()

    for name in names:

        skill = get_skill(str(name).strip())

        if skill is None:

            continue

        normalized = skill.name.lower()

        if normalized in seen:

            continue

        selected.append(skill)

        seen.add(normalized)

    return selected





def select_skills(prompt: str, max_skills: int = 3) -> list[SkillDefinition]:

    text = prompt.lower()

    selected: list[SkillDefinition] = []



    explicit_names = {part[1:] for part in text.split() if part.startswith('#')}

    for explicit_name in explicit_names:

        skill = get_skill(explicit_name)

        if skill and skill not in selected:

            selected.append(skill)



    if len(selected) >= max_skills:

        return selected[:max_skills]



    all_skills = list_skills()

    scored: list[tuple[int, SkillDefinition]] = []

    for skill in all_skills:

        if skill in selected:

            continue

        score = sum(1 for keyword in skill.keywords if keyword.lower() in text)

        score += sum(1 for alias in skill.aliases if alias.lower() in text)

        if score > 0:

            scored.append((score, skill))



    scored.sort(key=lambda item: item[0], reverse=True)

    for _, skill in scored:

        if len(selected) >= max_skills:

            break

        selected.append(skill)



    return selected





def build_skill_prompt(prompt: str, selected_names: list[str] | tuple[str, ...] | None = None) -> tuple[list[SkillDefinition], str]:

    global _last_matched_skills

    selected_skills = get_skills_by_names(selected_names or ()) if selected_names else select_skills(prompt)

    _last_matched_skills = selected_skills

    if not selected_skills:

        return [], ''



    parts: list[str] = ['下面是本轮问题匹配到的 skill，请优先遵循这份专长说明：']

    for skill in selected_skills:

        if not skill.file_path.exists():

            continue

        content = _loader.get_body(skill)

        parts.append(f'\n[Skill: {skill.name} | {skill.title}]\n{content}')



    return selected_skills, '\n'.join(parts).strip()





def get_last_matched_skills() -> list[SkillDefinition]:

    return list(_last_matched_skills)





# ---------------------------------------------------------------------------

# Skill validation

# ---------------------------------------------------------------------------



_VALID_NAME_RE = re.compile(r'^[a-z][a-z0-9_]*$')





def validate_skills() -> list[str]:

    """验证所有 skill 文件的结构完整性，返回警告/错误列表。"""

    issues: list[str] = []

    seen_names: dict[str, str] = {}



    for file_path in _loader._scan_files():

        rel = str(file_path.relative_to(SKILLS_DIR))

        try:

            raw = file_path.read_text(encoding='utf-8')

        except Exception as exc:

            issues.append(f"[ERROR] {rel}: 无法读取文件 ({exc})")

            continue



        fm = _parse_frontmatter(raw)



        if 'name' not in fm:

            continue



        name = _loader._first_str(fm.get('name', ''), file_path.stem)

        description = _loader._first_str(fm.get('description', ''), '')

        keywords = _loader._as_tuple(fm.get('keywords', ()))

        body = _get_body(raw)



        if not name:

            issues.append(f"[ERROR] {rel}: name 为空")

        elif not _VALID_NAME_RE.match(name):

            issues.append(f"[WARN] {rel}: name '{name}' 建议只用小写字母、数字和下划线")



        if not description:

            issues.append(f"[WARN] {rel}: description 为空，技能列表中将无描述")



        if not body:

            issues.append(f"[ERROR] {rel}: 技能正文为空（只有 frontmatter，没有内容）")



        if name in seen_names:

            issues.append(f"[ERROR] {rel}: name '{name}' 与 {seen_names[name]} 重复")

        else:

            seen_names[name] = rel



        if not keywords:

            issues.append(f"[WARN] {rel}: keywords 为空，技能将无法通过关键词自动命中")



    return issues
