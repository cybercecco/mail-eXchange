"""Read/write DKIM key material for cluster sync and local DNS hints."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from app import db as db_module

OPENDKIM_KEYS_DIR = Path(os.environ.get("OPENDKIM_KEYS_DIR", "/etc/opendkim/keys"))


def _dkim_pub_dir() -> Path:
    return db_module.GENERATED_DIR / "dkim"


def _normalize_domain(domain: str) -> str:
    return domain.strip().lower()


def _normalize_selector(selector: str) -> str:
    return (selector or "mail").strip()


def private_key_path(domain: str, selector: str) -> Path:
    name = _normalize_domain(domain)
    sel = _normalize_selector(selector)
    return OPENDKIM_KEYS_DIR / name / f"{sel}.private"


def read_dkim_private_key_pem(domain: str, selector: str) -> str | None:
    path = private_key_path(domain, selector)
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8").strip()
    return text or None


def read_dkim_public_key_base64(domain: str) -> str | None:
    path = _dkim_pub_dir() / f"{_normalize_domain(domain)}.pub"
    if not path.is_file():
        return None
    cleaned = re.sub(r"\s+", "", path.read_text(encoding="utf-8"))
    return cleaned or None


def format_dkim_dns_txt(pubkey_base64: str) -> str:
    return f"v=DKIM1; k=rsa; p={pubkey_base64}"


def parse_dkim_public_key_from_dns_txt(txt: str) -> str | None:
    compact = re.sub(r"\s+", "", txt.strip())
    match = re.search(r"p=([A-Za-z0-9+/=]+)", compact)
    return match.group(1) if match else None


def install_dkim_key_pair(
    domain: str,
    selector: str,
    private_pem: str,
    public_dns_txt: str | None = None,
) -> None:
    """Install private key for OpenDKIM and public key for DNS hints."""
    name = _normalize_domain(domain)
    sel = _normalize_selector(selector)
    key_dir = OPENDKIM_KEYS_DIR / name
    key_dir.mkdir(parents=True, exist_ok=True)
    private_path = key_dir / f"{sel}.private"
    private_path.write_text(private_pem.strip() + "\n", encoding="utf-8")
    try:
        private_path.chmod(0o600)
    except OSError:
        pass

    pubkey = None
    if public_dns_txt:
        pubkey = parse_dkim_public_key_from_dns_txt(public_dns_txt)
    if not pubkey:
        pubkey = _extract_public_key_from_private_pem(private_pem)
    if pubkey:
        pub_dir = _dkim_pub_dir()
        pub_dir.mkdir(parents=True, exist_ok=True)
        (pub_dir / f"{name}.pub").write_text(pubkey, encoding="utf-8")


def regenerate_dkim_key_pair(domain: str, selector: str) -> tuple[str, str]:
    """Generate a new RSA key pair; returns (private_pem, dkim_dns_txt)."""
    name = _normalize_domain(domain)
    sel = _normalize_selector(selector)
    proc = subprocess.run(
        ["openssl", "genrsa", "-f4", "2048"],
        capture_output=True,
        check=True,
    )
    private_pem = proc.stdout.decode("utf-8").strip()
    pub_proc = subprocess.run(
        ["openssl", "rsa", "-pubout"],
        input=private_pem.encode("utf-8"),
        capture_output=True,
        check=True,
    )
    pubkey_pem = pub_proc.stdout.decode("utf-8")
    pubkey_b64 = _pem_public_key_to_dns_base64(pubkey_pem)
    if not pubkey_b64:
        raise RuntimeError("Failed to derive DKIM public key from generated private key")
    install_dkim_key_pair(name, sel, private_pem, format_dkim_dns_txt(pubkey_b64))
    return private_pem, format_dkim_dns_txt(pubkey_b64)


def _extract_public_key_from_private_pem(private_pem: str) -> str | None:
    proc = subprocess.run(
        ["openssl", "rsa", "-pubout"],
        input=private_pem.encode("utf-8"),
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    return _pem_public_key_to_dns_base64(proc.stdout.decode("utf-8"))


def _pem_public_key_to_dns_base64(pem_public: str) -> str | None:
    proc = subprocess.run(
        ["openssl", "rsa", "-pubin", "-outform", "DER"],
        input=pem_public.encode("utf-8"),
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout:
        return None
    import base64

    der = proc.stdout
    if len(der) < 24:
        return None
    # Skip DER SEQUENCE + algorithm identifier; remainder is PKCS#1 RSAPublicKey BIT STRING body.
    bit_string = der[24:]
    return base64.b64encode(bit_string).decode("ascii")
