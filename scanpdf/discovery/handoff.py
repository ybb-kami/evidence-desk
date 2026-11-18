"""Turn recognized publication addresses into explicit local discovery steps."""

from urllib.parse import unquote, urlencode, urlsplit

from . import fetch


def candidate(provider, identifier):
    """Build a discovery link using a supported, canonical publication identifier."""
    if provider == "arxiv" and fetch.valid_id(identifier):
        canonical, publisher = identifier, "https://arxiv.org/abs/" + identifier
    elif provider == "acl" and (canonical := fetch.acl_doi(identifier)):
        publisher = fetch.ACL_ENDPOINT + canonical.rsplit("/", 1)[1] + "/"
    elif provider == "joss" and (canonical := fetch.joss_doi(identifier)):
        publisher = "https://joss.theoj.org/papers/" + canonical
    else:
        return None
    params = {"provider": provider, "identifier": canonical}
    return {**params, "href": "/download?" + urlencode(params), "publisher_url": publisher}


def from_url(url):
    """Recognize identifiers only in exact supported HTTPS publication hosts and paths."""
    if (not isinstance(url, str) or len(url) > 2048 or not url.isprintable()
            or any(char.isspace() or char == "\\" for char in url)):
        return None
    try:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.username is not None or parsed.port not in (None, 443):
            return None
        path = unquote(parsed.path, errors="strict").removeprefix("/").removesuffix("/")
    except (UnicodeError, ValueError):
        return None
    if parsed.hostname == "doi.org":
        if path.lower().startswith("10.48550/arxiv."):
            return candidate("arxiv", path[len("10.48550/arxiv."):])
        return candidate("acl", path) or candidate("joss", path)
    if parsed.hostname == "joss.theoj.org" and path.startswith("papers/"):
        return candidate("joss", path.removeprefix("papers/"))
    if path.lower().endswith(".pdf"):
        path = path[:-4]
    if parsed.hostname == "aclanthology.org":
        return candidate("acl", "10.18653/v1/" + path)
    if parsed.hostname == "arxiv.org":
        section, _, identifier = path.partition("/")
        if section in ("abs", "pdf", "html"):
            return candidate("arxiv", identifier)
    return None
