from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
import yaml

from app.runtime.guardrails import GuardrailBlocked, check_url_resolved

MAX_SKILL_BYTES = 256 * 1024
ALLOWED_CONTENT_TYPES = (
    "text/plain",
    "text/markdown",
    "application/octet-stream",
)


class SkillImportError(ValueError):
    pass


@dataclass(frozen=True)
class ImportedSkill:
    name: str
    description: str
    instructions: str
    source_url: str = ""

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "instructions": self.instructions,
            "source_url": self.source_url,
        }


def parse_skill_markdown(text: str, source_url: str = "") -> ImportedSkill:
    body = text.strip()
    metadata = {}
    if body.startswith("---"):
        parts = body.split("---", 2)
        if len(parts) == 3:
            try:
                metadata = yaml.safe_load(parts[1]) or {}
            except yaml.YAMLError as exc:
                raise SkillImportError("SKILL.md frontmatter 格式无效") from exc
            body = parts[2].strip()
    if not body:
        raise SkillImportError("SKILL.md 内容为空")
    fallback = urlparse(source_url).path.rstrip("/").split("/")[-2:-1]
    name = str(metadata.get("name") or (fallback[0] if fallback else "imported-skill")).strip()
    description = str(metadata.get("description") or "").strip()
    if not name:
        raise SkillImportError("Skill 名称为空")
    return ImportedSkill(name, description, body, source_url)


async def fetch_skill(url: str) -> ImportedSkill:
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https":
        raise SkillImportError("仅允许使用 HTTPS 地址")
    try:
        await check_url_resolved(url)
    except GuardrailBlocked as exc:
        raise SkillImportError(str(exc)) from exc

    try:
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=httpx.Timeout(15),
        ) as client:
            response = await client.get(url, headers={"Accept": "text/markdown,text/plain"})
    except httpx.HTTPError as exc:
        raise SkillImportError(f"读取 Skill 失败: {exc}") from exc

    if 300 <= response.status_code < 400:
        raise SkillImportError("不允许远程地址重定向")
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise SkillImportError(f"远程地址返回 HTTP {response.status_code}") from exc
    if len(response.content) > MAX_SKILL_BYTES:
        raise SkillImportError("SKILL.md 超过 256 KiB 限制")
    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
    if content_type and content_type not in ALLOWED_CONTENT_TYPES:
        raise SkillImportError(f"不支持的内容类型: {content_type}")
    try:
        text = response.content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SkillImportError("SKILL.md 必须是 UTF-8 文本") from exc
    return parse_skill_markdown(text, url)
