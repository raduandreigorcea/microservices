"""Transform. Raw bodies in, typed rows out.

Every parser here is a plain function over a decoded payload, so the whole
step runs against saved fixtures without a browser or a database.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from app.models import (
    PARTY_COMPANY,
    PARTY_PERSON,
    ROLE_ADMINISTRATOR,
    ROLE_FOUNDER,
    SOURCE_DEPOZITAR,
    SOURCE_OPENMONEY,
)

# Both sources number the statement forms the same way.
FORM_NAMES = {
    "5": "BILANȚUL",
    "6": "SITUAȚIA DE PROFIT ȘI PIERDERE",
    "7": "SITUAȚIA MODIFICĂRILOR CAPITALULUI PROPRIU",
    "8": "SITUAȚIA FLUXURILOR DE NUMERAR",
}

_CODE_KEY = re.compile(r"^CODE_(\d+)_(\d+)(?:_(.+))?$")
# Thin and non-breaking spaces are used as thousands separators.
_SPACES = re.compile(r"[\s   ']")


def parse_number(value: object) -> Decimal | None:
    """Numbers arrive as ints, or as strings like '1 234,56' and '(87)'."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    if not isinstance(value, str):
        return None
    text = _SPACES.sub("", value).strip()
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    text = text.replace(",", ".")
    if text.count(".") > 1:
        head, _, tail = text.rpartition(".")
        text = head.replace(".", "") + "." + tail
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    return -number if negative else number


def parse_date(value: object) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def parse_int(value: object) -> int | None:
    number = parse_number(value)
    return int(number) if number is not None else None


def clean_text(value: object, limit: int | None = None) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not text:
        return None
    return text[:limit] if limit else text


def loads(body: str) -> object | None:
    try:
        return json.loads(body)
    except (TypeError, ValueError):
        return None


@dataclass(slots=True)
class PersonRecord:
    role: str
    party_type: str
    full_name: str
    source_key: str | None = None
    share_percent: Decimal | None = None


@dataclass(slots=True)
class LineItemRecord:
    group_code: str
    field_code: str
    group_name: str | None = None
    label: str | None = None
    value_current: Decimal | None = None
    value_previous: Decimal | None = None
    value_extra_1: Decimal | None = None
    value_extra_2: Decimal | None = None


@dataclass(slots=True)
class StatementRecord:
    year: int
    source: str
    source_ref: str | None = None
    doctype: str | None = None
    status: str | None = None
    origin: str | None = None
    entity_name: str | None = None
    period_from: date | None = None
    period_to: date | None = None
    declaration_date: date | None = None
    is_audited: bool | None = None
    is_signed: bool | None = None
    line_items: list[LineItemRecord] = field(default_factory=list)


@dataclass(slots=True)
class CompanyRecord:
    idno: str
    fields: dict = field(default_factory=dict)
    people: list[PersonRecord] = field(default_factory=list)
    statements: list[StatementRecord] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)


# --- openmoney --------------------------------------------------------------


def company_from_openmoney(payload: object) -> dict:
    if not isinstance(payload, dict):
        return {}
    address = payload.get("address") if isinstance(payload.get("address"), dict) else {}
    cuatm = payload.get("cuatm") if isinstance(payload.get("cuatm"), dict) else {}
    return _drop_empty(
        {
            "name": clean_text(payload.get("fullName"), 500),
            "legal_form": clean_text(payload.get("type"), 64),
            "is_active": payload.get("isActive")
            if isinstance(payload.get("isActive"), bool)
            else None,
            "registered_at": parse_date(payload.get("registeredAt")),
            "revision_date": parse_date(payload.get("revisionDate")),
            "address": clean_text(address.get("fullName"), 500),
            "city": clean_text(address.get("city"), 200),
            "street": _street(address),
            "cuatm_code": clean_text(cuatm.get("id"), 16),
            "cuatm_name": clean_text(cuatm.get("name"), 200),
            "licensed_activities": _activities(payload.get("licensedActivities")),
            "unlicensed_activities": _activities(payload.get("unlicensedActivities")),
        }
    )


def _street(address: dict) -> str | None:
    parts = [clean_text(address.get("street")), clean_text(address.get("streetNumber"))]
    joined = " ".join(part for part in parts if part)
    return joined[:300] or None


def _activities(raw: object) -> list | None:
    if not isinstance(raw, list):
        return None
    out = [
        {"code": item.get("id"), "name": clean_text(item.get("name"))}
        for item in raw
        if isinstance(item, dict) and item.get("name")
    ]
    return out or None


def people_from_openmoney(payload: object) -> list[PersonRecord]:
    if not isinstance(payload, dict):
        return []
    groups = (
        ("personFounders", ROLE_FOUNDER, PARTY_PERSON),
        ("personAdministrators", ROLE_ADMINISTRATOR, PARTY_PERSON),
        ("companyFounders", ROLE_FOUNDER, PARTY_COMPANY),
        ("companyAdministrators", ROLE_ADMINISTRATOR, PARTY_COMPANY),
    )
    out: list[PersonRecord] = []
    seen: set[tuple[str, str, str]] = set()
    for key, role, party in groups:
        for item in payload.get(key) or []:
            if not isinstance(item, dict):
                continue
            name = clean_text(item.get("fullName"), 300)
            if not name:
                continue
            identity = (role, party, name)
            if identity in seen:
                continue
            seen.add(identity)
            out.append(
                PersonRecord(
                    role=role,
                    party_type=party,
                    full_name=name,
                    source_key=clean_text(item.get("idno") or item.get("id"), 128),
                    share_percent=parse_number(item.get("shares")),
                )
            )
    return out


def statements_from_openmoney(payload: object) -> list[StatementRecord]:
    """One statement per year, rebuilt from the flat CODE_form_row keys."""
    if not isinstance(payload, list):
        return []
    out: list[StatementRecord] = []
    for year_payload in payload:
        if not isinstance(year_payload, dict):
            continue
        year = parse_int(year_payload.get("year"))
        if year is None:
            continue
        out.append(
            StatementRecord(
                year=year,
                source=SOURCE_OPENMONEY,
                doctype=clean_text(year_payload.get("doctype"), 32),
                entity_name=clean_text(
                    year_payload.get("name") or year_payload.get("organizationName"),
                    500,
                ),
                line_items=_line_items_from_codes(year_payload),
            )
        )
    out.sort(key=lambda statement: statement.year)
    return out


def _line_items_from_codes(year_payload: dict) -> list[LineItemRecord]:
    items: dict[tuple[str, str], LineItemRecord] = {}
    for key, value in year_payload.items():
        match = _CODE_KEY.match(key)
        if not match:
            continue
        form, row, label_slug = match.groups()
        item = items.setdefault(
            (form, row),
            LineItemRecord(
                group_code=form, field_code=row, group_name=FORM_NAMES.get(form)
            ),
        )
        number = parse_number(value)
        if number is not None and item.value_current is None:
            item.value_current = number
        if label_slug and item.label is None:
            item.label = _label_from_slug(label_slug)
    return sorted(items.values(), key=lambda item: (item.group_code, item.field_code))


def _label_from_slug(slug: str) -> str | None:
    # Keys read CODE_5_130_Total_imobilizari_corporale_rd_060_rd_070_.
    text = slug.replace("_", " ").strip()
    text = re.sub(r"\s+rd\s+", " rd.", text)
    return clean_text(text, 500)


# --- depozitar --------------------------------------------------------------


def company_from_depozitar(payload: object) -> dict:
    if not isinstance(payload, dict):
        return {}
    entity = payload.get("legalEntity")
    if not isinstance(entity, dict):
        return {}
    caem = entity.get("caem") if isinstance(entity.get("caem"), dict) else {}
    cfoj = entity.get("cfoj") if isinstance(entity.get("cfoj"), dict) else {}
    cfp = entity.get("cfp") if isinstance(entity.get("cfp"), dict) else {}
    cuatm = entity.get("cuatm") if isinstance(entity.get("cuatm"), dict) else {}
    return _drop_empty(
        {
            "name": clean_text(
                entity.get("name") or entity.get("organizationName"), 500
            ),
            "caem_code": clean_text(caem.get("code"), 16),
            "caem_name": clean_text(caem.get("nameRo") or caem.get("nameEn"), 300),
            "legal_form": clean_text(cfoj.get("nameRo"), 64),
            "ownership_name": clean_text(cfp.get("nameRo"), 200),
            "cuatm_code": clean_text(cuatm.get("code"), 16),
            "cuatm_name": clean_text(cuatm.get("nameRo"), 200),
            "cuiio": clean_text(entity.get("cuiio"), 32),
            "email": clean_text(entity.get("email"), 320),
            "phone": clean_text(entity.get("telefon"), 64),
            "website": clean_text(entity.get("web"), 500),
            "postal_code": clean_text(entity.get("postal"), 16),
            "street": clean_text(entity.get("street"), 300),
            "employees": parse_int(entity.get("nrEmployees")),
            "in_liquidation": entity.get("liquidation")
            if isinstance(entity.get("liquidation"), bool)
            else None,
        }
    )


def statement_from_depozitar(payload: object) -> StatementRecord | None:
    if not isinstance(payload, dict):
        return None
    year = parse_int(payload.get("year"))
    if year is None:
        return None
    entity = (
        payload.get("legalEntity")
        if isinstance(payload.get("legalEntity"), dict)
        else {}
    )
    return StatementRecord(
        year=year,
        source=SOURCE_DEPOZITAR,
        source_ref=clean_text(payload.get("id"), 64),
        doctype=clean_text(payload.get("doctype"), 32),
        status=clean_text(payload.get("status"), 32),
        origin=clean_text(payload.get("source"), 32),
        entity_name=clean_text(
            entity.get("name") or entity.get("organizationName"), 500
        ),
        period_from=parse_date(payload.get("periodFrom")),
        period_to=parse_date(payload.get("periodTo")),
        declaration_date=parse_date(payload.get("declarationDate")),
        is_audited=payload.get("audited")
        if isinstance(payload.get("audited"), bool)
        else None,
        is_signed=payload.get("signed")
        if isinstance(payload.get("signed"), bool)
        else None,
        line_items=_line_items_from_groups(payload.get("groups")),
    )


def _line_items_from_groups(groups: object) -> list[LineItemRecord]:
    if not isinstance(groups, list):
        return []
    items: list[LineItemRecord] = []
    seen: set[tuple[str, str]] = set()
    for group in groups:
        if not isinstance(group, dict):
            continue
        group_code = clean_text(str(group.get("id") or ""), 16) or clean_text(
            group.get("code"), 16
        )
        if not group_code:
            continue
        group_name = clean_text(group.get("name"), 200) or FORM_NAMES.get(group_code)
        for raw in group.get("fields") or []:
            if not isinstance(raw, dict):
                continue
            field_code = clean_text(raw.get("code"), 16)
            if not field_code or (group_code, field_code) in seen:
                continue
            values = (
                parse_number(raw.get("dateCurrent")),
                parse_number(raw.get("datePrev")),
                parse_number(raw.get("data3")),
                parse_number(raw.get("data4")),
            )
            if all(value is None for value in values):
                continue
            seen.add((group_code, field_code))
            items.append(
                LineItemRecord(
                    group_code=group_code,
                    field_code=field_code,
                    group_name=group_name,
                    label=clean_text(raw.get("name"), 500),
                    value_current=values[0],
                    value_previous=values[1],
                    value_extra_1=values[2],
                    value_extra_2=values[3],
                )
            )
    return items


# --- merge ------------------------------------------------------------------


def _drop_empty(values: dict) -> dict:
    return {key: value for key, value in values.items() if value is not None}


def build_company(
    idno: str,
    *,
    openmoney_company: str | None = None,
    openmoney_fin_data: str | None = None,
    depozitar_declarations: list[str] | None = None,
) -> CompanyRecord:
    """Merge every raw body we hold for one IDNO into one record.

    openmoney describes who owns the company, the depositary describes what it
    filed. The registry wins the fields both carry, since its names and codes
    are the shorter ones, and the depositary contributes everything else.
    """
    record = CompanyRecord(idno=idno)
    registry_fields: dict = {}
    depositary_fields: dict = {}
    # A company can file twice for the same year, a correction after the
    # original. Bodies arrive oldest first, so the last one seen wins.
    statements: dict[tuple[int, str], StatementRecord] = {}

    if openmoney_company:
        payload = loads(openmoney_company)
        registry_fields = company_from_openmoney(payload)
        if registry_fields:
            record.people = people_from_openmoney(payload)
            record.sources.append(SOURCE_OPENMONEY)

    if openmoney_fin_data:
        from_registry = statements_from_openmoney(loads(openmoney_fin_data))
        if from_registry:
            for statement in from_registry:
                statements[(statement.year, statement.source)] = statement
            if SOURCE_OPENMONEY not in record.sources:
                record.sources.append(SOURCE_OPENMONEY)

    for body in depozitar_declarations or []:
        payload = loads(body)
        statement = statement_from_depozitar(payload)
        if statement is None:
            continue
        statements[(statement.year, statement.source)] = statement
        depositary_fields.update(company_from_depozitar(payload))
        if SOURCE_DEPOZITAR not in record.sources:
            record.sources.append(SOURCE_DEPOZITAR)

    record.fields = {**depositary_fields, **registry_fields}
    record.statements = [statements[key] for key in sorted(statements)]
    return record
