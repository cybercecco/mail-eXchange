import csv
import io
import re
from fastapi import HTTPException

from app.db import db
from app.domain_destinations import resolve_destination_for_mailbox
from app.domains import resolve_domain_for_mailbox

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

MAIL_HEADERS = {"mail", "email", "e-mail", "indirizzo", "mailbox"}
DEST_HEADERS = {"destinazione", "destination", "destination_host", "host", "server", "relay"}
PORT_HEADERS = {"porta", "port", "destination_port"}


def _normalize_header(name: str) -> str:
    return name.strip().lower().replace(" ", "_")


def _detect_delimiter(sample: str) -> str:
    if sample.count(";") > sample.count(","):
        return ";"
    return ","


def _map_headers(fieldnames: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for raw in fieldnames:
        key = _normalize_header(raw)
        if key in MAIL_HEADERS and "mail" not in mapping:
            mapping["mail"] = raw
        elif key in DEST_HEADERS and "destinazione" not in mapping:
            mapping["destinazione"] = raw
        elif key in PORT_HEADERS and "porta" not in mapping:
            mapping["porta"] = raw
    if "mail" not in mapping or "destinazione" not in mapping:
        raise HTTPException(
            status_code=400,
            detail="CSV must include columns for mail/email and destinazione/destination (porta optional)",
        )
    return mapping


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


def _append_parsed_row(
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
        raise ValueError(f"line {line_no}: missing destination")
    if not EMAIL_PATTERN.match(mail):
        raise ValueError(f"line {line_no}: invalid email '{mail}'")
    rows.append(
        {
            "line": line_no,
            "email": mail,
            "destination_host": dest,
            "destination_port": _parse_port(port_raw, line_no),
        }
    )


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
            dest = (cells[1] if len(cells) > 1 else "").strip()
            port_raw = (cells[2] if len(cells) > 2 else "").strip()
            _append_parsed_row(rows, line_no=idx, mail=mail, dest=dest, port_raw=port_raw)
    else:
        delimiter = _detect_delimiter(text.splitlines()[0])
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        if not reader.fieldnames:
            raise HTTPException(status_code=400, detail="CSV has no header row")

        col_map = _map_headers(list(reader.fieldnames))
        rows = []
        for idx, row in enumerate(reader, start=2):
            mail = (row.get(col_map["mail"]) or "").strip().lower()
            dest = (row.get(col_map["destinazione"]) or "").strip()
            port_raw = row.get(col_map.get("porta", ""), "") if "porta" in col_map else ""
            _append_parsed_row(rows, line_no=idx, mail=mail, dest=dest, port_raw=port_raw)

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
                resolve_destination_for_mailbox(
                    domain_id, row["destination_host"], row["destination_port"]
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
                            row["destination_host"],
                            row["destination_port"],
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
                            row["destination_host"],
                            row["destination_port"],
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
