import pytest


async def _admin_headers(client):
    credentials = {
        "email": "skills@example.com",
        "password": "Str0ng!Passw0rd",
    }
    await client.post("/v1/auth/register", json={
        **credentials,
        "tenant_name": "skills",
    })
    response = await client.post("/v1/auth/login", json=credentials)
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_parse_skill_markdown_reads_frontmatter():
    from app.skills.importer import parse_skill_markdown

    parsed = parse_skill_markdown(
        "---\n"
        "name: browser-testing\n"
        "description: Test web applications safely\n"
        "---\n\n"
        "# Browser testing\n\nUse Playwright."
    )

    assert parsed.name == "browser-testing"
    assert parsed.description == "Test web applications safely"
    assert parsed.instructions.startswith("# Browser testing")


@pytest.mark.asyncio
async def test_fetch_skill_rejects_non_https_urls():
    from app.skills.importer import SkillImportError, fetch_skill

    with pytest.raises(SkillImportError, match="HTTPS"):
        await fetch_skill("http://example.com/SKILL.md")


async def test_catalog_lists_official_skills(client):
    headers = await _admin_headers(client)

    response = await client.get("/v1/skills/catalog", headers=headers)

    assert response.status_code == 200
    entries = response.json()
    assert any(item["publisher"] == "OpenAI" for item in entries)
    assert any(item["publisher"] == "Anthropic" for item in entries)
    assert all(item["source_url"].startswith("https://") for item in entries)


async def test_preview_and_install_catalog_skill(client, monkeypatch):
    from app.skills.importer import ImportedSkill
    from app.api import routes_skill

    headers = await _admin_headers(client)

    async def fake_fetch(url):
        return ImportedSkill(
            name="playwright",
            description="Browser automation",
            instructions="# Playwright\n\nAutomate the browser.",
            source_url=url,
        )

    monkeypatch.setattr(routes_skill, "fetch_skill", fake_fetch)

    preview = await client.post(
        "/v1/skills/import/preview",
        headers=headers,
        json={"catalog_id": "openai-playwright"},
    )
    assert preview.status_code == 200
    assert preview.json()["name"] == "playwright"

    installed = await client.post(
        "/v1/skills/import",
        headers=headers,
        json={"catalog_id": "openai-playwright"},
    )
    assert installed.status_code == 201

    duplicate = await client.post(
        "/v1/skills/import",
        headers=headers,
        json={"catalog_id": "openai-playwright"},
    )
    assert duplicate.status_code == 409

    skills = (await client.get("/v1/skills", headers=headers)).json()
    assert skills[0]["description"] == "Browser automation"
    assert skills[0]["instructions"].startswith("# Playwright")
