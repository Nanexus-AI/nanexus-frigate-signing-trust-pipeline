"""Private post-admission video-scope recognition; never a native verifier.

Only use on the original entry immediately after Bundle.add_file. Admission and
native custody establish execution provenance; these scoped reports are NOT a
standalone transcript from which to replay or recreate cryptographic validity.
"""
import base64
import json
from collections import Counter
from . import contracts as a, wire
from .composition import canonical
from .continuity_protocol import pairs, sha
from .continuity_updates import decode, token_text
from .rolling_product import CONFIGURATION


class EvidenceError(ValueError):
    """Contradictory/malformed admitted evidence: stop, never downgrade/fallback."""


def need(value, reason):
    if not value:
        raise EvidenceError('video-signing: ' + reason)


def loads(raw):
    try:
        return json.loads(raw, object_pairs_hook=pairs)
    except (ValueError, RecursionError, UnicodeError) as exc:
        raise EvidenceError('video-signing: malformed object') from exc


def recognize(entry, row, *, owner=None, camera=None, request_id=None):
    """GREEN/AMBER/RED for pinned product evidence, None for other profiles.

    Does not accept a browser-provided evidence object or authorize a principal.
    Source admission and Bundle/owner authorization remain mandatory callers.
    """
    if entry.get('result', {}).get('page', {}).get('record', {}).get('engine_profile') != 'candidate-rolling-context-experimental-v1':
        return None
    try:
        return _recognize(entry, row, owner, camera, request_id)
    except EvidenceError:
        raise
    except (KeyError, TypeError, ValueError, IndexError, AttributeError, RecursionError) as exc:
        raise EvidenceError('video-signing: malformed or incomplete binding') from exc


def _recognize(entry, row, owner, camera, request_id):
    result = entry['result']
    page_raw = canonical(result['page'])
    need(len(page_raw) == result['size'] and sha(page_raw) == result['sha256'], 'page digest')
    page = wire.loads(page_raw)
    objects = {}
    total = 0
    need(type(result['objects']) is list and len(result['objects']) <= 128, 'object count bound')
    for obj in result['objects']:
        need(obj['encoding'] == 'base64' and obj['id'] not in objects, 'object identity')
        need(type(obj['data']) is str and len(obj['data']) <= ((4 * 1024 * 1024 + 2)//3)*4, 'object bound')
        raw = base64.b64decode(obj['data'], validate=True)
        total += len(raw)
        need(total <= 4 << 20 and len(raw) <= 1 << 20, 'evidence bound')
        need(len(raw) == obj['size'] and sha(raw) == obj['sha256'], 'object digest')
        objects[obj['id']] = raw
    need({r.evidence_id:(r.sha256,r.size) for r in page.evidence} ==
         {key:(sha(raw),len(raw)) for key,raw in objects.items()}, 'evidence set')

    def one(prefix):
        values = [loads(raw) for key,raw in objects.items() if key.startswith(prefix+'-')]
        need(len(values) == 1, prefix+' object')
        return values[0]

    source = one('sources')
    candidate = source.get('candidate', {})
    if candidate.get('schema') != 'candidate-rolling-product-file/1':
        return None
    need(set(candidate) == {'schema','selection_sha256','request_sha256','owner_sha256','policy'}, 'product scope')
    need(candidate['request_sha256'] == sha(wire.dumps(page.request)) and
         candidate['selection_sha256'] == source['admitted_proof_sha256'], 'request selection')
    need(len(candidate['owner_sha256']) == 64 and all(c in '0123456789abcdef' for c in candidate['owner_sha256']), 'admitted owner digest')
    bound = owner is not None and camera is not None and request_id is not None
    if bound:
        need(candidate['owner_sha256'] == sha(owner.encode()) and page.request.camera == camera and page.request.request_id == request_id, 'authorized request owner camera')
    need(source['run'] == entry['execution']['run'], 'run binding')
    trust = one('trust');tail = one('tail')
    need(trust['configuration'] == CONFIGURATION, 'unsupported native configuration')
    need(type(trust['sha256']) is str and len(trust['sha256']) == 64, 'trust identity')
    identity = sha(canonical([source['admitted_proof_sha256'],trust['sha256'],CONFIGURATION]))
    need(page.job_id == 'rolling-'+identity and source['run'] == 'run-'+sha(canonical([identity,entry['execution']['attempt']])), 'execution identity')
    files = source['files']
    need(0 < len(files) <= 182, 'source bound')
    need(len({f['original']['item_id'] for f in files}) == len(files), 'source duplicates')
    owned = [f for f in files if f['original']['item_id'] == entry['recording']]
    need(len(owned) == 1, 'owned source')
    f = owned[0];original = f['original'];receipt = f['receipt']
    need(entry['recording'] == row['recording'] and entry['generation'] == row['generation'] == original['generation'], 'recording generation')
    need(result['original_file_sha256'] == original['sha256'] == receipt['sha256'], 'original hash')
    need(receipt['item_id'] == original['item_id'] and receipt['generation'] == original['generation'], 'receipt')
    need(f['metadata'] == entry['metadata_interval'] == row['metadata_interval'] and f['committed'], 'source commit')
    start,end = f['start'],f['end']
    need(type(start) is int and type(end) is int and 0 <= start < end <= 32768 and end-start == original['nal_count'] <= 1024, 'owned range')
    facts = source['occurrences']
    need(facts == result['source_ledger'] and len(facts) == end-start, 'occurrence accounting')
    prefix = sha((original['item_id']+':'+original['generation']+':'+original['sha256']).encode())
    tokens = []
    for fact in facts:
        token = f"{prefix}:p{fact['packet']}:n{fact['nal']}:o{fact['payload_offset']}:l{fact['payload_bytes']}"
        need(fact['token'] == token, 'source occurrence token');tokens.append(token)
    positions = [(fact['packet'],fact['nal']) for fact in facts]
    need(positions == sorted(positions) and len(set(positions)) == len(positions) and len(set(tokens)) == len(tokens), 'occurrence order')
    token_ordinals = {token:start+n for n,token in enumerate(tokens)}
    observations = list(page.observations)
    if page.next_cursor:
        continuation = loads(objects[page.next_cursor])
        need(continuation['run'] == source['run'] and continuation['offset'] == 128, 'continuation scope')
        observations.extend(wire.loads(canonical(o)) for o in continuation['observations'])
    need(len(observations) <= len(tokens) and len({o.observation_id for o in observations}) == len(observations), 'observation accounting')
    counts = dict(Counter(o.state.value for o in observations))
    need(counts == row['finalized_counts'], 'count binding')
    unresolved = tail['unresolved_tokens'];unprocessed = tail['unprocessed_tokens']
    need(len(unresolved) == row['unresolved'] and len(unprocessed) == row['unprocessed'] and bool(tail['missing']) == row['missing_evidence'], 'tail binding')
    need(set(unprocessed) <= set(unresolved), 'unprocessed accounting')
    need(len(set(unresolved)) == len(unresolved) and set(unresolved) <= set(tokens) and set(unprocessed) <= set(tokens), 'tail tokens')
    reports = {}
    for key,raw in objects.items():
        if not key.startswith('updates-'):continue
        updates = loads(raw);need(type(updates) is list and len(updates) <= 4096, 'update bound')
        for update in updates:
            u = decode(canonical(update),32768)
            need((u['run_id'],u['attempt'],u['trust'],u['configuration']) ==
                 (source['run'],entry['execution']['attempt'],trust['sha256'],CONFIGURATION), 'native binding')
            need(not any(i['axes']['key_changed'] for i in u['items']), 'unsupported key change')
            if u['fresh_report']:
                need(u['phase'] == 'feed' and not u['error'] and not u['stop'], 'report outcome')
                need(u['report_generation'] > 0 and u['generating_feed_ordinal'] == u['consumed']-1, 'report generation feed')
                g = u['report_generation'];need(g not in reports or reports[g] == u, 'conflicting report')
                reports[g] = u
    seen = set();positive = 0;qualified = True;negative = False
    needed_reports = set()
    report_items = {g:{i['received_ordinal']:i for i in u['items']} for g,u in reports.items()}
    need(all(len(report_items[g]) == len(u['items']) for g,u in reports.items()), 'duplicate native occurrence')
    qualified &= bound
    qualified &= candidate['policy'] == 'catalog-start-id-packet-nal/1'
    for observation in observations:
        report = observation.report;need(report is not None, 'observation report')
        need(observation.run_id == source['run'] and observation.finality is a.Finality.FINALIZED, 'observation finality')
        context = {key for key in objects if key != page.next_cursor}
        need(set(observation.evidence_refs) == context and set(observation.context_refs) == context, 'observation evidence')
        token = observation.support.sample_ref
        need(token in tokens and token not in seen and report.received_refs == (token,), 'observation source')
        seen.add(token);ordinal = token_ordinals[token]
        need(str(int(report.generation)) == report.generation, 'canonical report generation')
        u = reports.get(int(report.generation));need(u is not None, 'missing generating report')
        need(report.generating_feed_ordinal == u['generating_feed_ordinal'] and report.retired_prefix_ordinal == u['retired_prefix_ordinal'], 'report reference')
        i = report_items[int(report.generation)].get(ordinal)
        need(i is not None, 'native occurrence')
        need(token_text(i['source_token_hex']) == token and i['revision'] == report.revision and i['report_generation'] == int(report.generation), 'native revision token')
        need(observation.observation_id == 'obs-'+sha(canonical([source['run'],ordinal,i['revision']])), 'observation revision identity')
        need(i['finalized'] and u['retired_prefix_ordinal'] is not None and ordinal <= u['retired_prefix_ordinal'], 'native retirement')
        need(observation.state.value == ('VERIFIED_SIGNING','INTEGRITY_NEGATIVE','NO_RECOGNIZED_SIGNING_EVIDENCE','INDETERMINATE')[i['state']], 'native state preservation')
        producers = [g for g in files if g['start'] <= u['generating_feed_ordinal'] < g['end']]
        need(len(producers) == 1 and producers[0]['committed'], 'report producer receipt')
        needed_reports.add(int(report.generation))
        axes = i['axes'];ok = (axes['authenticity'],axes['provenance'],axes['combined'],axes['key_changed']) == (4,3,2,False)
        if i['nalu_type'] in 'IiPp':
            if i['state'] == 1:
                need(i['raw_status'] == 'N' and axes['authenticity'] == 2, 'native negative');negative = True
            elif i['state'] == 0:
                need(i['raw_status'] == '.' and observation.context_quality is a.Quality.SATISFIED and observation.trust_profile == 'public-ca-'+trust['sha256'], 'positive trust')
                need(axes['authenticity'] in (3,4) and axes['provenance'] == 3 and axes['combined'] in (1,2), 'contradictory positive axes')
                positive += 1;qualified &= ok
            else:qualified = False
        else:
            qualified &= (i['state'] == 3 and (i['nalu_type'],i['raw_status']) in (('S','.'),('v','_')) and ok)
    need(seen.isdisjoint(unresolved) and seen | set(unresolved) == set(tokens), 'complete owned accounting')
    for g in needed_reports:
        u = reports[g]
        # The report's already retired controls are supporting context. Its later
        # provisional suffix does not invalidate earlier retired owned video.
        for i in u['items']:
            if i['finalized'] and i['nalu_type'] not in 'IiPp':
                supports = [g for g in files if g['start'] <= i['received_ordinal'] < g['end']]
                need(len(supports) == 1 and supports[0]['committed'], 'supporting control source')
                supporting = supports[0];o = supporting['original'];receipt = supporting['receipt']
                need((receipt['item_id'],receipt['generation'],receipt['sha256']) ==
                     (o['item_id'],o['generation'],o['sha256']), 'supporting control receipt')
                prefix = sha((o['item_id']+':'+o['generation']+':'+o['sha256']).encode())
                need(token_text(i['source_token_hex']).startswith(prefix+':p'), 'supporting control occurrence')
                need(i['report_generation'] == g and u['retired_prefix_ordinal'] is not None and
                     i['received_ordinal'] <= u['retired_prefix_ordinal'], 'supporting control report')
                axes = i['axes']
                qualified &= (i['state'] == 3 and (i['nalu_type'],i['raw_status']) in (('S','.'),('v','_')) and
                              (axes['authenticity'],axes['provenance'],axes['combined'],axes['key_changed']) == (4,3,2,False))
        qualified &= not u['missing']
    complete = page.lifecycle is a.Lifecycle.COMPLETE and page.output_complete
    state = entry['execution']['state']
    need((complete and state == 'ROLLING_RETIRED') or
         (not complete and page.lifecycle is a.Lifecycle.INCOMPLETE and state == 'CONTEXT_ENDED_INCOMPLETE'), 'publication finality')
    if negative:return 'RED'
    if not complete or unresolved or unprocessed or tail['missing']:return 'AMBER'
    return 'GREEN' if positive and qualified else 'AMBER'
