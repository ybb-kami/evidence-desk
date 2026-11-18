"""External-broker sender for the approved model and discovery routes.

Nothing in the judge launcher imports this module. Constructing a sender does not
contact the provider. Metadata and paid POSTs use fixed official HTTPS paths.
"""
import http.client
import json
import re
import ssl

from group_budget import Denied, ENDPOINT, MODEL
from group_dispatch import tls_transport


def _read_catalog(key):
    connection = http.client.HTTPSConnection('api.deepseek.com', 443, timeout=12,
                                             context=ssl.create_default_context())
    try:
        connection.request('GET', '/models', headers={'x-api-key': key, 'Accept': 'application/json'})
        response = connection.getresponse()
        if response.status != 200:
            raise Denied('model catalog unavailable')
        raw = response.read(65537)
        if len(raw) > 65536:
            raise Denied('oversized model catalog')
        return json.loads(raw)
    finally:
        connection.close()


def _require_flash(catalog):
    try:
        entries = catalog['data']
        if not isinstance(entries, list) or 'error' in catalog:
            raise ValueError
        entry, = [item for item in entries if isinstance(item, dict) and item.get('id') == MODEL]
        if entry['name'] != 'DeepSeek-V4.1-Flash':
            raise ValueError
        raw = entry['client_metadata']['deepseek-harness']['badge']['text']
        labels = [value for k, value in raw.items() if re.fullmatch(r'en(?:[-_][a-z]{2})?', k, re.I)] if isinstance(raw, dict) else [raw]
        if not labels or not all(isinstance(label, str) and label.strip() == '1017' for label in labels):
            raise ValueError
    except (TypeError, ValueError, KeyError, AttributeError):
        raise Denied('Flash release could not be verified') from None


def approved_sender(key, *, catalog=_read_catalog, post_factory=tls_transport):
    """Inject into an explicitly approved UnixProviderBroker outside the private namespace."""
    post = post_factory(key)

    def send(endpoint, wire):
        if endpoint not in ENDPOINT.values() or not isinstance(wire, bytes) or key.encode() in wire:
            raise Denied('invalid outbound request')
        if endpoint == ENDPOINT['model']:
            _require_flash(catalog(key))
        return post(endpoint, wire)

    return send
