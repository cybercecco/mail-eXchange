import os
from pathlib import Path

MAIL_CONFIG_DIR = Path(os.environ.get("MAIL_CONFIG_DIR", "/mail-exchange-config"))

POSTFIX_GENERATED_DIR = MAIL_CONFIG_DIR / "postfix" / "generated"
SPAMASSASSIN_LOCAL_CF = MAIL_CONFIG_DIR / "spamassassin" / "local.cf"
AMAVIS_SPAM_OVERRIDES = MAIL_CONFIG_DIR / "amavis" / "spam-overrides.conf"


def ensure_mail_config_dirs() -> None:
    POSTFIX_GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    SPAMASSASSIN_LOCAL_CF.parent.mkdir(parents=True, exist_ok=True)
    AMAVIS_SPAM_OVERRIDES.parent.mkdir(parents=True, exist_ok=True)
