import csv
import io
import re
from fastapi import HTTPException

from app.db import db
from app.domain_destinations import (
    resolve_destination_by_label,
    resolve_destination_for_mailbox,
)
from app.domains import assert_mailboxes_allowed, resolve_domain_for_mailbox

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

MAIL_HEADERS = {"mail", "email", "e-mail", "indirizzo", "mailbox"}
LOCAL_HEADERS = {"local", "local_part", "username", "user", "utente"}
DOMAIN_HEADERS = {"domain", "dominio"}
LABEL_HEADERS = {"destination_label", "label", "etichetta", "dest_label"}
LEGACY_HOST_HEADERS = {"destination_host", "host", "server", "relay"}
GENERIC_DEST_HEADERS = {"destinazione", "destination"}
PORT_HEADERS = {"porta", "port", "destination_port"}

LEGACY_CSV_HINT = (
    "Legacy CSV with host/port is deprecated; use destination_label "
    "(etichetta server in Domini) instead of host/port columns"
)


def _normalize_header(name: str) -> str:
    return name.strip().lower().replace(" ", "_")


def _detect_delimiter(sample: str) -> str:
    if sample.count(";") > sample.count(","):
        return ";"
    return ","


def _detect_import_mode(mapping: dict[str, str]) -> str:
    if "host" in mapping or "porta" in mapping:
        return "legacy_host"
    if "label" in mapping:
        return "label"
    if "destinazione" in mapping:
        return "label"
    raise HTTPException(
        status_code=400,
        detail=(
            "CSV must include mail/email (or local+domain) and destination_label "
            f"(or label/destinazione). {LEGACY_CSV_HINT}"
        ),
    )


def _map_headers(fieldnames: list[str]) -> tuple[dict[str, str], str]:
    mapping: dict[str, str] = {}
    for raw in fieldnames:
        key = _normalize_header(raw)
        if key in MAIL_HEADERS and "mail" not in mapping:
            mapping["mail"] = raw
        elif key in LOCAL_HEADERS and "local" not in mapping:
            mapping["local"] = raw
        elif key in DOMAIN_HEADERS and "domain" not in mapping:
            mapping["domain"] = raw
        elif key in LABEL_HEADERS and "label" not in mapping:
            mapping["label"] = raw
        elif key in LEGACY_HOST_HEADERS and "host" not in mapping:
            mapping["host"] = raw
        elif key in GENERIC_DEST_HEADERS:
            if "destinazione" not in mapping:
                mapping["destinazione"] = raw
        elif key in PORT_HEADERS and "porta" not in mapping:
            mapping["porta"] = raw

    if "mail" not in mapping and ("local" not in mapping or "domain" not in mapping):
        raise HTTPException(
            status_code=400,
            detail=(
                "CSV must include mail/email or both local+domain columns, plus "
                "destination_label (or legacy host/port columns)"
            ),
        )

    if (
        "destinazione" in mapping
        and "label" not in mapping
        and "host" not in mapping
        and "porta" not in mapping
    ):
        mapping["label"] = mapping["destinazione"]

    mode = _detect_import_mode(mapping)
    if mode == "label" and "label" not in mapping:
        raise HTTPException(
            status_code=400,
            detail=(
                "CSV must include destination_label (or label/destinazione). "
                f"{LEGACY_CSV_HINT}"
            ),
        )
    if mode == "legacy_host" and "host" not in mapping:
        if "destinazione" in mapping:
            mapping["host"] = mapping["destinazione"]
        elif "label" in mapping and "porta" in mapping:
            mapping["host"] = mapping["label"]
        else:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Legacy CSV requires destination_host/host (and optional port). "
                    f"{LEGACY_CSV_HINT}"
                ),
            )
    return mapping, mode


def _parse_port(value: str, line_no: int) -> int:
    raw = (value or "").strip()
    if not raw:
        return 25
    try:
        port = int(raw)
    except ValueError as exc:
        raise ValueError(f"line {line_no}: invalid port '{value}'") from exc
    if port < 1 or port > 65535:
        raise ValueError(f"line {line_no}: port out of range")
    return port


def _email_from_row(row: dict, col_map: dict[str, str], line_no: int) -> str:
    if "mail" in col_map:
        mail = (row.get(col_map["mail"]) or "").strip().lower()
    else:
        local = (row.get(col_map["local"]) or "").strip().lower()
        domain = (row.get(col_map["domain"]) or "").strip().lower()
        if not local or not domain:
            raise ValueError(f"line {line_no}: missing local part or domain")
        mail = f"{local}@{domain}"
    return mail


def _drop_first_non_empty_line(text: str) -> str:
    lines = text.splitlines()
    dropped = False
    kept: list[str] = []
    for line in lines:
        if not dropped and line.strip():
            dropped = True
            continue
        kept.append(line)
    return "\n".join(kept)


def _skip_header_legacy_third_column(port_raw: str) -> bool:
    raw = (port_raw or "").strip()
    if not raw:
        return False
    try:
        port = int(raw)
    except ValueError:
        return False
    return 1 <= port <= 65535


def _append_parsed_row_label(
    rows: list[dict],
    *,
    line_no: int,
    mail: str,
    label: str,
) -> None:
    if not mail and not label:
        return
    if not mail:
        raise ValueError(f"line {line_no}: missing email")
    if not label:
        raise ValueError(f"line {line_no}: missing destination_label")
    if not EMAIL_PATTERN.match(mail):
        raise ValueError(f"line {line_no}: invalid email '{mail}'")
    rows.append(
        {
            "line": line_no,
            "email": mail,
            "destination_label": label.strip(),
        }
    )


def _append_parsed_row_legacy(
    rows: list[dict],
    *,
    line_no: int,
    mail: str,
    dest: str,
    port_raw: str,
) -> None:
    if not mail and not dest:
        return
    if not mail:
        raise ValueError(f"line {line_no}: missing email")
    if not dest:
        raise ValueError(f"line {line_no}: missing destination host")
    if not EMAIL_PATTERN.match(mail):
        raise ValueError(f"line {line_no}: invalid email '{mail}'")
    rows.append(
        {
            "line": line_no,
            "email": mail,
            "destination_host": dest,
            "destination_port": _parse_port(port_raw, line_no),
            "legacy_host": True,
        }
    )


def _resolve_row_destination(domain_id: int, row: dict) -> tuple[str, int]:
    if row.get("destination_label"):
        return resolve_destination_by_label(domain_id, row["destination_label"])
    host = row["destination_host"]
    port = row["destination_port"]
    resolve_destination_for_mailbox(domain_id, host, port)
    return host, port


def _parse_rows(csv_text: str, skip_header: bool = False) -> list[dict]:
    text = csv_text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="CSV file is empty")

    if skip_header:
        text = _drop_first_non_empty_line(text).strip()
        if not text:
            raise HTTPException(status_code=400, detail="No data rows in CSV (only header or empty file)")

        delimiter = _detect_delimiter(text.splitlines()[0])
        reader = csv.reader(io.StringIO(text), delimiter=delimiter)
        rows: list[dict] = []
        for idx, cells in enumerate(reader, start=2):
            mail = (cells[0] if len(cells) > 0 else "").strip().lower()
            second = (cells[1] if len(cells) > 1 else "").strip()
            third = (cells[2] if len(cells) > 2 else "").strip()
            if _skip_header_legacy_third_column(third):
                _append_parsed_row_legacy(
                    rows, line_no=idx, mail=mail, dest=second, port_raw=third
                )
            else:
                _append_parsed_row_label(rows, line_no=idx, mail=mail, label=second)
    else:
        delimiter = _detect_delimiter(text.splitlines()[0])
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        if not reader.fieldnames:
            raise HTTPException(status_code=400, detail="CSV has no header row")

        col_map, mode = _map_headers(list(reader.fieldnames))
        rows = []
        for idx, row in enumerate(reader, start=2):
            mail = _email_from_row(row, col_map, idx)
            if mode == "label":
                label = (row.get(col_map["label"]) or "").strip()
                _append_parsed_row_label(rows, line_no=idx, mail=mail, label=label)
            else:
                dest = (row.get(col_map["host"]) or "").strip()
                port_raw = row.get(col_map.get("porta", ""), "") if "porta" in col_map else ""
                _append_parsed_row_legacy(
                    rows, line_no=idx, mail=mail, dest=dest, port_raw=port_raw
                )

    if not rows:
        raise HTTPException(status_code=400, detail="No data rows in CSV")
    return rows


def import_mailboxes_csv(
    csv_text: str,
    update_existing: bool = False,
    skip_header: bool = False,
) -> dict:
    try:
        parsed = _parse_rows(csv_text, skip_header=skip_header)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    relay_blocked_domains: set[int] = set()
    for row in parsed:
        try:
            domain_id, _ = resolve_domain_for_mailbox(row["email"], None)
        except HTTPException:
            continue
        if domain_id in relay_blocked_domains:
            continue
        relay_blocked_domains.add(domain_id)
        assert_mailboxes_allowed(domain_id)

    created = 0
    updated = 0
    skipped = 0
    errors: list[dict] = []
    affected_domain_ids: set[int] = set()

    with db() as conn:
        for row in parsed:
            line = row["line"]
            email = row["email"]
            try:
                domain_id, _ = resolve_domain_for_mailbox(email, None)
                assert_mailboxes_allowed(domain_id)
                destination_host, destination_port = _resolve_row_destination(
                    domain_id, row
                )
                existing = conn.execute(
                    "SELECT id, destination_host, destination_port FROM mailboxes WHERE email = ?",
                    (email,),
                ).fetchone()

                if existing:
                    if not update_existing:
                        skipped += 1
                        continue
                    conn.execute(
                        """
                        UPDATE mailboxes
                        SET destination_host = ?, destination_port = ?, enabled = 1, domain_id = ?
                        WHERE id = ?
                        """,
                        (
                            destination_host,
                            destination_port,
                            domain_id,
                            existing["id"],
                        ),
                    )
                    updated += 1
                else:
                    conn.execute(
                        """
                        INSERT INTO mailboxes (email, destination_host, destination_port, enabled, domain_id)
                        VALUES (?, ?, ?, 1, ?)
                        """,
                        (
                            email,
                            destination_host,
                            destination_port,
                            domain_id,
                        ),
                    )
                    created += 1
                affected_domain_ids.add(domain_id)
            except HTTPException as exc:
                detail = exc.detail
                if not isinstance(detail, str):
                    detail = str(detail)
                errors.append({"line": line, "email": email, "error": detail})
            except Exception as exc:
                errors.append({"line": line, "email": email, "error": str(exc)})

        conn.commit()

    from app.sync import touch_domain_updated_at

    for domain_id in affected_domain_ids:
        touch_domain_updated_at(domain_id)

    return {
        "total_rows": len(parsed),
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
        "update_existing": update_existing,
        "skip_header": skip_header,
        "affected_domain_ids": sorted(affected_domain_ids),
    }
