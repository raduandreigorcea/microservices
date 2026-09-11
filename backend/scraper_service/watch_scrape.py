"""Walk one company through Extract and Transform, narrating each step.

A development tool, not part of the service. Run it with --show to watch
Chromium do the work in a visible window:

    .venv\\Scripts\\python.exe watch_scrape.py --show
    .venv\\Scripts\\python.exe watch_scrape.py 1014600000912
"""

from __future__ import annotations

import argparse
import asyncio

from app.config import Settings
from app.sources import (
    RESOURCE_COMPANY,
    RESOURCE_COMPANY_PAGE,
    RESOURCE_FIN_DATA,
    BrowserPool,
    DepozitarSource,
    OpenMoneySource,
    declaration_ids,
    idnos_in_listing,
    total_companies,
)
from app.transform import build_company

DEFAULT_IDNO = "1003600069773"


def show(item) -> None:
    mark = "ok " if item.ok else "ERR"
    print(
        f"    [{mark}] {item.resource:<22} {item.fetched.status} "
        f"{len(item.fetched.body):>7} bytes  {item.fetched.url}"
    )


def body_of(items, resource: str) -> str | None:
    for item in items:
        if item.resource == resource and item.ok:
            return item.fetched.body
    return None


async def main(idno: str, headless: bool, pause: float) -> None:
    settings = Settings(
        # Neither is touched: this script never writes to the database.
        database_url="postgresql+psycopg://unused@localhost/unused",
        redis_url="redis://localhost:6379/15",
        browser_headless=headless,
        request_min_interval_seconds=pause,
    )
    browser = BrowserPool(settings)
    openmoney = OpenMoneySource(settings, browser)
    depozitar = DepozitarSource(settings, browser)

    print(f"\nstarting chromium (headless={headless})")
    await browser.start()
    try:
        print("\n1. EXTRACT  one page of the openmoney register")
        listing = await openmoney.fetch_listing(0, 5)
        for item in listing:
            show(item)
        page = body_of(listing, RESOURCE_COMPANY_PAGE)
        print(
            f"    -> {len(idnos_in_listing(page))} companies on this page, "
            f"{total_companies(page)} in the register"
        )

        print(f"\n2. EXTRACT  the openmoney profile for {idno}")
        company_items = await openmoney.fetch_company(idno)
        for item in company_items:
            show(item)

        print(f"\n3. EXTRACT  what the depositary holds for {idno}")
        index = await depozitar.fetch_index(idno)
        show(index)
        ids = declaration_ids(index.fetched.body) if index.ok else []
        print(f"    -> {len(ids)} declarations on file")

        declarations = []
        if ids:
            print("\n4. EXTRACT  the most recent declaration in full")
            declarations = await depozitar.fetch_declaration(idno, ids[-1])
            for item in declarations:
                show(item)

        print("\n5. TRANSFORM  merge both sources into one record")
        record = build_company(
            idno,
            openmoney_company=body_of(company_items, RESOURCE_COMPANY),
            openmoney_fin_data=body_of(company_items, RESOURCE_FIN_DATA),
            depozitar_declarations=[
                item.fetched.body for item in declarations if item.ok
            ],
        )
        print(f"    name      {record.fields.get('name')}")
        print(f"    sources   {record.sources}")
        print(f"    people    {len(record.people)}")
        for person in record.people:
            share = "" if person.share_percent is None else f" {person.share_percent}%"
            print(f"              {person.role:<14} {person.full_name}{share}")
        print(f"    statements {len(record.statements)}")
        for statement in record.statements:
            print(
                f"              {statement.year} {statement.source:<10} "
                f"{len(statement.line_items):>3} line items"
            )
        if record.statements:
            newest = record.statements[-1]
            print(f"    a few lines from {newest.year} ({newest.source}):")
            for line in newest.line_items[:5]:
                print(
                    f"              form {line.group_code} row {line.field_code}  "
                    f"{(line.label or '')[:46]:<46} {line.value_current}"
                )
    finally:
        print("\nclosing chromium")
        await browser.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("idno", nargs="?", default=DEFAULT_IDNO)
    parser.add_argument(
        "--show", action="store_true", help="open a visible browser window"
    )
    parser.add_argument(
        "--pause",
        type=float,
        default=1.0,
        help="seconds between requests to the same site",
    )
    args = parser.parse_args()
    asyncio.run(main(args.idno, not args.show, args.pause))
