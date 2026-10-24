"""Camera-centric resolution and guarded observation, independent of display names."""
import secrets
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from nanexus_trust.companion import CompanionService, LiveScheduler, PlaybackMapping
from nanexus_trust.companion.cameras import CanonicalSelector, resolve_camera
from nanexus_trust.companion.transport import LiveTransport
from nanexus_trust.contracts import CameraId, RenditionId
from nanexus_trust.live import Binding


def mapping(stream='primary', context='single', revision='v1'):
    return PlaybackMapping('front', stream, context,
        Binding(CameraId('logical-front'), RenditionId('main' if stream == 'primary' else 'alternate'),
                stream, revision, None))


def selector(m):
    return CanonicalSelector(m.frigate_camera, m.stream, m.context, m.binding.identity)


class CameraTests(unittest.TestCase):
    def test_zero_one_many(self):
        a, b = mapping(), mapping('alternate')
        self.assertEqual(resolve_camera('front', ())[0]['coverage_state'], 'NOT_CONFIGURED')
        meta, chosen = resolve_camera('front', (a,))
        self.assertEqual(meta['coverage_state'], 'CONFIGURED'); self.assertIs(chosen, a)
        self.assertEqual(resolve_camera('front', (a,b))[0]['coverage_state'], 'CONFIGURATION_AMBIGUOUS')
        meta, chosen = resolve_camera('front', (a,b), (selector(b),))
        self.assertEqual(meta['stream'], 'alternate'); self.assertIs(chosen, b)

    def test_invalid_stale_duplicate_fail_closed(self):
        a, b = mapping(), mapping('alternate')
        for choices in ((selector(mapping(revision='stale')),), (selector(a),selector(a)),
                        (CanonicalSelector('front','missing','single',a.binding.identity),)):
            self.assertEqual(resolve_camera('front',(a,b),choices)[0]['coverage_state'], 'CONFIGURATION_AMBIGUOUS')
        self.assertIsNone(resolve_camera('front',(a,a),(selector(a),))[1])
        conflict = PlaybackMapping('other','other','single',Binding(a.binding.camera,a.binding.rendition,'different','v2',None))
        self.assertIsNone(resolve_camera('front',(a,conflict))[1])

    def test_camera_id_not_display_and_metadata_never_observes(self):
        a = mapping(); factory = Mock(side_effect=AssertionError('metadata must not open media'))
        live = LiveScheduler((a,),factory)
        service = CompanionService(live,SimpleNamespace(),('logical-front',))
        before = service.camera_bindings()
        display_name = 'New presentation name'
        self.assertEqual(before, service.camera_bindings()); self.assertNotIn(display_name,str(before)); factory.assert_not_called()
        self.assertEqual(resolve_camera('renamed-machine-id',(a,))[0]['coverage_state'],'NOT_CONFIGURED')
        live.close()

    def test_no_runtime_for_ambiguous_or_mismatch(self):
        a,b = mapping(),mapping('alternate'); live=LiveScheduler((a,b),Mock())
        service=CompanionService(live,SimpleNamespace(),('logical-front',))
        live.query=Mock(side_effect=AssertionError('must not query'))
        self.assertEqual(service.camera_status('front','0'*64)['result']['status'],'UNKNOWN')
        service.canonical_selectors=(selector(a),)
        self.assertEqual(service.camera_status('front','0'*64)['result']['status'],'UNKNOWN')
        live.query.assert_not_called();live.close()

    def test_exact_selected_tuple_and_runtime_identity(self):
        a,b = mapping(),mapping('alternate'); live=LiveScheduler((a,b),Mock())
        service=CompanionService(live,SimpleNamespace(),('logical-front',),canonical_selectors=(selector(b),))
        guard=service.camera_bindings()['cameras'][0]['binding_guard']
        runtime = next(x for x in live._bindings() if x.rendition == b.binding.rendition)
        result={'status':'ACTIVE','reason':'recent_signing_evidence','evidence_age_ms':'1.4',
                'observed_at':'2026-10-17T00:00:00Z','last_evidence_at':'2026-10-17T00:00:00Z',
                'session_id':'session','mapping_id':runtime.identity,'camera':'logical-front','rendition':'alternate'}
        live.query=Mock(return_value=result)
        self.assertEqual(service.camera_status('front',guard)['result']['status'],'ACTIVE')
        live.query.assert_called_once_with('front','alternate','single')
        result['mapping_id']='wrong'
        self.assertEqual(service.camera_status('front',guard)['result']['status'],'UNKNOWN')
        live.close()

    def test_private_transport_auth_and_methods(self):
        service=Mock();service.camera_bindings.return_value={'cameras':[]}
        token=secrets.token_urlsafe(32); t=LiveTransport(service,token)
        self.assertEqual(t.dispatch('GET','/v1/cameras/bindings',None)[0],401)
        service.camera_bindings.assert_not_called()
        self.assertEqual(t.dispatch('POST','/v1/cameras/bindings','Bearer '+token)[0],405)
        self.assertEqual(t.dispatch('GET','/v1/cameras/bindings','Bearer '+token),(200,{'cameras':[]}))
        for q in ('camera=front','camera=front&guard=a&guard=b','camera=front&guard=x&stream=main'):
            self.assertEqual(t.dispatch('GET','/v1/cameras/status?'+q,'Bearer '+token)[0],400)
        service.camera_status.assert_not_called()

if __name__ == '__main__': unittest.main()
