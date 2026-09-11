"""Extract. One Chromium instance, and the two sites it reads through it."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from urllib.parse import urlsplit

from playwright.async_api import Browser, BrowserContext, Page, async_playwright
from playwright.async_api import Error as PlaywrightError

from app.config import Settings
from app.models import SOURCE_DEPOZITAR, SOURCE_OPENMONEY

# openmoney
RESOURCE_COMPANY_PAGE_HTML = "company_page_html"
RESOURCE_COMPANY_PAGE = "company_page"
RESOURCE_COMPANY_HTML = "company_html"
RESOURCE_COMPANY = "company"
RESOURCE_FIN_DATA = "fin_data"

# depozitar
RESOURCE_DECLARATION_INDEX = "declaration_index"
RESOURCE_DECLARATION_HTML = "declaration_html"
RESOURCE_DECLARATION = "declaration"

# Statuses that mean come back later, rather than don't bother.
RETRYABLE = (408, 425, 429, 500, 502, 503, 504)


@dataclass(frozen=True, slots=True)
class Fetched:
    """One response, kept exactly as it arrived."""

    url: str
    status: int
    content_type: str | None
    body: str

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


@dataclass(frozen=True, slots=True)
class Extracted:
    """A response plus enough context for the Load step to file it."""

    source: str
    resource: str
    resource_key: str
    fetched: Fetched
    idno: str | None = None

    @property
    def ok(self) -> bool:
        return self.fetched.ok


# --- the browser ------------------------------------------------------------


class HostThrottle:
    """Keeps a minimum gap between two requests to the same host."""

    def __init__(self, min_interval_seconds: float) -> None:
        self._min_interval = min_interval_seconds
        self._locks: dict[str, asyncio.Lock] = {}
        self._last: dict[str, float] = {}

    async def wait(self, url: str) -> None:
        if self._min_interval <= 0:
            return
        host = urlsplit(url).netloc
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            gap = time.monotonic() - self._last.get(host, 0.0)
            if gap < self._min_interval:
                await asyncio.sleep(self._min_interval - gap)
            self._last[host] = time.monotonic()


class BrowserPool:
    """Hands out pages from a single browser context, a few at a time."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._pages: asyncio.Semaphore | None = None
        self._start_lock = asyncio.Lock()
        self.throttle = HostThrottle(settings.request_min_interval_seconds)

    @property
    def started(self) -> bool:
        return self._context is not None

    async def start(self) -> None:
        async with self._start_lock:
            if self._context is not None:
                return
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=self._settings.browser_headless,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            self._context = await self._browser.new_context(
                locale=self._settings.browser_locale,
                user_agent=self._settings.browser_user_agent,
                viewport={"width": 1440, "height": 900},
            )
            self._context.set_default_navigation_timeout(
                self._settings.browser_navigation_timeout_ms
            )
            self._context.set_default_timeout(
                self._settings.browser_navigation_timeout_ms
            )
            self._pages = asyncio.Semaphore(self._settings.browser_max_pages)

    async def stop(self) -> None:
        async with self._start_lock:
            for closer in (self._context, self._browser):
                if closer is not None:
                    try:
                        await closer.close()
                    except PlaywrightError:
                        pass
            if self._playwright is not None:
                await self._playwright.stop()
            self._context = self._browser = self._playwright = None
            self._pages = None

    @asynccontextmanager
    async def page(self) -> AsyncIterator[Page]:
        if self._context is None or self._pages is None:
            raise RuntimeError("browser pool is not started")
        async with self._pages:
            page = await self._context.new_page()
            try:
                yield page
            finally:
                try:
                    await page.close()
                except PlaywrightError:
                    pass

    async def render(self, page: Page, url: str) -> Fetched:
        """Open a URL in a real tab and keep the DOM once it has settled."""
        await self.throttle.wait(url)
        response = await page.goto(url, wait_until="domcontentloaded")
        try:
            await page.wait_for_load_state("networkidle")
        except PlaywrightError:
            # A page that keeps polling never goes idle. What rendered is enough.
            pass
        return Fetched(
            url=url,
            status=response.status if response is not None else 0,
            content_type="text/html",
            body=await page.content(),
        )

    async def get(self, url: str, *, referer: str | None = None) -> Fetched:
        """Fetch through the browser's own network stack, cookies included."""
        if self._context is None:
            raise RuntimeError("browser pool is not started")
        await self.throttle.wait(url)
        headers = {"Accept": "application/json"}
        if referer:
            headers["Referer"] = referer
        response = await self._context.request.get(
            url,
            headers=headers,
            timeout=self._settings.browser_request_timeout_ms,
            fail_on_status_code=False,
        )
        return Fetched(
            url=url,
            status=response.status,
            content_type=response.headers.get("content-type"),
            body=await response.text(),
        )

    async def get_with_retry(self, url: str, *, referer: str | None = None) -> Fetched:
        attempts = self._settings.request_max_attempts
        last: Fetched | None = None
        for attempt in range(1, attempts + 1):
            try:
                last = await self.get(url, referer=referer)
            except PlaywrightError as exc:
                if attempt == attempts:
                    raise
                last = Fetched(url=url, status=0, content_type=None, body=str(exc))
            else:
                if last.status not in RETRYABLE or attempt == attempts:
                    return last
            await asyncio.sleep(self._settings.request_backoff_seconds * attempt)
        assert last is not None
        return last


# --- openmoney.md -----------------------------------------------------------


class OpenMoneySource:
    """The company listing and, per IDNO, the profile and its financials.

    The site is an Angular app that fills the company page in the browser, so
    the tab does the rendering and the data comes from the same API the page
    itself calls, through that tab's network stack.
    """

    def __init__(self, settings: Settings, browser: BrowserPool) -> None:
        self._settings = settings
        self._browser = browser

    @property
    def site(self) -> str:
        return self._settings.openmoney_site_url.rstrip("/")

    @property
    def api(self) -> str:
        return self._settings.openmoney_api_url.rstrip("/")

    async def fetch_listing(self, page_number: int, page_size: int) -> list[Extracted]:
        """One page of the company register."""
        out: list[Extracted] = []
        key = str(page_number)

        if self._settings.capture_rendered_html:
            url = f"{self.site}/companies?page={page_number}"
            async with self._browser.page() as tab:
                rendered = await self._browser.render(tab, url)
            out.append(
                Extracted(
                    source=SOURCE_OPENMONEY,
                    resource=RESOURCE_COMPANY_PAGE_HTML,
                    resource_key=key,
                    fetched=rendered,
                )
            )

        listing = await self._browser.get_with_retry(
            f"{self.api}/api/companies?page={page_number}&size={page_size}",
            referer=f"{self.site}/companies",
        )
        out.append(
            Extracted(
                source=SOURCE_OPENMONEY,
                resource=RESOURCE_COMPANY_PAGE,
                resource_key=key,
                fetched=listing,
            )
        )
        return out

    async def fetch_company(self, idno: str) -> list[Extracted]:
        """The profile page and the per-year financials behind it."""
        out: list[Extracted] = []
        profile_url = f"{self.site}/companies/{idno}"

        if self._settings.capture_rendered_html:
            async with self._browser.page() as tab:
                rendered = await self._browser.render(tab, profile_url)
            out.append(
                Extracted(
                    source=SOURCE_OPENMONEY,
                    resource=RESOURCE_COMPANY_HTML,
                    resource_key=idno,
                    idno=idno,
                    fetched=rendered,
                )
            )

        for resource, url in (
            (RESOURCE_COMPANY, f"{self.api}/api/companies/{idno}"),
            (RESOURCE_FIN_DATA, f"{self.api}/api/fin-data-v1/{idno}"),
        ):
            fetched = await self._browser.get_with_retry(url, referer=profile_url)
            out.append(
                Extracted(
                    source=SOURCE_OPENMONEY,
                    resource=resource,
                    resource_key=idno,
                    idno=idno,
                    fetched=fetched,
                )
            )
        return out


def idnos_in_listing(body: str) -> list[str]:
    """Pull the IDNOs out of one listing page, in the order they appear."""
    payload = _loads(body)
    rows = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return []
    seen: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        idno = row.get("idno") or row.get("id")
        if idno and str(idno) not in seen:
            seen.append(str(idno))
    return seen


def total_companies(body: str) -> int | None:
    payload = _loads(body)
    if isinstance(payload, dict) and isinstance(payload.get("totalItemCount"), int):
        return payload["totalItemCount"]
    return None


# --- depozitar.statistica.md ------------------------------------------------


class DepozitarSource:
    """Declarations for one IDNO, then each declaration in full.

    Only the lookup by fiscal code is used. The site's paginated search sits
    behind a reCAPTCHA and is deliberately left alone.
    """

    def __init__(self, settings: Settings, browser: BrowserPool) -> None:
        self._settings = settings
        self._browser = browser

    @property
    def site(self) -> str:
        return self._settings.depozitar_site_url.rstrip("/")

    @property
    def api(self) -> str:
        return self._settings.depozitar_api_url.rstrip("/")

    async def fetch_index(self, idno: str) -> Extracted:
        """Which years this company has on file, and under which ids."""
        fetched = await self._browser.get_with_retry(
            f"{self.api}/api/public/v1/fs/economic-agent?idno={idno}",
            referer=f"{self.site}/economic-agent",
        )
        return Extracted(
            source=SOURCE_DEPOZITAR,
            resource=RESOURCE_DECLARATION_INDEX,
            resource_key=idno,
            idno=idno,
            fetched=fetched,
        )

    async def fetch_declaration(
        self, idno: str, declaration_id: str
    ) -> list[Extracted]:
        out: list[Extracted] = []
        page_url = f"{self.site}/financial-statement/{declaration_id}"

        if self._settings.capture_rendered_html:
            async with self._browser.page() as tab:
                rendered = await self._browser.render(tab, page_url)
            out.append(
                Extracted(
                    source=SOURCE_DEPOZITAR,
                    resource=RESOURCE_DECLARATION_HTML,
                    resource_key=declaration_id,
                    idno=idno,
                    fetched=rendered,
                )
            )

        fetched = await self._browser.get_with_retry(
            f"{self.api}/api/public/v1/fs/{declaration_id}", referer=page_url
        )
        out.append(
            Extracted(
                source=SOURCE_DEPOZITAR,
                resource=RESOURCE_DECLARATION,
                resource_key=declaration_id,
                idno=idno,
                fetched=fetched,
            )
        )
        return out


def declaration_ids(body: str) -> list[str]:
    """Declaration ids from an index response, oldest year first."""
    payload = _loads(body)
    if not isinstance(payload, list):
        return []
    rows = [row for row in payload if isinstance(row, dict) and row.get("id")]
    rows.sort(key=lambda row: row.get("year") or 0)
    return [str(row["id"]) for row in rows]


def _loads(body: str) -> object | None:
    try:
        return json.loads(body)
    except (TypeError, ValueError):
        return None
