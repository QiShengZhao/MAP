import asyncio
import ipaddress
import re
import socket
from urllib.parse import urlparse

class GuardrailBlocked(Exception): pass

GuardrailViolation = GuardrailBlocked

DANGEROUS_PATTERNS = [
    r"rm\s+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)\s+/(\s|$)",
    r":\(\)\s*\{.*\};\s*:",
    r"mkfs\.", r"dd\s+if=.*of=/dev/",
    r"shutdown|reboot\b",
    r"curl[^|]*\|\s*(bash|sh)\b",
    r"kubectl\s+(delete|drain)",
]
SECRET_PATTERNS = [
    r"(?i)(api[_-]?key|secret|password|token)\s*[:=]\s*\S{8,}",
    r"-----BEGIN (RSA|EC|OPENSSH) PRIVATE KEY-----",
]
PRIVATE_NETS = ("127.", "10.", "192.168.", "169.254.", "0.0.0.0", "localhost")


def _blocked_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        try:
            ip = ipaddress.ip_address(socket.inet_ntoa(socket.inet_aton(value)))
        except OSError:
            return False
    return any((
        ip.is_private, ip.is_loopback, ip.is_link_local, ip.is_multicast,
        ip.is_reserved, ip.is_unspecified,
    ))


async def _getaddrinfo(host: str, port: int):
    return await asyncio.to_thread(
        socket.getaddrinfo, host, port, type=socket.SOCK_STREAM)

def check_command(cmd: str) -> None:
    for pat in DANGEROUS_PATTERNS:
        if re.search(pat, cmd):
            raise GuardrailBlocked(f"dangerous command pattern: {pat}")
    for pat in SECRET_PATTERNS:
        if re.search(pat, cmd):
            raise GuardrailBlocked("possible secret leakage")


def check_url(url: str, blocked_domains: list | None = None) -> None:
    host = (urlparse(url).hostname or "").lower()
    if not host:
        raise GuardrailBlocked("invalid url")
    if any(host.startswith(p.rstrip(".")) for p in PRIVATE_NETS):
        raise GuardrailBlocked("SSRF: private network access denied")
    if _blocked_ip(host):
        raise GuardrailBlocked("SSRF: private network access denied")
    scheme = (urlparse(url).scheme or "").lower()
    if scheme not in ("http", "https"):
        raise GuardrailBlocked(f"scheme blocked: {scheme}")
    for domain in (blocked_domains or []):
        if host == domain or host.endswith("." + domain):
            raise GuardrailBlocked(f"domain blocked: {domain}")


async def check_url_resolved(
        url: str, blocked_domains: list | None = None) -> None:
    check_url(url, blocked_domains)
    parsed = urlparse(url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        addresses = await _getaddrinfo(parsed.hostname or "", port)
    except socket.gaierror as exc:
        raise GuardrailBlocked(f"DNS resolution failed: {exc}") from exc
    for result in addresses:
        address = result[4][0]
        if _blocked_ip(address):
            raise GuardrailBlocked(
                f"SSRF: hostname resolved to private address {address}")


def scan_secrets(text: str) -> list[dict]:
    findings = []
    if re.search(r"sk-proj-[A-Za-z0-9]{20,}", text):
        findings.append({"kind": "openai_key"})
    if re.search(r"AKIA[0-9A-Z]{16}", text):
        findings.append({"kind": "aws_access_key"})
    for pat in SECRET_PATTERNS:
        if re.search(pat, text):
            findings.append({"kind": "secret_pattern"})
    return findings


class Guardrails:
    @staticmethod
    async def check_tool_call(policy, call):
        if call.name == "run_command":
            check_command(call.args.get("command", ""))
        if call.name in ("web_fetch", "browser_visit", "browser_screenshot"):
            await check_url_resolved(
                call.args.get("url", ""), policy.blocked_domains or [])
