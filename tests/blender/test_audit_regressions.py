"""Run with Blender --background --factory-startup --python this_file.

Uses repository code, a separate engine port, and analysis-only graphs:
no output is connected to speakers and no .blend or preferences are saved.
"""
import sys
import time
import tempfile
import unittest
import wave
import struct
import math
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import bpy
import blender_addon as addon
from blender_addon import command_client as cc, engine_manager as em, shared_memory_reader as shm
from blender_addon import permissions
from blender_addon.nodes.core import node_tree as nt


def wait_for(fn, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(.02)
    raise AssertionError('Timed out waiting for engine')


class BlenderLogic(unittest.TestCase):
    def setUp(self):
        self.tree = bpy.data.node_groups.new('AuditRegression', 'AudioNodeTree')
        self.data = bpy.context.scene.audio_nodes_data
        shm.reset_control_state()

    def tearDown(self):
        bpy.data.node_groups.remove(self.tree)
        self.data.channels.clear()

    def node(self, kind):
        return self.tree.nodes.new(kind)

    def test_gain_mute_is_silence_not_bypass(self):
        n = self.node('AudioGainNode')
        for muted in (True, False):
            n.mute = muted
            with patch.object(cc, 'is_node_registered', return_value=True), patch.object(cc, 'set_parameter') as send:
                shm._sync_mute_states()
                self.assertIn(unittest.mock.call(n.engine_node_id, 'mute', float(muted)), send.call_args_list)
                self.assertNotIn(unittest.mock.call(n.engine_node_id, 'bypass', 1.), send.call_args_list)

    def test_disconnect_restores_base_and_reconnect_resends(self):
        source = self.node('AudioMathNode'); source.input_a = 1
        gain = self.node('AudioGainNode')
        link = self.tree.links.new(source.outputs['Value'], gain.inputs['Gain'])
        with patch.object(em, 'is_engine_running', return_value=True), patch.object(cc, 'set_parameter') as send:
            shm._update_math_nodes(self.data)
            shm._update_control_connections(self.data, {})
            self.assertIn(unittest.mock.call(gain.engine_node_id, 'gain_db', 24.), send.call_args_list)
            self.tree.links.remove(link)
            shm._update_control_connections(self.data, {})
            self.assertEqual(send.call_args_list[-1], unittest.mock.call(gain.engine_node_id, 'gain_db', 0.))
            self.tree.links.new(source.outputs['Value'], gain.inputs['Gain'])
            shm._update_control_connections(self.data, {})
            self.assertEqual(send.call_args_list[-1], unittest.mock.call(gain.engine_node_id, 'gain_db', 24.))

    def test_direct_math_input_restored_after_disconnect(self):
        source = self.node('AudioMathNode'); source.input_a = 9
        dest = self.node('AudioMathNode'); dest.input_a = 3
        link = self.tree.links.new(source.outputs['Value'], dest.inputs['A'])
        with patch.object(em, 'is_engine_running', return_value=True), patch.object(cc, 'set_parameter') as send:
            shm._update_math_nodes(self.data); shm._update_control_connections(self.data, {})
            self.tree.links.remove(link); shm._update_control_connections(self.data, {})
            self.assertIn(unittest.mock.call(dest.engine_node_id, 'control_a', 3.), send.call_args_list)

    def test_duplicate_tap_names_use_socket_identity(self):
        fft = self.node('AudioFFTAnalyzerNode')
        a = fft.add_tap(440, name='Tap'); b = fft.add_tap(880, name='Tap')
        math_node = self.node('AudioMathNode')
        socket = fft.get_tap_socket(b.tap_id)
        for tap, val in ((a, .25), (b, .75)):
            ch = self.data.channels.add(); ch.name = f'{fft.engine_node_id}_tap_{tap.tap_id}'; ch.value = val
        self.tree.links.new(socket, math_node.inputs['A'])
        shm._update_math_nodes(self.data)
        self.assertEqual(self.data.channels[math_node.get_channel_name()].value, .75)

    def test_math_chain_evaluates_upstream_first_and_cycles_are_explicit(self):
        dest = self.node('AudioMathNode'); dest.operation = 'MUL'; dest.input_b = 2
        source = self.node('AudioMathNode'); source.input_a = 3
        self.tree.links.new(source.outputs['Value'], dest.inputs['A'])
        shm._update_math_nodes(self.data)
        self.assertEqual(self.data.channels[dest.get_channel_name()].value, 6.)
        self.tree.links.new(dest.outputs['Value'], source.inputs['A'])
        shm._update_math_nodes(self.data)
        self.assertIn(dest.engine_node_id, shm._control_errors)
        self.assertEqual(self.data.channels[dest.get_channel_name()].value, 0.)

    def test_blender_only_links_stay_out_of_engine(self):
        lfo = self.node('AudioLFONode'); gain = self.node('AudioGainNode')
        self.tree.links.new(lfo.outputs[0], gain.inputs['Gain'])
        self.assertEqual(nt._get_tree_connections(self.tree), set())

    def test_failed_connection_retries_current_source(self):
        old = self.node('AudioOscillatorNode')
        new = self.node('AudioOscillatorNode')
        dest = self.node('AudioGainNode')
        self.tree.links.new(new.outputs[0], dest.inputs['Input'])
        live = {old.engine_node_id, new.engine_node_id, dest.engine_node_id}
        error = {('connect', dest.engine_node_id, 'input'): 'timeout'}
        with patch.object(em, 'is_engine_running', return_value=True), \
             patch.object(cc, 'registered_node_ids', return_value=live), \
             patch.object(cc, 'is_node_registered', return_value=True), \
             patch.object(cc, 'get_command_errors', return_value=error), \
             patch.object(cc, 'connect_nodes') as connect, \
             patch.object(cc, 'disconnect_nodes'):
            nt.ensure_engine_sync()
            connect.assert_any_call(new.engine_node_id, dest.engine_node_id, 'output', 'input')
            self.assertFalse(any(c.args[0] == old.engine_node_id for c in connect.call_args_list))

    def test_sample_property_callback_never_waits_for_engine(self):
        sampler = self.node('AudioSamplerNode')
        with patch.object(em, 'is_engine_running', return_value=True), \
             patch.object(cc, 'queue_command') as queue, \
             patch.object(cc, 'send_command', side_effect=AssertionError('blocking UI')), \
             patch.object(cc, 'flush_queue', side_effect=AssertionError('blocking UI')):
            sampler.sample_path = '/missing-audit.wav'
            self.assertEqual(queue.call_args.args[0]['type'], 'load_sample')
            self.assertEqual(cc.get_sample_load_state(sampler.engine_node_id)['status'], 'Loading')


class ClientLogic(unittest.TestCase):
    def setUp(self):
        cc.clear_registered_nodes()

    def tearDown(self):
        cc.clear_registered_nodes()

    def test_engine_error_response_not_success(self):
        sock = unittest.mock.Mock()
        sock.recv_string.return_value = '{"status":"error","message":"missing node"}'
        _, response = cc._send_with_retries(sock, None, {'type':'connect'})
        self.assertEqual(response['status'], 'error')

    def test_create_pending_until_ack_and_failure_can_retry(self):
        with patch.object(cc, 'queue_command') as queue:
            cc.create_node('gain_test', 'gain')
            self.assertFalse(cc.is_node_registered('gain_test'))
            self.assertTrue(cc.is_node_pending('gain_test'))
            cmd = queue.call_args.args[0]
            cc._record_response(cmd, {'status':'error','message':'full'})
            self.assertFalse(cc.is_node_registered('gain_test'))
            self.assertFalse(cc.is_node_pending('gain_test'))
            cc.create_node('gain_test', 'gain')
            self.assertEqual(queue.call_args_list[-2].args[0]['type'], 'delete_node')
            cc._record_response(cmd, {'status':'ok'})
            self.assertTrue(cc.is_node_registered('gain_test'))
            self.assertEqual(cc.get_command_errors(), {})

    def test_async_sample_latest_request_wins(self):
        with patch.object(cc, 'queue_command') as queue, patch.object(cc, 'flush_queue', side_effect=AssertionError('UI blocked')), patch.object(cc, 'send_command', side_effect=AssertionError('UI blocked')):
            cc.load_sample('sampler_test', 'old.wav'); old = queue.call_args.args[0]
            cc.load_sample('sampler_test', 'new.wav'); new = queue.call_args.args[0]
            cc._record_response(old, {'status':'ok'})
            self.assertEqual(cc.get_sample_load_state('sampler_test')['status'], 'Loading')
            cc._record_response(new, {'status':'error'})
            state = cc.get_sample_load_state('sampler_test')
            self.assertEqual(state['status'], 'Error')
            self.assertEqual(state['path'], 'new.wav')
            self.assertTrue(state['message'])
            cc.delete_node('sampler_test')
            cc._record_response(new, {'status':'ok'})
            self.assertEqual(cc.get_sample_load_state('sampler_test'), {})


class EngineRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port_patch = patch.object(cc, 'get_port', return_value=15556); cls.port_patch.start()
        cls.engine_port_patch = patch.object(em, 'get_zmq_port', return_value=15556); cls.engine_port_patch.start()
        if not em.start_engine():
            raise AssertionError('Engine did not start')

    @classmethod
    def tearDownClass(cls):
        em.stop_engine(); cc.disconnect(); cls.port_patch.stop(); cls.engine_port_patch.stop()

    def setUp(self):
        shm.reset_control_state()
        self.tree = bpy.data.node_groups.new('EngineRegression', 'AudioNodeTree')

    def tearDown(self):
        bpy.data.node_groups.remove(self.tree)
        cc.flush_queue(); time.sleep(.05)

    def test_live_gain_mute_and_disconnect(self):
        osc = self.tree.nodes.new('AudioOscillatorNode')
        gain = self.tree.nodes.new('AudioGainNode')
        fft = self.tree.nodes.new('AudioFFTAnalyzerNode')
        math_node = self.tree.nodes.new('AudioMathNode'); math_node.input_a = 1
        self.tree.links.new(osc.outputs[0], gain.inputs['Input'])
        self.tree.links.new(gain.outputs[0], fft.inputs[0])
        nt._do_sync_connections(); cc.flush_queue(); time.sleep(.15)
        def rms():
            d = shm.read_visualization_data()
            return (d or {}).get('fft_nodes', {}).get(fft.engine_node_id, {}).get('rms_left', 0.)
        baseline = wait_for(lambda: rms() if rms() > .05 else None)
        gain.mute = True; shm._sync_mute_states()
        wait_for(lambda: rms() < .0001)
        gain.mute = False; shm._sync_mute_states()
        wait_for(lambda: rms() > baseline * .8)
        link = self.tree.links.new(math_node.outputs['Value'], gain.inputs['Gain'])
        shm._update_math_nodes(bpy.context.scene.audio_nodes_data)
        shm._update_control_connections(bpy.context.scene.audio_nodes_data, {})
        wait_for(lambda: rms() > baseline * 10)
        self.tree.links.remove(link); shm._update_control_connections(bpy.context.scene.audio_nodes_data, {})
        wait_for(lambda: baseline * .7 < rms() < baseline * 1.3)
        self.assertEqual(gain.gain_db, 0.)

    def test_live_sample_error_success_and_worker_stays_responsive(self):
        sampler = self.tree.nodes.new('AudioSamplerNode')
        start = time.perf_counter()
        sampler.sample_path = '/__audit_nonexistent__.wav'
        self.assertLess(time.perf_counter() - start, .1)
        wait_for(lambda: cc.get_sample_load_state(sampler.engine_node_id).get('status') == 'Error')
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp)/'tone.wav'
            with wave.open(str(wav), 'wb') as out:
                out.setnchannels(1); out.setsampwidth(2); out.setframerate(44100)
                out.writeframes(b''.join(struct.pack('<h', int(1000*math.sin(i*.1))) for i in range(4410)))
            sampler.sample_path = str(wav)
            wait_for(lambda: cc.get_sample_load_state(sampler.engine_node_id).get('status') == 'Loaded')
        self.assertTrue(cc.ping())
        self.assertTrue(cc.is_node_registered(sampler.engine_node_id))


if __name__ == '__main__':
    with patch.object(permissions, 'request_audio_permission'):
        addon.register()
    try:
        suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(cls)
                                  for cls in (BlenderLogic, ClientLogic, EngineRegression))
        outcome = unittest.TextTestRunner(verbosity=2).run(suite)
    finally:
        addon.unregister()
    if not outcome.wasSuccessful():
        raise RuntimeError('Audio Nodes regression tests failed')
