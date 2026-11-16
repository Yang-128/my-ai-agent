import ipaddress
import json
import socket
from io import BytesIO
from urllib.parse import urljoin, urlparse

import httpx
from ddgs import DDGS
from lxml import html


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024


def search_web(query: str, max_results: int = 5) -> str:
    query = query.strip()
    if not query:
        raise ValueError("A search query is required.")

    max_results = max(1, min(int(max_results), 10))
    errors = []

    for backend in ("yahoo", "auto"):
        try:
            results = DDGS().text(
                query,
                max_results=max_results,
                backend=backend,
            )
            if results:
                return json.dumps({
                    "query": query,
                    "search_engine": backend,
                    "results": [
                        {
                            "title": item.get("title", ""),
                            "url": item.get("href", ""),
                            "snippet": item.get("body", ""),
                        }
                        for item in results
                    ],
                }, ensure_ascii=False, indent=2)
            errors.append(f"{backend}: no results")
        except Exception as error:
            errors.append(f"{backend}: {type(error).__name__}: {error}")

    raise RuntimeError("Web search failed. " + " | ".join(errors))


def _validate_public_url(url: str) -> str:
    parsed = urlparse(url.strip())

    if parsed.scheme not in ("http", "https"):
        raise ValueError("Only HTTP and HTTPS URLs are supported.")
    if not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Invalid URL.")
    if parsed.port and not 1 <= parsed.port <= 65535:
        raise ValueError("Invalid URL port.")

    hostname = parsed.hostname.rstrip(".").lower()
    if hostname in ("localhost",) or hostname.endswith(".localhost"):
        raise ValueError("Local addresses are not accessible.")

    try:
        addresses = [ipaddress.ip_address(hostname)]
    except ValueError:
        try:
            records = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
            addresses = list({
                ipaddress.ip_address(record[4][0])
                for record in records
            })
        except socket.gaierror as error:
            raise ValueError(f"Could not resolve hostname: {hostname}") from error

    if not addresses or any(
        not address.is_global for address in addresses
    ):
        raise ValueError("Private, local, or reserved network addresses are blocked.")

    return url


def _download(url: str) -> tuple[bytes, str, str]:
    current_url = url

    with httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=25,
        follow_redirects=False,
    ) as client:
        for _ in range(6):
            current_url = _validate_public_url(current_url)

            with client.stream("GET", current_url) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("location")
                    if not location:
                        raise RuntimeError("The website returned an invalid redirect.")
                    current_url = urljoin(current_url, location)
                    continue

                response.raise_for_status()
                content_type = response.headers.get("content-type", "").lower()
                chunks = []
                total = 0

                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > MAX_DOWNLOAD_BYTES:
                        raise ValueError("The page or document exceeds the 10 MB download limit.")
                    chunks.append(chunk)

                return b"".join(chunks), content_type, current_url

    raise RuntimeError("Too many redirects.")


def fetch_webpage(url: str, max_chars: int = 18000) -> str:
    data, content_type, final_url = _download(url)
    max_chars = max(1000, min(int(max_chars), 30000))

    is_pdf = (
        "application/pdf" in content_type
        or urlparse(final_url).path.lower().endswith(".pdf")
        or data[:5] == b"%PDF-"
    )

    if is_pdf:
        from pypdf import PdfReader

        reader = PdfReader(BytesIO(data))
        page_text = []
        for index, page in enumerate(reader.pages[:60]):
            text = page.extract_text() or ""
            if text.strip():
                page_text.append(f"[Page {index + 1}]\n{text}")

        content = "\n\n".join(page_text)
        return json.dumps({
            "url": final_url,
            "content_type": "application/pdf",
            "pages_read": min(len(reader.pages), 60),
            "text": content[:max_chars],
            "truncated": len(content) > max_chars,
        }, ensure_ascii=False, indent=2)

    if "html" not in content_type and not content_type.startswith("text/"):
        raise ValueError(f"Unsupported content type: {content_type or 'unknown'}")

    encoding = "utf-8"
    try:
        document = html.fromstring(data)
        document.make_links_absolute(final_url)

        title = " ".join(document.xpath("//title//text()")).strip()
        for element in document.xpath(
            "//script|//style|//noscript|//svg|//nav|//footer|"
            "//header|//form|//iframe|//button"
        ):
            element.drop_tree()

        main_nodes = document.xpath(
            "//main | //article | //*[@role='main']"
        )
        root = main_nodes[0] if main_nodes else document
        content = "\n".join(
            line.strip()
            for line in root.text_content().splitlines()
            if line.strip()
        )

        links = []
        seen = set()
        for element in root.xpath(".//a[@href]"):
            href = element.get("href", "").strip()
            label = " ".join(" ".join(element.itertext()).split())
            if (
                href.startswith(("http://", "https://"))
                and href not in seen
                and label
            ):
                seen.add(href)
                links.append({"text": label[:180], "url": href})
                if len(links) >= 40:
                    break

        images = []
        seen_images = set()
        for element in root.xpath(".//img[@src]"):
            src = element.get("src", "").strip()
            image_url = urljoin(final_url, src)
            alt = " ".join((element.get("alt") or "").split())
            if image_url.startswith(("http://", "https://")) and image_url not in seen_images:
                seen_images.add(image_url)
                images.append({"alt": alt[:180], "url": image_url})
                if len(images) >= 30:
                    break

        return json.dumps({
            "title": title,
            "url": final_url,
            "content_type": content_type,
            "text": content[:max_chars],
            "truncated": len(content) > max_chars,
            "links": links,
            "images": images,
        }, ensure_ascii=False, indent=2)

    except Exception as error:
        raise RuntimeError(f"Could not extract webpage content: {error}") from error
