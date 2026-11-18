"""Build both bridges against one existing ledger. Construction never sends or provisions."""
from pathlib import Path

from candidate.gateway.synthesis_bridge import SynthesisBridge
from discovery_tickets import Tickets
from group_budget import Denied, GroupBudget
from group_dispatch import ApprovedGroupCaller, tls_transport
from official_input_bound import _load, submitted_count


def existing_ledger(config):
    fields = {'path', 'approval_sha', 'price_sha', 'verified_epoch', 'expiry_epoch', 'seat_groups'}
    if not isinstance(config, dict) or set(config) not in (fields, fields | {'cap_policy'}):
        raise Denied('operator metadata')
    if not Path(config['path']).is_absolute():
        raise Denied('an absolute persistent ledger path is required')
    _load()
    ledger = GroupBudget(**config, input_bound=submitted_count)
    ledger._time()
    with ledger._connect() as db:
        ledger._check_cap_marker(db)
        if db.execute('SELECT hash FROM conf').fetchall() != [(ledger.config,)]:
            raise Denied('ledger mismatch; no automatic migration/provision')
    return ledger


def _bridges(ledger, transport, mode=None):
    discovery = Tickets.grouped(ApprovedGroupCaller(ledger, transport))
    synthesis = SynthesisBridge.grouped(discovery)
    synthesis.provider_mode = mode
    discovery.provider_mode = mode
    return dict(discovery_bridge=discovery, synthesis_bridge=synthesis, model_bridge=None)


def runtime(config, key):
    """Original standalone factory. The four-seat entry uses private_runtime."""
    return _bridges(existing_ledger(config), tls_transport(key))


def private_runtime(config, *, socket_path, broker_uid, provider_mode='offline_fixture'):
    """No credentials or internet client are supplied to the gateway or its workers."""
    from private_transport import UnixProviderTransport
    if provider_mode not in ('offline_fixture', 'approved_unix'):
        raise Denied('unknown private transport')
    if provider_mode == 'approved_unix' and not config.get('cap_policy'):
        raise Denied('explicit CNY budget required for this entry')
    ledger = existing_ledger(config)
    if provider_mode == 'approved_unix':
        from legacy_ledger import require_migrated_policy
        with ledger._connect() as db:
            require_migrated_policy(ledger, db)
    transport = UnixProviderTransport(socket_path, broker_uid=broker_uid, provider_mode=provider_mode)
    return _bridges(ledger, transport, provider_mode)
