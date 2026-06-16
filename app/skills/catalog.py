from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class CatalogSkill:
    id: str
    name: str
    description: str
    publisher: str
    category: str
    homepage: str
    source_url: str

    def as_dict(self) -> dict:
        return asdict(self)


CATALOG = (
    CatalogSkill(
        id="openai-playwright",
        name="Playwright",
        description="使用 Playwright 自动化浏览器操作与网页测试。",
        publisher="OpenAI",
        category="开发测试",
        homepage="https://github.com/openai/skills/tree/main/skills/.curated/playwright",
        source_url="https://raw.githubusercontent.com/openai/skills/main/skills/.curated/playwright/SKILL.md",
    ),
    CatalogSkill(
        id="openai-security-best-practices",
        name="Security Best Practices",
        description="按语言和框架检查常见安全风险并提出改进建议。",
        publisher="OpenAI",
        category="安全",
        homepage="https://github.com/openai/skills/tree/main/skills/.curated/security-best-practices",
        source_url="https://raw.githubusercontent.com/openai/skills/main/skills/.curated/security-best-practices/SKILL.md",
    ),
    CatalogSkill(
        id="openai-openai-docs",
        name="OpenAI Docs",
        description="基于 OpenAI 官方文档回答产品与 API 开发问题。",
        publisher="OpenAI",
        category="文档",
        homepage="https://github.com/openai/skills/tree/main/skills/.curated/openai-docs",
        source_url="https://raw.githubusercontent.com/openai/skills/main/skills/.curated/openai-docs/SKILL.md",
    ),
    CatalogSkill(
        id="anthropic-pdf",
        name="PDF",
        description="读取、创建和处理 PDF 文档。",
        publisher="Anthropic",
        category="文档",
        homepage="https://github.com/anthropics/skills/tree/main/skills/pdf",
        source_url="https://raw.githubusercontent.com/anthropics/skills/main/skills/pdf/SKILL.md",
    ),
    CatalogSkill(
        id="anthropic-docx",
        name="DOCX",
        description="创建、编辑和分析 Word 文档。",
        publisher="Anthropic",
        category="文档",
        homepage="https://github.com/anthropics/skills/tree/main/skills/docx",
        source_url="https://raw.githubusercontent.com/anthropics/skills/main/skills/docx/SKILL.md",
    ),
    CatalogSkill(
        id="anthropic-pptx",
        name="PPTX",
        description="创建、编辑和分析演示文稿。",
        publisher="Anthropic",
        category="文档",
        homepage="https://github.com/anthropics/skills/tree/main/skills/pptx",
        source_url="https://raw.githubusercontent.com/anthropics/skills/main/skills/pptx/SKILL.md",
    ),
    CatalogSkill(
        id="anthropic-xlsx",
        name="XLSX",
        description="创建、编辑和分析电子表格。",
        publisher="Anthropic",
        category="数据",
        homepage="https://github.com/anthropics/skills/tree/main/skills/xlsx",
        source_url="https://raw.githubusercontent.com/anthropics/skills/main/skills/xlsx/SKILL.md",
    ),
)


def get_catalog_skill(skill_id: str) -> CatalogSkill | None:
    return next((skill for skill in CATALOG if skill.id == skill_id), None)
